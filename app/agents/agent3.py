import os
import sys
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Fix Windows terminal UnicodeEncodeError (emoji printing)
sys.stdout.reconfigure(encoding='utf-8')

# Import all tools
from app.tools.financial import (
    get_transaction_details,
    calculate_z_score,
    get_vendor_transaction_history,
    check_transaction_timing,
    get_sales_trend,
    compare_category_baseline,
    get_user_spending_pattern,
    get_monthly_expense_trend
)
from app.tools.fraud import find_duplicate_expenses, get_vendor_history

def run_evidence_reviewer(transaction_id: int, agent1_findings: dict, agent2_findings: dict, base_scoring: dict):
    """
    Menjalankan Agent 3 (Evidence Review & Decision)
    Tugasnya crosscheck hasil Agent 1 dan 2, mencari counter-evidence,
    dan memberikan penilaian akhir (llm_semantic_adjustment).
    """
    load_dotenv(override=True)
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY")
    )

    print(f"\n[Agent 3] Memulai review evidence (Tool Calling Loop) untuk Transaction ID: {transaction_id}...")

    # 1. BENTUK PROMPT SISTEM/USER
    prompt = f"""
Kamu adalah Agent 3: Evidence Review & Decision.
Tugasmu adalah mereview temuan dari Agent 1 dan Agent 2 untuk transaksi dengan ID: {transaction_id}, serta mencari *counter-evidence* (bukti bantahan) jika ada, sebelum laporan final dibuat.

Temuan Agent 1 (Finansial):
{json.dumps(agent1_findings, indent=2)}

Temuan Agent 2 (Fraud):
{json.dumps(agent2_findings, indent=2)}

Skor objektif per-agent dan hasil agregasi dari Scoring Engine:
{json.dumps(base_scoring, indent=2)}

Langkah-langkah:
1. Pahami temuan, evidence, dan skor domain dari Agent 1 dan Agent 2.
2. Review skor dan trigger objektif Agent 1 dan Agent 2. Jangan menghitung ulang skor objektif dari nol; validasi apakah trigger tersebut layak diterima, perlu dibantah, atau perlu konteks tambahan.
3. Gunakan tools yang tersedia (seperti `get_sales_trend`, `compare_category_baseline`, atau tool lain yang menurutmu relevan) untuk memverifikasi apakah temuan tersebut valid atau ada penjelasan bisnis yang sah.
4. Berikan `llm_semantic_adjustment` (nilai -20 hingga +20) berdasarkan hasil investigasimu.
   - Jika kamu menemukan *counter-evidence* (misal: pengeluaran besar terjustifikasi oleh tren sales), berikan adjustment negatif (misal -15) agar skor risiko turun.
   - Jika temuan semakin mencurigakan, berikan adjustment positif.
   - Jika tidak ada temuan baru, berikan 0.

Setelah kamu selesai melakukan crosscheck, berikan laporan akhir HANYA DALAM FORMAT JSON yang valid tanpa markdown apapun.
Format JSON yang DIBUTUHKAN:
{{
  "finding": "Ringkasan evaluasi akhir terhadap bukti-bukti, termasuk counter-evidence jika ada.",
  "provenance": {{
      "generated_by": "Agent_3_Evidence_Review",
      "tools_used": ["daftar_tools_yang_kamu_panggil_sendiri"]
  }},
  "evidence": {{
      "semantic": [
          {{"source": "get_sales_trend", "insight": "Klaim lonjakan biaya marketing sejalan dengan pertumbuhan sales 10% bulan ini."}}
      ]
  }},
  "scoring": {{
      "llm_semantic_adjustment": -10,
      "adjustment_reason": "Lonjakan pengeluaran berbanding lurus dengan peningkatan volume sales bulan ini."
  }}
}}
Ingat: kembalikan HANYA format JSON di output akhirmu!
"""

    # 2. DEFINISIKAN TOOLS & KONFIGURASI
    tools_list = [
        get_transaction_details,
        calculate_z_score,
        get_vendor_transaction_history,
        check_transaction_timing,
        get_sales_trend,
        compare_category_baseline,
        get_user_spending_pattern,
        get_monthly_expense_trend,
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
        'models/gemini-3.5-flash-lite',
        'models/gemini-3-flash-preview'
    ]

    response = None
    for idx, model_name in enumerate(models_to_try):
        print(f"[Agent 3] Mengevaluasi evidence dan memanggil tools ({model_name})...")
        try:
            chat = client.chats.create(model=model_name, config=config)
            response = chat.send_message(prompt)
            break
        except Exception as e:
            error_str = str(e)
            print(f"[Agent 3] Error pada model {model_name}: {error_str}")
            is_overloaded = any(err in error_str for err in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"])
            if is_overloaded and idx < len(models_to_try) - 1:
                print(f"[Agent 3] Fallback ke model berikutnya...")
                continue
            raise e

    content = response.text
    print("\n[Agent 3] Selesai Investigasi. Laporan Akhir:")
    print(content)

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
