import os
import sys
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Fix Windows terminal UnicodeEncodeError (emoji printing)
sys.stdout.reconfigure(encoding='utf-8')

# Import tools
from app.tools.financial import (
    get_transaction_details,
    calculate_z_score,
    get_vendor_transaction_history,
    check_transaction_timing
)

def run_financial_investigator(transaction_id: int):
    """
    Menjalankan Agent 1 (Financial Analytics Investigator)
    untuk menginvestigasi transaksi menggunakan Gemini API secara Interaktif (Tool Calling).
    """
    load_dotenv(override=True)
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY")
    )

    print(f"\n[Agent 1] Memulai investigasi (Tool Calling Loop) untuk Transaction ID: {transaction_id}...")

    # 1. BENTUK PROMPT SISTEM/USER
    prompt = f"""
Kamu adalah Agent 1: Financial Analytics Investigator.
Tugasmu adalah menginvestigasi transaksi dengan ID: {transaction_id}.

Gunakan tools yang tersedia untuk mengumpulkan fakta objektif tentang:
1. Detail transaksi
2. Z-Score anomali pengeluaran
3. Histori transaksi vendor
4. Waktu transaksi (jam kerja/akhir pekan)

Setelah kamu memiliki semua data objektif yang diperlukan, berikan laporan akhir HANYA DALAM FORMAT JSON yang valid tanpa markdown apapun.
Gunakan nama metric yang stabil karena backend akan menghitung skor domain Agent 1 dari evidence ini.
Format JSON yang DIBUTUHKAN:
{{
  "finding": "Ringkasan komprehensif temuan dari multi-dimensi (timing, vendor, z-score).",
  "provenance": {{
      "generated_by": "Agent_1_Financial_Analytics",
      "tools_used": ["daftar_tools_yang_kamu_panggil"]
  }},
  "evidence": {{
      "objective": [
          {{"metric": "z_score", "value": 70.58, "status": "anomali ekstrim"}},
          {{"metric": "timing", "value": "23:59:00", "status": "unusual_timing"}},
          {{"metric": "vendor_history", "value": "new_vendor", "status": "mencurigakan"}}
      ]
  }}
}}
Ingat: kembalikan HANYA format JSON di output akhirmu!
"""

    # 2. DEFINISIKAN TOOLS & KONFIGURASI
    tools_list = [
        get_transaction_details,
        calculate_z_score,
        get_vendor_transaction_history,
        check_transaction_timing
    ]

    config = types.GenerateContentConfig(
        tools=tools_list,
        temperature=0.2,
        top_p=0.95,
        max_output_tokens=4096,
        # Mengaktifkan automatic function calling (Gemini akan mengeksekusi fungsi Python secara otomatis)
    )

    # 3. PANGGIL LLM (CHAT SESSION) DENGAN MULTI-FALLBACK
    models_to_try = [
        'models/gemini-3.5-flash',
        'models/gemini-3.6-flash',
        'models/gemini-3.5-flash-lite',
        'models/gemini-3-flash-preview'
    ]

    response = None
    for idx, model_name in enumerate(models_to_try):
        print(f"[Agent 1] Menganalisis dan memanggil tools secara otomatis ({model_name})...")
        try:
            chat = client.chats.create(model=model_name, config=config)
            response = chat.send_message(prompt)
            break  # Berhasil, keluar dari loop fallback
        except Exception as e:
            error_str = str(e)
            print(f"[Agent 1] Error pada model {model_name}: {error_str}")
            is_overloaded = any(err in error_str for err in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"])
            if is_overloaded and idx < len(models_to_try) - 1:
                print(f"[Agent 1] Fallback ke model berikutnya...")
                continue
            # Jika error selain overload, atau sudah mentok di model terakhir
            raise e


    content = response.text
    print("\n[Agent 1] Selesai Investigasi. Laporan Akhir:")
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
