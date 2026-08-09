"""
Agent 2 — Detektif Pola Fraud.

PERANNYA: menyelidiki pola. Ia menerima pelanggaran yang sudah dipastikan Python
(duplikasi faktur, split payment, status vendor), lalu mencari konteks perilaku
di sekitarnya — siapa yang menginput, apakah ini kebiasaan atau kejadian tunggal.

Tool deteksinya dicabut karena alasan konkret, bukan teoretis:
`find_duplicate_expenses` hanya mencari nominal identik dalam 24 jam, sehingga
untuk kasus split payment berjarak 2 hari ia SELALU mengembalikan "0 ditemukan".
Pada run sebelumnya Agent 2 karena itu menulis status "aman" pada temuan yang
diberi skor 60 High Risk. Toolset-nya lebih miskin daripada detektor Python,
jadi memakainya hanya menghasilkan bantahan yang keliru.

Ia TIDAK mempengaruhi skor.
"""
import json

from app.agents.llm import run_agent
from app.tools.financial import (
    get_user_spending_pattern,
    get_vendor_transaction_history,
)

# Tool KONTEKS saja: siapa orangnya, seperti apa vendornya.
# Tidak ada find_duplicate_expenses / get_vendor_history — keduanya sudah
# dijalankan Python dengan definisi yang lebih lengkap.
CONTEXT_TOOLS = [
    get_vendor_transaction_history,
    get_user_spending_pattern,
]


def run_fraud_investigator(transaction_id: int, facts: dict | None = None):
    facts = facts or {}
    transaction = facts.get("transaction", {})
    triggers = facts.get("triggers", [])

    findings_block = json.dumps(triggers, indent=2, ensure_ascii=False, default=str) \
        if triggers else "(tidak ada pola fraud yang terdeteksi pada transaksi ini)"

    prompt = f"""
Kamu adalah Agent 2: Detektif Pola Fraud.

Transaksi yang diselidiki (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}

POLA YANG SUDAH DIPASTIKAN oleh detektor Python. Ini hasil query, bukan dugaan:
{findings_block}

ATURAN KERAS:
- JANGAN membantah pola di atas dan jangan menyatakan "aman" atas transaksi yang
  sudah punya pola. Kalau daftarnya berisi sesuatu, berarti ada yang ditemukan.
- JANGAN mengarang jenis pelanggaran baru.
- Bedakan dengan tegas, jangan pernah tertukar:
    * `duplicate_confirmed`  = faktur yang SAMA dibayar dua kali. Uang keluar
      dua kali untuk satu kewajiban. Ini BUKAN split payment.
    * `duplicate_suspected`  = dugaan, karena nomor faktur tidak diisi. Wajib
      memakai kata "indikasi", bukan "terdeteksi".
    * `split_payment`        = faktur BERBEDA, sengaja dipecah agar masing-masing
      lolos ambang persetujuan. Tidak ada pembayaran ganda di sini; yang
      dilanggar adalah kontrol persetujuan. Sebutkan nilai ambangnya.
- Kalau daftarnya kosong, katakan tidak ada pola fraud yang terdeteksi.

Tugasmu sebagai detektif:
1. Jelaskan pelanggarannya dalam bahasa yang dimengerti orang keuangan, dan
   sebutkan APA yang dilanggar (uang ganda? atau kontrol persetujuan?).
2. Gunakan tool konteks untuk melihat latar perilakunya:
   - `get_user_spending_pattern` — apakah penginput ini biasa bertransaksi
     sebesar ini, atau ini menyimpang dari kebiasaannya?
   - `get_vendor_transaction_history` — apakah vendor ini punya rekam jejak?
3. Laporkan juga kalau konteksnya justru meringankan.

Balas HANYA JSON valid tanpa markdown:
{{
  "finding": "Narasi bahasa Indonesia. Sebutkan nomor faktur / nilai ambang bila relevan.",
  "provenance": {{
      "generated_by": "Agent_2_Fraud_Investigator",
      "tools_used": ["tool konteks yang benar-benar kamu panggil"]
  }},
  "evidence": {{
      "context": [
          {{"source": "nama_tool", "insight": "apa yang kamu temukan dari tool itu"}}
      ]
  }}
}}
"""

    response = run_agent(label="Agent 2", prompt=prompt, tools=CONTEXT_TOOLS)
    print(f"\n[Agent 2] Selesai (model {response.model}).")
    return response
