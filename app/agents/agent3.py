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
    get_user_spending_pattern,
    get_vendor_transaction_history,
)

# Gabungan tool kedua detektif: apa pun yang mereka klaim, Agent 3 bisa cek ulang.
VERIFICATION_TOOLS = [
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
    month = str(transaction.get("recorded_at", ""))[:7]

    revenue = facts.get("revenue_context") or {}
    revenue_block = (json.dumps(revenue, indent=2, ensure_ascii=False, default=str)
                     if revenue else "(data revenue tidak tersedia untuk bulan ini)")

    triggers = [
        {"code": t["code"], "points": t["points"], "narrative": t["narrative"]}
        for t in base_scoring.get("objective_triggers", [])
    ]

    prompt = f"""
You are Agent 3: Verifier and final report writer.

TRANSACTION (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Transaction month: {month}

REVENUE CONTEXT for {month} — COMPUTED BY PYTHON, not by you:
{revenue_block}
This number is FINAL. Do not recompute it, do not cite a different revenue figure.
If you cite a revenue number different from the one above, that is wrong.

OBJECTIVE SCORE from the engine (base = {base_scoring.get('base_risk_score')}):
{json.dumps(triggers, indent=2, ensure_ascii=False, default=str)}

INVESTIGATOR 1 VERDICT (Financial Analytics):
{_summarize(agent1_findings, "Agent 1")}

INVESTIGATOR 2 VERDICT (Fraud Pattern):
{_summarize(agent2_findings, "Agent 2")}

YOUR JOB: verify both verdicts, then write the final report.

STEPS:
1. For each investigator, check whether their verdict is SUPPORTED by evidence:
   - If they cite a REVENUE number, compare it against the REVENUE CONTEXT
     above. An investigator citing a revenue number different from that is
     WRONG — reject their verdict, no matter how confident it sounds.
   - For other claims, call the same tools yourself to confirm.
   - If they cite something that is NOT in the objective trigger list, treat
     it as an unsupported claim and reject it.
   - If they FAILED to run, do not make up their content. Verify from the
     objective facts alone.
2. Decide the `llm_semantic_adjustment` between -20 and +20:
   - NEGATIVE only when the business justification is VERIFIED by your own
     tool calls. You MUST cite a concrete number that MATCHES the context above.
   - POSITIVE when the context actually makes it worse, e.g. a claim in the
     transaction description is contradicted by the data.
   - ZERO when there's no new information. Zero is often the correct answer.
3. Write the `finding`: the final report the auditor reads first. State what
   happened, how confident you are, and what action is recommended.


LANGUAGE — MANDATORY:
Your reader is a finance person, not an engineer. Write as if explaining to a
finance manager in a meeting. Write the `finding` narrative in English.

FORBIDDEN in your narrative: z-score, modified z, MAD, median, baseline,
threshold, standard deviation, outlier, trigger, amplifier, and rule code
names like `duplicate_confirmed` or `insufficient_baseline`. Those are machine
terms; the numbers are already stored separately for auditors who dig in.

Replace with something immediately concrete:
  "usually around Rp20 million, this one is Rp45 million"   not  "z-score 158"
  "about 26 times the usual amount"                          not  "median 1.7 million"
  "not enough history yet to compare"                        not  "insufficient_baseline"
  "paid twice for a single invoice"                           not  "duplicate_confirmed"

State the RUPIAH amount and its impact. The reader only wants to know three
things: what happened, how much money is involved, and what to do about it.

BOUNDARIES YOU MUST NOT CROSS:
- Do not recompute or dispute the objective numbers. You are verifying the
  INTERPRETATION of the numbers, not the numbers themselves.
- Certain facts like `duplicate_confirmed` and `split_payment` cannot be
  cancelled out by argument. At most reduced slightly, and only with concrete
  evidence.
- Do not invent new findings.

Reply with ONLY valid JSON, no markdown:
{{
  "finding": "English final report for the auditor.",
  "verdict_review": {{
      "agent1": "agreed | rejected | unverifiable",
      "agent2": "agreed | rejected | unverifiable",
      "reason": "why you agreed or rejected"
  }},
  "provenance": {{
      "generated_by": "Agent_3_Evidence_Review",
      "tools_used": ["tools you actually called to verify"]
  }},
  "evidence": {{
      "semantic": [
          {{"source": "revenue_context", "insight": "verification result with its numbers"}}
      ]
  }},
  "scoring": {{
      "llm_semantic_adjustment": 0,
      "adjustment_reason": "Brief reason. Cite numbers if giving a negative value."
  }}
}}
"""

    # temperature=0: verifikator memutuskan angka, bukan menulis prosa. Pada 0.2
    # transaksi yang sama pernah menghasilkan -15, +15, dan -10 di tiga kali
    # jalan berturut-turut. Keputusan audit tidak boleh berubah karena undian.
    response = run_agent(label="Agent 3", prompt=prompt, tools=VERIFICATION_TOOLS,
                         agent="agent3", temperature=0.0)
    print(f"\n[Agent 3] Selesai (model {response.model}).")
    return response
