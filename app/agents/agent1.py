"""
Agent 1 — Detektif Analitik Finansial.

PERANNYA: menyelidiki. Ia menerima fakta statistik yang sudah dihitung Python,
lalu mencari KONTEKS di sekitarnya — apakah pola belanja vendor ini masuk akal,
apakah kategorinya sedang naik, apakah ada penjelasan di balik angka itu.

Tool yang dimilikinya adalah tool KONTEKS, bukan tool deteksi. Ia tidak
menghitung ulang z-score atau memeriksa ulang jam, karena keduanya sudah
dihitung dan menjadi dasar skor; menghitung ulang hanya menghasilkan versi kedua
dari angka yang sama, yang bisa berbeda dan membingungkan pembaca.

Ia TIDAK mempengaruhi skor. Skor sudah ditetapkan scoring engine sebelum agen ini
dipanggil.
"""
import json

from app.agents.llm import run_agent
from app.tools.financial import (
    compare_category_baseline,
    get_monthly_expense_trend,
    get_vendor_transaction_history,
)

# Tool KONTEKS saja. Tidak ada calculate_z_score / check_transaction_timing:
# keduanya sudah dijalankan Python dan hasilnya ada di dalam prompt.
CONTEXT_TOOLS = [
    get_vendor_transaction_history,
    compare_category_baseline,
    get_monthly_expense_trend,
]


def run_financial_investigator(transaction_id: int, facts: dict | None = None):
    """
    `facts` berisi transaksi dan trigger milik Agent 1 yang sudah dihitung.
    Dibuat opsional agar pemanggilan lama tetap jalan, tetapi tanpa fakta agen
    ini hanya bisa menebak.
    """
    facts = facts or {}
    transaction = facts.get("transaction", {})
    triggers = facts.get("triggers", [])

    findings_block = json.dumps(triggers, indent=2, ensure_ascii=False, default=str) \
        if triggers else "(tidak ada trigger finansial pada transaksi ini)"

    prompt = f"""
Kamu adalah Agent 1: Detektif Analitik Finansial.

Transaksi yang diselidiki (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}

FAKTA OBJEKTIF yang SUDAH dihitung oleh mesin statistik Python.
Angka-angka ini final dan sudah menjadi dasar skor risiko:
{findings_block}

ATURAN KERAS:
- JANGAN menghitung ulang atau membantah angka di atas. Angka itu bukan pendapat.
- JANGAN mengarang metrik baru yang tidak ada di daftar itu.
- Kalau sebuah nilai berstatus `insufficient_baseline`, artinya nominal TIDAK
  dapat dinilai secara statistik. Itu bukan berarti transaksinya wajar, dan
  bukan berarti skornya nol. Katakan apa adanya.
- Tugasmu adalah MENJELASKAN, bukan menilai ulang.

Tugasmu sebagai detektif:
1. Terjemahkan angka di atas menjadi kalimat yang dimengerti orang keuangan.
2. Gunakan tool konteks untuk mencari LATAR di sekitar angka itu:
   - `get_vendor_transaction_history` — seperti apa kebiasaan vendor ini?
   - `compare_category_baseline` — bagaimana kalau dibandingkan kategorinya?
   - `get_monthly_expense_trend` — apakah biaya memang sedang naik belakangan?
3. Sebutkan apa yang kamu temukan, termasuk kalau konteksnya justru MEMBUAT
   transaksi ini terlihat lebih wajar. Detektif yang jujur melaporkan keduanya.

Balas HANYA JSON valid tanpa markdown:
{{
  "finding": "Narasi bahasa Indonesia untuk auditor. Sebutkan angka konkret.",
  "provenance": {{
      "generated_by": "Agent_1_Financial_Analytics",
      "tools_used": ["tool konteks yang benar-benar kamu panggil"]
  }},
  "evidence": {{
      "context": [
          {{"source": "nama_tool", "insight": "apa yang kamu temukan dari tool itu"}}
      ]
  }}
}}
"""

    response = run_agent(label="Agent 1", prompt=prompt, tools=CONTEXT_TOOLS)
    print(f"\n[Agent 1] Selesai (model {response.model}).")
    return response
