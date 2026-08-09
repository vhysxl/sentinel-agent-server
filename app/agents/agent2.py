"""
Agent 2 — Detektif Pola Fraud.

PERANNYA: memeriksa kandidat secara mendalam dari sisi pola dan perilaku, lalu
MEMILAH — mana pelanggaran yang punya penjelasan sah, mana yang benar berisiko.

BATAS: `verdict`-nya usulan, bukan skor. Hanya Agent 3 yang menggeser angka.

Yang TIDAK diberikan: `find_duplicate_expenses` dan `get_vendor_history`.
Alasannya konkret, bukan teoretis: `find_duplicate_expenses` hanya mencocokkan
nominal identik dalam 24 jam, sehingga untuk split payment berjarak 2 hari ia
selalu mengembalikan "0 ditemukan" — dan pada run sebelumnya Agent 2 karena itu
menulis status "aman" pada temuan berskor 60 High Risk. Tool-nya lebih miskin
daripada detektor Python yang hasilnya sudah ada di dalam prompt.
"""
import json

from app.agents.llm import run_agent
from app.tools.financial import (
    get_monthly_expense_trend,
    get_user_spending_pattern,
    get_vendor_transaction_history,
)

EVIDENCE_TOOLS = [
    get_vendor_transaction_history,
    get_user_spending_pattern,
    get_monthly_expense_trend,
]


def run_fraud_investigator(transaction_id: int, facts: dict | None = None):
    facts = facts or {}
    transaction = facts.get("transaction", {})
    triggers = facts.get("triggers", [])
    month = str(transaction.get("recorded_at", ""))[:7]

    revenue = facts.get("revenue_context") or {}
    revenue_block = (json.dumps(revenue, indent=2, ensure_ascii=False, default=str)
                     if revenue else "(data revenue tidak tersedia untuk bulan ini)")

    findings_block = json.dumps(triggers, indent=2, ensure_ascii=False, default=str) \
        if triggers else "(tidak ada pola fraud terdeteksi pada kandidat ini)"

    prompt = f"""
Kamu adalah Agent 2: Detektif Pola Fraud.

KANDIDAT yang harus kamu periksa (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Bulan transaksi: {month}

KONTEKS REVENUE bulan {month} — DIHITUNG PYTHON, bukan olehmu:
{revenue_block}
Angka ini FINAL. Jangan menghitung ulang, jangan menyebut angka revenue lain.
Kalau kamu menyebut angka revenue yang berbeda dari di atas, itu salah.

POLA YANG SUDAH DIPASTIKAN detektor Python. Ini hasil query, bukan dugaan:
{findings_block}

TUGASMU: memeriksa apakah pola ini punya penjelasan sah, atau benar-benar berisiko.

LANGKAH:
1. Pahami pelanggarannya, dan bedakan dengan tegas — jangan pernah tertukar:
   * `duplicate_confirmed` = faktur SAMA dibayar dua kali. Uang keluar dua kali
     untuk satu kewajiban. Ini BUKAN split payment.
   * `duplicate_suspected` = dugaan, karena nomor faktur kosong. Pakai kata
     "indikasi", bukan "terdeteksi".
   * `split_payment` = faktur BERBEDA, dipecah agar masing-masing lolos ambang
     persetujuan. Tidak ada pembayaran ganda; yang dilanggar kontrol persetujuan.
     Sebutkan nilai ambangnya.
2. Cari bukti perilaku dengan tool sebelum menyimpulkan:
   - `get_user_spending_pattern` — apakah penginput ini biasa bertransaksi
     sebesar ini, atau menyimpang dari kebiasaannya sendiri?
   - `get_vendor_transaction_history` — vendor ini punya rekam jejak, atau baru muncul?
   - `get_monthly_expense_trend` — adakah konteks biaya yang menjelaskan
     lonjakan aktivitas ke vendor ini? (konteks revenue sudah diberikan di atas)
3. Putuskan `verdict`:
   - "explainable" — ada penjelasan sah yang DIDUKUNG ANGKA. Contoh sah:
     kontrak bertahap yang memang dijadwalkan, atau pembayaran berulang rutin.
     WAJIB menyebut angkanya.
   - "risk"        — tidak ada penjelasan, atau justru diperkuat oleh perilaku
     penginput/vendor.
   - "uncertain"   — bukti tidak cukup.

ATURAN KERAS:
- JANGAN membantah pola di atas, dan JANGAN menyatakan "aman" atas kandidat yang
  sudah punya pola. Kalau daftarnya berisi sesuatu, berarti ada yang ditemukan.
  Yang boleh kamu simpulkan adalah apakah pola itu PUNYA PENJELASAN.
- JANGAN mengarang jenis pelanggaran baru.
- Verdict-mu usulan untuk Agent 3, bukan keputusan akhir.

Balas HANYA JSON valid tanpa markdown:
{{
  "verdict": "explainable | risk | uncertain",
  "confidence": "low | medium | high",
  "finding": "Narasi bahasa Indonesia. Sebutkan nomor faktur / nilai ambang bila relevan.",
  "provenance": {{
      "generated_by": "Agent_2_Fraud_Investigator",
      "tools_used": ["tool yang benar-benar kamu panggil"]
  }},
  "evidence": {{
      "context": [
          {{"source": "nama_tool", "insight": "temuan berikut angkanya"}}
      ]
  }}
}}
"""

    response = run_agent(label="Agent 2", prompt=prompt, tools=EVIDENCE_TOOLS)
    print(f"\n[Agent 2] Selesai (model {response.model}).")
    return response
