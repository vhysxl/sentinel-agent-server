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
You are Agent 2: Fraud Pattern Investigator.

CANDIDATE you must examine (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Transaction month: {month}

REVENUE CONTEXT for {month} — COMPUTED BY PYTHON, not by you:
{revenue_block}
This number is FINAL. Do not recompute it, do not cite a different revenue figure.
If you cite a revenue number different from the one above, that is wrong.

PATTERNS ALREADY CONFIRMED by the Python detector. This is a query result, not
a guess:
{findings_block}

YOUR JOB: determine whether this pattern has a legitimate explanation, or is
genuinely risky.

STEPS:
1. Understand the violation, and distinguish these carefully — never confuse them:
   * `duplicate_confirmed` = the SAME invoice paid twice. Money went out twice
     for one obligation. This is NOT a split payment.
   * `duplicate_suspected` = a suspicion, because the invoice number is empty.
     Use "indication", not "detected".
   * `split_payment` = DIFFERENT invoices, broken up so each one clears the
     approval threshold. There's no double payment; what's violated is
     approval control. Cite the threshold value.
2. Gather behavioral evidence with the tools before concluding:
   - `get_user_spending_pattern` — does this submitter normally transact at
     this size, or is this a departure from their own habits?
   - `get_vendor_transaction_history` — does this vendor have a track record,
     or is it newly appeared?
   - `get_monthly_expense_trend` — is there a cost context that explains the
     spike in activity to this vendor? (revenue context is already given above)
3. Decide the `verdict`:
   - "explainable" — there is a legitimate explanation SUPPORTED BY NUMBERS.
     Legitimate examples: a staged contract that was genuinely scheduled, or a
     routine recurring payment. You MUST cite the numbers.
   - "risk"        — no explanation exists, or the pattern is actually
     reinforced by the submitter's/vendor's behavior.
   - "uncertain"   — not enough evidence.


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

HARD RULES:
- DO NOT dispute the pattern above, and DO NOT call a candidate that already
  has a confirmed pattern "safe". If the list contains something, something
  was found. What you may conclude is whether that pattern HAS AN EXPLANATION.
- DO NOT invent a new type of violation.
- Your verdict is a proposal for Agent 3, not the final decision.

Reply with ONLY valid JSON, no markdown:
{{
  "verdict": "explainable | risk | uncertain",
  "confidence": "low | medium | high",
  "finding": "English narrative. Cite invoice numbers / threshold values where relevant.",
  "provenance": {{
      "generated_by": "Agent_2_Fraud_Investigator",
      "tools_used": ["tools you actually called"]
  }},
  "evidence": {{
      "context": [
          {{"source": "tool_name", "insight": "finding with its numbers"}}
      ]
  }}
}}
"""

    response = run_agent(label="Agent 2", prompt=prompt, tools=EVIDENCE_TOOLS)
    print(f"\n[Agent 2] Selesai (model {response.model}).")
    return response
