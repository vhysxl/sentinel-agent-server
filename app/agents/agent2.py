import os
import sys
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Fix Windows terminal UnicodeEncodeError (emoji printing)
sys.stdout.reconfigure(encoding='utf-8')

# Import tools
from app.tools.financial import get_transaction_details
from app.tools.fraud import find_duplicate_expenses, get_vendor_history

def run_fraud_investigator(transaction_id: int):
    """
    Menjalankan Agent 2 (Fraud Pattern Investigator) 
    untuk mendeteksi pola penipuan (split payment, duplikasi, vendor fiktif)
    menggunakan Gemini API secara Interaktif (Tool Calling).
    """
    load_dotenv(override=True)
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY")
    )
    
    print(f"\n[Agent 2] Memulai investigasi Fraud (Tool Calling Loop) untuk Transaction ID: {transaction_id}...")
    
    # 1. BENTUK PROMPT SISTEM/USER
    prompt = f"""
Kamu adalah Agent 2: Fraud Pattern Investigator.
Tugasmu adalah menginvestigasi transaksi dengan ID: {transaction_id} untuk mencari indikasi penipuan.

Langkah-langkah:
1. Panggil `get_transaction_details` menggunakan transaction_id untuk mendapatkan amount, vendor_id, dan transaction_date.
2. Gunakan `find_duplicate_expenses` menggunakan data dari langkah 1 (amount, vendor_id, date) untuk mengecek kemungkinan transaksi ganda (split payment).
3. Gunakan `get_vendor_history` menggunakan vendor_id untuk memeriksa apakah vendor ini valid atau fiktif.

Setelah kamu memiliki semua data objektif yang diperlukan, berikan laporan akhir HANYA DALAM FORMAT JSON yang valid tanpa markdown apapun.
Format JSON yang DIBUTUHKAN:
{{
  "finding": "Ringkasan komprehensif temuan terkait pola penipuan (duplikasi, status vendor).",
  "provenance": {{
      "generated_by": "Agent_2_Fraud_Investigator",
      "tools_used": ["daftar_tools_yang_kamu_panggil"]
  }},
  "evidence": {{
      "objective": [
          {{"metric": "duplicate_transactions", "value": "2 ditemukan", "status": "indikasi split payment"}},
          {{"metric": "vendor_status", "value": "new_vendor", "status": "mencurigakan"}}
      ]
  }}
}}
Ingat: kembalikan HANYA format JSON di output akhirmu!
"""

    # 2. DEFINISIKAN TOOLS & KONFIGURASI
    tools_list = [
        get_transaction_details,
        find_duplicate_expenses,
        get_vendor_history
    ]
    
    config = types.GenerateContentConfig(
        tools=tools_list,
        temperature=0.2,
        top_p=0.95,
        max_output_tokens=4096,
    )

    # 3. PANGGIL LLM (CHAT SESSION) DENGAN MULTI-FALLBACK
    models_to_try = [
        'models/gemini-3.5-flash',
        'models/gemini-3.6-flash',
        'models/gemini-3.5-flash-lite'
    ]
    
    response = None
    for idx, model_name in enumerate(models_to_try):
        print(f"[Agent 2] Menganalisis pola fraud dan memanggil tools secara otomatis ({model_name})...")
        try:
            chat = client.chats.create(model=model_name, config=config)
            response = chat.send_message(prompt)
            break
        except Exception as e:
            error_str = str(e)
            is_overloaded = any(err in error_str for err in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"])
            if is_overloaded and idx < len(models_to_try) - 1:
                print(f"[Agent 2] Model {model_name} overloaded/error. Fallback ke model berikutnya...")
                continue
            raise e
    
    content = response.text
    print("\n[Agent 2] Selesai Investigasi. Laporan Akhir:")
    print(content)
    
    # Mengembalikan custom object agar kompatibel dengan app/main.py
    class MockMessage:
        def __init__(self, c):
            self.content = c
    class MockChoice:
        def __init__(self, c):
            self.message = MockMessage(c)
    class MockResponse:
        def __init__(self, c):
            self.choices = [MockChoice(c)]

    return MockResponse(content)
