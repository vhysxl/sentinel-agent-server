"""
Agent 3 — Verifikator & Penulis Laporan.

PERANNYA: memverifikasi putusan Agent 1 dan Agent 2 — mana yang benar-benar
berisiko, mana yang tidak — lalu menulis laporan akhir yang disimpan ke tabel
`findings`.

Ia diberi tool yang sama dengan kedua detektif, dan itu disengaja: verifikasi
berarti bisa MEMERIKSA SENDIRI, bukan sekadar mempercayai laporan. Kalau seorang
detektif mengklaim "revenue naik 43%", Agent 3 harus bisa memanggil tool yang
sama dan melihat apakah benar.

Ia satu-satunya yang boleh menggeser skor, maksimal +/-20, wajib berasalan.
Batas itu disengaja: fakta objektif tidak boleh dihapus oleh narasi. Duplikasi
faktur bernilai 50 poin tidak akan pernah bisa dijadikan nol, sekuat apa pun
argumennya.

Tool DETEKSI tetap tidak diberikan. Kalau ia bisa menghitung ulang z-score atau
mencari duplikat sendiri, ia bisa diam-diam membantah skor dasar tanpa jejak.
"""
import json

from app.agents.llm import run_agent
from app.tools.financial import (
    compare_category_baseline,
    get_monthly_expense_trend,
    get_sales_trend,
    get_user_spending_pattern,
    get_vendor_transaction_history,
)

# Gabungan tool kedua detektif: apa pun yang mereka klaim, Agent 3 bisa cek ulang.
VERIFICATION_TOOLS = [
    get_sales_trend,
    compare_category_baseline,
    get_monthly_expense_trend,
    get_vendor_transaction_history,
    get_user_spending_pattern,
]


def _summarize(agent_findings: dict, label: str) -> str:
    if not agent_findings or agent_findings.get("_failed"):
        return f"{label}: GAGAL dijalankan. Abaikan, jangan mengarang isinya."
    return json.dumps({
        "verdict": agent_findings.get("verdict"),
        "confidence": agent_findings.get("confidence"),
        "finding": agent_findings.get("finding"),
        "evidence": agent_findings.get("evidence", {}).get("context", []),
        "tools_used": agent_findings.get("provenance", {}).get("tools_used", []),
    }, indent=2, ensure_ascii=False, default=str)


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
Kamu adalah Agent 3: Verifikator sekaligus penulis laporan akhir.

TRANSAKSI (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Bulan transaksi: {month}

SKOR OBJEKTIF dari mesin (base = {base_scoring.get('base_risk_score')}):
{json.dumps(triggers, indent=2, ensure_ascii=False, default=str)}

PUTUSAN DETEKTIF 1 (Analitik Finansial):
{_summarize(agent1_findings, "Agent 1")}

PUTUSAN DETEKTIF 2 (Pola Fraud):
{_summarize(agent2_findings, "Agent 2")}

TUGASMU: memverifikasi kedua putusan itu, lalu menulis laporan akhir.

LANGKAH:
1. Untuk setiap detektif, periksa apakah putusannya DIDUKUNG bukti:
   - Kalau ia bilang "explainable", panggil tool yang sama untuk memastikan angka
     yang ia sebut memang benar. Detektif bisa keliru membaca atau mengarang.
   - Kalau ia menyebut sesuatu yang TIDAK ada dalam daftar trigger objektif,
     perlakukan sebagai klaim tak berdasar dan tolak.
   - Kalau ia GAGAL dijalankan, jangan mengarang isinya. Verifikasi dari fakta
     objektif saja.
2. Putuskan `llm_semantic_adjustment` antara -20 dan +20:
   - NEGATIF hanya bila pembenaran bisnisnya TERVERIFIKASI oleh tool-mu sendiri.
     WAJIB menyebut angka konkret (mis. "revenue naik 43.39%"). Tanpa angka
     terverifikasi, jangan beri nilai negatif.
   - POSITIF bila konteks justru memberatkan, misalnya klaim pada deskripsi
     transaksi terbantahkan data.
   - NOL bila tidak ada informasi baru. Nol sering merupakan jawaban yang benar.
3. Tulis `finding`: laporan akhir yang akan dibaca auditor lebih dulu. Sebutkan
   apa yang terjadi, seberapa yakin, dan apa tindakan yang disarankan.

BATAS YANG TIDAK BOLEH DILANGGAR:
- Jangan menghitung ulang atau membantah angka objektif. Kamu memverifikasi
  PENAFSIRAN atas angka, bukan angkanya.
- Fakta pasti seperti `duplicate_confirmed` dan `split_payment` tidak dapat
  dibatalkan oleh argumen. Paling jauh diturunkan sedikit, itu pun hanya dengan
  bukti konkret.
- Jangan mengarang temuan baru.

Balas HANYA JSON valid tanpa markdown:
{{
  "finding": "Laporan akhir bahasa Indonesia untuk auditor.",
  "verdict_review": {{
      "agent1": "agreed | rejected | unverifiable",
      "agent2": "agreed | rejected | unverifiable",
      "reason": "kenapa kamu setuju atau menolak"
  }},
  "provenance": {{
      "generated_by": "Agent_3_Evidence_Review",
      "tools_used": ["tool yang benar-benar kamu panggil untuk memverifikasi"]
  }},
  "evidence": {{
      "semantic": [
          {{"source": "get_sales_trend", "insight": "hasil verifikasi berikut angkanya"}}
      ]
  }},
  "scoring": {{
      "llm_semantic_adjustment": 0,
      "adjustment_reason": "Alasan singkat. Sebutkan angka bila memberi nilai negatif."
  }}
}}
"""

    response = run_agent(label="Agent 3", prompt=prompt, tools=VERIFICATION_TOOLS)
    print(f"\n[Agent 3] Selesai (model {response.model}).")
    return response
