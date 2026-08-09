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
    get_vendor_transaction_history,
)

EVIDENCE_TOOLS = [
    get_vendor_transaction_history,
    compare_category_baseline,
    get_monthly_expense_trend,
]


def run_financial_investigator(transaction_id: int, facts: dict | None = None):
    facts = facts or {}
    transaction = facts.get("transaction", {})
    triggers = facts.get("triggers", [])
    month = str(transaction.get("recorded_at", ""))[:7]

    revenue = facts.get("revenue_context") or {}
    revenue_block = (json.dumps(revenue, indent=2, ensure_ascii=False, default=str)
                     if revenue else "(data revenue tidak tersedia untuk bulan ini)")

    findings_block = json.dumps(triggers, indent=2, ensure_ascii=False, default=str) \
        if triggers else "(tidak ada trigger finansial pada kandidat ini)"

    prompt = f"""
Kamu adalah Agent 1: Detektif Analitik Finansial.

KANDIDAT yang harus kamu periksa (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Bulan transaksi: {month}

KONTEKS REVENUE bulan {month} — DIHITUNG PYTHON, bukan olehmu:
{revenue_block}
Angka ini FINAL. Jangan menghitung ulang, jangan menyebut angka revenue lain.
Kalau kamu menyebut angka revenue yang berbeda dari di atas, itu salah.

FAKTA OBJEKTIF yang SUDAH dihitung mesin statistik Python.
Angka ini final dan sudah menjadi dasar skor:
{findings_block}

Kandidat ini dipilih detektor yang sengaja dibuat longgar — supaya tidak ada yang
lolos. Konsekuensinya sebagian kandidat memang wajar. TUGASMU MENYEMPITKAN.

LANGKAH:
1. Pahami angka objektif di atas.
2. Cari bukti dengan tool. Jangan menyimpulkan sebelum memanggil tool:
   Konteks revenue SUDAH diberikan di atas — jangan mencarinya lagi. Lonjakan
   biaya yang sebanding dengan pertumbuhan revenue adalah pengeluaran wajar.
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


BAHASA — WAJIB DIPATUHI:
Pembacamu adalah orang keuangan, bukan engineer. Tulis seperti menjelaskan ke
manajer keuangan dalam rapat.

DILARANG muncul di narasimu: z-score, modified z, MAD, median, baseline,
ambang, standar deviasi, outlier, trigger, amplifier, dan nama kode aturan
seperti `duplicate_confirmed` atau `insufficient_baseline`. Itu istilah mesin;
angkanya sudah tersimpan terpisah untuk auditor yang menelusuri.

Ganti dengan yang langsung terbayang:
  "biasanya sekitar Rp20 juta, yang ini Rp45 juta"   bukan  "z-score 158"
  "sekitar 26 kali lipat dari biasanya"               bukan  "median 1,7 juta"
  "belum ada cukup riwayat untuk dibandingkan"        bukan  "insufficient_baseline"
  "dibayar dua kali untuk satu tagihan"               bukan  "duplicate_confirmed"

Sebutkan RUPIAH dan dampaknya. Yang ingin diketahui pembaca cuma tiga hal:
apa yang terjadi, berapa uang yang terlibat, dan apa yang harus ia lakukan.

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
