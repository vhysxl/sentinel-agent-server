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
You are Agent 1: Financial Analytics Investigator.

CANDIDATE you must examine (ID {transaction_id}):
{json.dumps(transaction, indent=2, ensure_ascii=False, default=str)}
Transaction month: {month}

REVENUE CONTEXT for {month} — COMPUTED BY PYTHON, not by you:
{revenue_block}
This number is FINAL. Do not recompute it, do not cite a different revenue figure.
If you cite a revenue number different from the one above, that is wrong.

OBJECTIVE FACTS already computed by the Python statistics engine.
These numbers are final and already form the basis of the score:
{findings_block}

This candidate was picked by a detector deliberately built loose — so nothing
slips through. As a consequence, some candidates turn out to be legitimate.
YOUR JOB IS TO NARROW IT DOWN.

STEPS:
1. Understand the objective numbers above.
2. Gather evidence with the tools. Do not conclude before calling a tool:
   The revenue context is ALREADY given above — don't look it up again. A cost
   spike that tracks revenue growth is a legitimate expense.
   - `get_monthly_expense_trend` — is spending rising broadly, or is this
     transaction an outlier on its own?
   - `compare_category_baseline` — how does it compare to similar categories?
   - `get_vendor_transaction_history` — what's this vendor's usual pattern?
3. Decide the `verdict`:
   - "explainable" — there is a business explanation SUPPORTED BY NUMBERS from
     a tool. You MUST cite the numbers. Without numbers, don't pick this.
   - "risk"        — no explanation exists, or a claim in the transaction
     description is actually contradicted by the data.
   - "uncertain"   — the evidence isn't enough to lean either way. This is a
     legitimate answer, and better than guessing.


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
- DO NOT recompute or dispute the objective numbers. That is not up for opinion.
- DO NOT invent metrics that aren't in the trigger list.
- If the status is `insufficient_baseline`, it means the amount CANNOT be
  assessed statistically. That does not mean it's fine, and does not mean the
  score is zero.
- Your verdict is a proposal for Agent 3, not the final decision.

Reply with ONLY valid JSON, no markdown:
{{
  "verdict": "explainable | risk | uncertain",
  "confidence": "low | medium | high",
  "finding": "English narrative for the auditor. Cite concrete numbers from the tools.",
  "provenance": {{
      "generated_by": "Agent_1_Financial_Analytics",
      "tools_used": ["tools you actually called"]
  }},
  "evidence": {{
      "context": [
          {{"source": "tool_name", "insight": "finding with its numbers"}}
      ]
  }}
}}
"""

    response = run_agent(label="Agent 1", prompt=prompt, tools=EVIDENCE_TOOLS)
    print(f"\n[Agent 1] Selesai (model {response.model}).")
    return response
