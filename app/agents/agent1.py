"""
Agent 1 — Detektif Analitik Finansial.

PERANNYA: memeriksa kandidat secara mendalam dari sisi angka, lalu MEMILAH —
mana yang sebenarnya bisa dijelaskan, mana yang benar-benar berisiko.

Ia diberi tool evidence penuh (tren revenue, baseline kategori, tren biaya,
riwayat vendor) karena memilah butuh bukti, bukan tebakan. Kandidat datang dari
detektor Python yang sengaja longgar; tugas agen ini menyempitkannya.

BATAS: putusannya (`verdict`) adalah USULAN, bukan skor. Ia tidak menggeser
angka sedikit pun. Hanya Agent 3 yang boleh, dan hanya +/-20. Kalau tiga agen
bisa menggeser skor, tidak ada lagi yang dapat ditelusuri.

Yang TIDAK diberikan: tool deteksi (`calculate_z_score`, `check_transaction_timing`).
Keduanya sudah dihitung Python dan hasilnya ada di dalam prompt. Menghitung ulang
hanya melahirkan versi kedua dari angka yang sama.
"""
import json

from app.agents.llm import run_agent
from app.tools.financial import (
    compare_category_baseline,
    get_monthly_expense_trend,
    get_sales_trend,
    get_vendor_transaction_history,
)

EVIDENCE_TOOLS = [
    get_vendor_transaction_history,
    compare_category_baseline,
    get_monthly_expense_trend,
    get_sales_trend,
]


def run_financial_investigator(transaction_id: int, facts: dict | None = None):
    facts = facts or {}
    transaction = facts.get("transaction", {})
    triggers = facts.get("triggers", [])
    month = str(transaction.get("transaction_date", ""))[:7]

    findings_block = json.dumps(triggers, indent=2, ensure_ascii=False, default=str) \
        if triggers else "(tidak ada trigger finansial pada kandidat ini)"

    prompt = f"""
Kamu adalah Agent 1: Detektif Analitik Finansial.

KANDIDAT yang harus kamu periksa (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Bulan transaksi: {month}

FAKTA OBJEKTIF yang SUDAH dihitung mesin statistik Python.
Angka ini final dan sudah menjadi dasar skor:
{findings_block}

Kandidat ini dipilih detektor yang sengaja dibuat longgar — supaya tidak ada yang
lolos. Konsekuensinya sebagian kandidat memang wajar. TUGASMU MENYEMPITKAN.

LANGKAH:
1. Pahami angka objektif di atas.
2. Cari bukti dengan tool. Jangan menyimpulkan sebelum memanggil tool:
   - `get_sales_trend("{month}")` — apakah revenue bulan itu naik? Lonjakan biaya
     yang sebanding dengan pertumbuhan revenue adalah pengeluaran wajar, bukan anomali.
   - `get_monthly_expense_trend` — apakah biaya memang sedang naik menyeluruh,
     atau transaksi ini menyendiri?
   - `compare_category_baseline` — bagaimana dibanding kategori sejenis?
   - `get_vendor_transaction_history` — seperti apa kebiasaan vendor ini?
3. Putuskan `verdict`:
   - "explainable" — ada penjelasan bisnis yang DIDUKUNG ANGKA dari tool.
     WAJIB menyebut angkanya. Tanpa angka, jangan pilih ini.
   - "risk"        — tidak ada penjelasan, atau klaim pada deskripsi transaksi
     justru terbantahkan oleh data.
   - "uncertain"   — bukti tidak cukup untuk condong ke mana pun. Ini jawaban
     yang sah dan lebih baik daripada menebak.

ATURAN KERAS:
- JANGAN menghitung ulang atau membantah angka objektif. Itu bukan pendapat.
- JANGAN mengarang metrik yang tidak ada di daftar trigger.
- Kalau statusnya `insufficient_baseline`, artinya nominal TIDAK dapat dinilai
  secara statistik. Itu bukan berarti wajar, dan bukan berarti skornya nol.
- Verdict-mu adalah usulan untuk Agent 3, bukan keputusan akhir.

Balas HANYA JSON valid tanpa markdown:
{{
  "verdict": "explainable | risk | uncertain",
  "confidence": "low | medium | high",
  "finding": "Narasi bahasa Indonesia untuk auditor. Sebutkan angka konkret dari tool.",
  "provenance": {{
      "generated_by": "Agent_1_Financial_Analytics",
      "tools_used": ["tool yang benar-benar kamu panggil"]
  }},
  "evidence": {{
      "context": [
          {{"source": "nama_tool", "insight": "temuan berikut angkanya"}}
      ]
  }}
}}
"""

    response = run_agent(label="Agent 1", prompt=prompt, tools=EVIDENCE_TOOLS)
    print(f"\n[Agent 1] Selesai (model {response.model}).")
    return response
