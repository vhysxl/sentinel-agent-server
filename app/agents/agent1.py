import os
import sys
import json
# pyrefly: ignore [missing-import]
from openai import OpenAI
from dotenv import load_dotenv

# Fix Windows terminal UnicodeEncodeError (emoji printing)
sys.stdout.reconfigure(encoding='utf-8')

# Import tools yang sudah kita buat
from app.tools.financial import calculate_z_score, get_sales_trend, get_transaction_details

load_dotenv()

# Define tools mapping so we can call them later
tool_functions = {
    "get_transaction_details": get_transaction_details,
    "calculate_z_score": calculate_z_score,
    "get_sales_trend": get_sales_trend
}

# The OpenAI API expects explicit tool definitions
tools = [
    {
        'type': 'function',
        'function': {
            'name': 'get_transaction_details',
            'description': 'Mendapatkan informasi dasar sebuah transaksi (termasuk departemen pembuatnya). Gunakan ini PERTAMA KALI sebelum menggunakan tools lain!',
            'parameters': {
                'type': 'object',
                'properties': {'transaction_id': {'type': 'integer'}},
                'required': ['transaction_id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'calculate_z_score',
            'description': 'Menghitung Z-score dari sebuah transaksi berdasarkan histori pengeluaran vendor/kategori.',
            'parameters': {
                'type': 'object',
                'properties': {'transaction_id': {'type': 'integer'}},
                'required': ['transaction_id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_sales_trend',
            'description': 'Mendapatkan tren penjualan atau KPI departemen pada bulan tertentu.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'department': {'type': 'string'},
                    'month': {'type': 'string'}
                },
                'required': ['department', 'month']
            }
        }
    }
]

def run_financial_investigator(transaction_id: int):
    """
    Menjalankan Agent 1 (Financial Analytics Investigator) 
    untuk menginvestigasi sebuah transaksi menggunakan DeepSeek via NVIDIA API.
    """
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=os.environ.get("NVIDIA_API_KEY")
    )
    
    prompt = f"""
    Kamu adalah Agent 1: Financial Analytics Investigator.
    Tugasmu adalah menganalisis transaksi keuangan dengan ID {transaction_id}.
    Gunakan tools yang tersedia untuk mengumpulkan 'Objective Findings'.

    Langkah-langkah yang HARUS kamu lakukan:
    1. Cek nilai Z-Score untuk mendeteksi anomali (calculate_z_score).
    2. Cek tren penjualan departemen untuk memvalidasi alasan pengeluaran (get_sales_trend).

    Setelah mengumpulkan bukti, berikan laporan objektif atas transaksi ini HANYA DALAM FORMAT JSON.
    Format JSON yang DIBUTUHKAN:
    {{
      "finding": "Ringkasan singkat temuan (contoh: Extreme Z-Score Anomaly)",
      "provenance": {{
          "generated_by": "Agent_1_Financial_Analytics",
          "tools_used": ["calculate_z_score", "get_sales_trend"]
      }},
      "evidence": {{
          "objective": [
              {{"metric": "z_score", "value": 70.58, "status": "anomali ekstrim"}}
          ]
      }}
    }}
    Jangan berikan keputusan final atau teks penjelasan di luar JSON. Kembalikan HANYA format JSON yang valid agar bisa diproses oleh Python Scoring Engine.
    """

    print(f"\n[Agent 1] Memulai investigasi untuk Transaction ID: {transaction_id}...\n")
    
    messages = [{"role": "user", "content": prompt}]

    # Kita gunakan loop agar model bisa memanggil tool berkali-kali secara berurutan
    # jika dia memilih untuk tidak memanggil semuanya sekaligus.
    while True:
        response = client.chat.completions.create(
            model="deepseek-ai/deepseek-v4-pro",
            messages=messages,
            tools=tools,
            temperature=0.2, # Ubah sedikit menjadi 0.2 agar outputnya objektif dan konsisten
            top_p=0.95,
            max_tokens=4096,
            extra_body={"chat_template_kwargs":{"thinking":False}},
            stream=False
        )
        
        message = response.choices[0].message
        messages.append(message)

        # Jika model tidak memanggil tool lagi, berarti dia memberikan respons final
        if not message.tool_calls:
            print("\n[Agent 1] Selesai Investigasi. Laporan Akhir:")
            print(message.content)
            return response
            
        print("\n[Agent 1] Model memanggil tools...")
        for tool_call in message.tool_calls:
            function_name = tool_call.function.name
            
            # Load argumen yang diberikan oleh LLM
            kwargs = json.loads(tool_call.function.arguments)
            print(f"  -> Memanggil {function_name} dengan argumen: {kwargs}")
            
            # Eksekusi fungsi Python sesungguhnya
            if function_name in tool_functions:
                result = tool_functions[function_name](**kwargs)
            else:
                result = {"error": f"Tool {function_name} not found"}
                
            # Tambahkan hasil dari tool kembali ke messages list
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result)
            })
            
        print("[Agent 1] Menganalisis hasil dari tools untuk langkah selanjutnya...")

