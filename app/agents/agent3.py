"""
Agent 3 — Verifikator.

PERANNYA berbeda dari Agent 1 dan 2. Mereka detektif yang menyelidiki; ia
menantang hasil penyelidikan itu. Pertanyaannya bukan "apa yang terjadi"
melainkan "apakah ada penjelasan sah yang membuat temuan ini tidak berarti apa
yang disangka".

Ia satu-satunya yang boleh menggeser skor, maksimal +/-20, dan wajib menyertakan
alasan tertulis. Batas itu disengaja: fakta objektif tidak boleh dihapus oleh
narasi. Duplikasi faktur bernilai 50 poin tidak akan pernah bisa dibuat menjadi
nol, sekuat apa pun argumen yang ditulis.

Tool deteksi tidak diberikan. Kalau ia bisa menghitung ulang z-score atau mencari
duplikat sendiri, ia bisa diam-diam membantah skor dasar tanpa meninggalkan jejak
— persis masalah yang seluruh arsitektur ini dibuat untuk mencegah.
"""
import json

from app.agents.llm import run_agent
from app.tools.financial import (
    compare_category_baseline,
    get_monthly_expense_trend,
    get_sales_trend,
)

# Tool KONTEKS BISNIS. Inilah yang memungkinkan sebuah false positive dibatalkan:
# lonjakan biaya yang sejalan dengan pertumbuhan revenue bukan anomali.
CONTEXT_TOOLS = [
    get_sales_trend,
    compare_category_baseline,
    get_monthly_expense_trend,
]


def run_evidence_reviewer(transaction_id: int, agent1_findings: dict,
                          agent2_findings: dict, base_scoring: dict,
                          facts: dict | None = None):
    facts = facts or {}
    transaction = facts.get("transaction", {})
    month = str(transaction.get("transaction_date", ""))[:7]

    triggers = [
        {"code": t["code"], "points": t["points"], "narrative": t["narrative"]}
        for t in base_scoring.get("objective_triggers", [])
    ]

    prompt = f"""
Kamu adalah Agent 3: Verifikator.

Transaksi (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Bulan transaksi: {month}

SKOR OBJEKTIF yang sudah ditetapkan mesin (base = {base_scoring.get('base_risk_score')}):
{json.dumps(triggers, indent=2, ensure_ascii=False, default=str)}

Laporan Detektif 1 (Analitik Finansial):
{json.dumps(agent1_findings, indent=2, ensure_ascii=False, default=str)}

Laporan Detektif 2 (Pola Fraud):
{json.dumps(agent2_findings, indent=2, ensure_ascii=False, default=str)}

TUGASMU: memverifikasi, bukan menyelidiki ulang.

1. Periksa apakah klaim kedua detektif benar-benar didukung fakta objektif di
   atas. Kalau seorang detektif menyebut sesuatu yang TIDAK ada dalam daftar
   trigger, sebut itu sebagai klaim tak berdasar dan abaikan.
2. Cari penjelasan bisnis yang sah dengan tool konteks:
   - `get_sales_trend("{month}")` — apakah revenue bulan itu naik? Lonjakan biaya
     yang sebanding dengan pertumbuhan revenue BUKAN anomali.
   - `compare_category_baseline` / `get_monthly_expense_trend` — apakah kategori
     ini memang sedang naik secara menyeluruh?
3. Tentukan `llm_semantic_adjustment` antara -20 dan +20:
   - NEGATIF bila kamu menemukan pembenaran nyata. WAJIB menyebut angka konkret
     dari tool (mis. "revenue naik 43.39%"). Tanpa angka, jangan beri nilai negatif.
   - POSITIF bila konteks justru memberatkan, misalnya klaim pada deskripsi
     transaksi terbantahkan oleh data.
   - NOL bila tidak ada informasi baru. Nol adalah jawaban yang benar dan sering.

BATAS YANG TIDAK BOLEH DILANGGAR:
- Jangan menghitung ulang atau membantah angka objektif. Kamu memverifikasi
  PENAFSIRAN atas angka, bukan angkanya.
- Fakta pasti seperti `duplicate_confirmed` dan `split_payment` tidak dapat
  dibatalkan oleh argumen. Paling jauh kamu boleh menurunkan skornya sedikit,
  itu pun hanya kalau ada bukti konkret.
- Jangan mengarang temuan baru.

Balas HANYA JSON valid tanpa markdown:
{{
  "finding": "Narasi final bahasa Indonesia untuk auditor. Inilah yang dibaca lebih dulu: sebutkan apa yang terjadi, seberapa yakin, dan apa yang harus dilakukan.",
  "provenance": {{
      "generated_by": "Agent_3_Evidence_Review",
      "tools_used": ["tool yang benar-benar kamu panggil"]
  }},
  "evidence": {{
      "semantic": [
          {{"source": "get_sales_trend", "insight": "temuan konteks berikut angkanya"}}
      ]
  }},
  "scoring": {{
      "llm_semantic_adjustment": 0,
      "adjustment_reason": "Alasan singkat. Sebutkan angka bila memberi nilai negatif."
  }}
}}
"""

    response = run_agent(label="Agent 3", prompt=prompt, tools=CONTEXT_TOOLS)
    print(f"\n[Agent 3] Selesai (model {response.model}).")
    return response
