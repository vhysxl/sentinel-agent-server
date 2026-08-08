from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from app.db.session import SessionLocal
from app.agents.agent1 import run_financial_investigator
from app.agents.agent2 import run_fraud_investigator
from app.agents.agent3 import run_evidence_reviewer
from app.engine.scoring import calculate_agent_score, aggregate_agent_scores
import json
import concurrent.futures

app = FastAPI(
    title="Sentinel Agent Server",
    description="Multi-Agent AI Financial Analyst",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    start_date: str
    end_date: str


@app.get("/")
def read_root():
    return {"message": "Sentinel Agent Server is running"}


def parse_agent_json(content: str) -> dict:
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0].strip()
    return json.loads(content)


def risk_decision(final_score: int) -> tuple[str, str]:
    if final_score < 40:
        return "Low Risk", "Transaksi wajar. Otomatis disetujui (No Action)."
    if final_score < 60:
        return "Medium Risk", "Anomali ringan. Dicatat ke dalam audit report bulanan."
    if final_score < 80:
        return "High Risk", "Indikasi kecurangan. Butuh verifikasi manual (Manual Review)."
    return "Critical Risk", "Indikasi fraud fatal. Eskalasi darurat ke Manajer/CFO."


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_candidate_groups(db, candidate_ids: list[int]) -> list[dict]:
    rows = db.execute(text("""
        SELECT id, vendor_id, amount, CAST(transaction_date AS DATE) AS tx_date
        FROM transactions
        WHERE id = ANY(:candidate_ids)
        ORDER BY id
    """), {"candidate_ids": candidate_ids}).fetchall()

    grouped = {}
    for row in rows:
        key = (row.vendor_id, str(row.amount), str(row.tx_date))
        grouped.setdefault(key, []).append(row.id)

    candidate_groups = []
    for ids in grouped.values():
        related_ids = sorted(ids)
        candidate_groups.append({
            "transaction_id": related_ids[0],
            "related_transaction_ids": related_ids,
        })

    return sorted(candidate_groups, key=lambda item: item["transaction_id"])


def has_safe_vendor_evidence(agent_finding: dict) -> bool:
    for item in agent_finding.get("evidence", {}).get("objective", []):
        if str(item.get("metric", "")).lower() not in ["vendor_history", "vendor_status"]:
            continue
        value = str(item.get("value", "")).lower()
        status = str(item.get("status", "")).lower()
        if any(flag in value or flag in status for flag in ["established", "active", "aman", "normal", "terpercaya"]):
            return True
    return False


def suppress_agent_vendor_trigger(agent_score: dict) -> dict:
    triggers = agent_score.get("objective_triggers", [])
    filtered_triggers = [trigger for trigger in triggers if "vendor baru/berisiko" not in trigger.lower()]
    if len(filtered_triggers) == len(triggers):
        return agent_score

    recomputed_score = 0
    for trigger in filtered_triggers:
        if "(+" not in trigger:
            continue
        try:
            recomputed_score += int(trigger.rsplit("(+", 1)[1].split(")", 1)[0])
        except (ValueError, IndexError):
            pass

    return {
        **agent_score,
        "base_score": min(recomputed_score, 80),
        "objective_triggers": filtered_triggers,
    }


@app.post("/api/analyze")
async def run_analysis(request: AnalyzeRequest):
    """
    Endpoint ini dieksekusi saat user menekan tombol Analyze di UI.
    Menggunakan Server-Sent Events (SSE) agar UI bisa menampilkan progress real-time.
    """
    def event_stream():
        db = SessionLocal()
        try:
            yield sse_event({
                "status": "progress",
                "node": "sql_extraction",
                "message": "Extracting candidates via SQL...",
            })

            sql = text("""
                WITH stats AS (
                    SELECT vendor_id, AVG(amount) as mean, COALESCE(NULLIF(STDDEV(amount), 0), 1) as std
                    FROM transactions
                    WHERE type = 'expense'
                    GROUP BY vendor_id
                ),
                financial_candidates AS (
                    SELECT t.id
                    FROM transactions t
                    JOIN stats s ON t.vendor_id = s.vendor_id
                    WHERE t.type = 'expense'
                      AND t.transaction_date >= :start_date
                      AND t.transaction_date <= :end_date
                      AND (t.amount - s.mean) / s.std > 3
                ),
                fraud_candidates AS (
                    SELECT t1.id
                    FROM transactions t1
                    JOIN transactions t2 ON t1.vendor_id = t2.vendor_id
                        AND t1.amount = t2.amount
                        AND t1.id != t2.id
                        AND CAST(t1.transaction_date AS DATE) = CAST(t2.transaction_date AS DATE)
                    WHERE t1.type = 'expense'
                      AND t1.transaction_date >= :start_date
                      AND t1.transaction_date <= :end_date

                    UNION

                    SELECT t.id
                    FROM transactions t
                    JOIN vendors v ON t.vendor_id = v.id
                    WHERE t.type = 'expense'
                      AND t.transaction_date >= :start_date
                      AND t.transaction_date <= :end_date
                      AND v.status IN ('flagged', 'suspended', 'high_risk')
                )
                SELECT id FROM financial_candidates
                UNION
                SELECT id FROM fraud_candidates
            """)

            result = db.execute(sql, {"start_date": request.start_date, "end_date": request.end_date}).fetchall()
            candidate_ids = [row[0] for row in result]

            if not candidate_ids:
                yield sse_event({"status": "complete", "findings": [], "message": "No anomalies found"})
                return

            candidate_groups = build_candidate_groups(db, candidate_ids)

            yield sse_event({
                "status": "info",
                "message": f"Found {len(candidate_ids)} candidate transactions across {len(candidate_groups)} finding groups. Starting analysis...",
            })

            findings = []
            for group in candidate_groups:
                tid = group["transaction_id"]
                related_transaction_ids = group["related_transaction_ids"]
                try:
                    yield sse_event({
                        "status": "progress",
                        "node": "agent_1_2",
                        "message": f"Running Agent 1 and Agent 2 in parallel for Transaction ID {tid}...",
                    })

                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        future_a1 = executor.submit(run_financial_investigator, transaction_id=tid)
                        future_a2 = executor.submit(run_fraud_investigator, transaction_id=tid)
                        response_a1 = future_a1.result()
                        response_a2 = future_a2.result()

                    json_a1 = parse_agent_json(response_a1.choices[0].message.content)
                    json_a2 = parse_agent_json(response_a2.choices[0].message.content)

                    agent1_score = calculate_agent_score(json_a1, "Agent 1 Financial")
                    agent2_score = calculate_agent_score(json_a2, "Agent 2 Fraud")
                    if has_safe_vendor_evidence(json_a2):
                        agent1_score = suppress_agent_vendor_trigger(agent1_score)
                    json_a1["scoring"] = agent1_score
                    json_a2["scoring"] = agent2_score

                    base_scoring = aggregate_agent_scores([agent1_score, agent2_score])

                    yield sse_event({
                        "status": "progress",
                        "node": "scoring",
                        "message": f"Aggregating Agent 1 and Agent 2 scores for Transaction ID {tid}...",
                    })

                    yield sse_event({
                        "status": "progress",
                        "node": "agent_3",
                        "message": f"Running Agent 3 for Evidence Review on Transaction ID {tid}...",
                    })

                    response_a3 = run_evidence_reviewer(tid, json_a1, json_a2, base_scoring)
                    json_a3 = parse_agent_json(response_a3.choices[0].message.content)

                    semantic_evidence = json_a3.get("evidence", {}).get("semantic", [])
                    a3_scoring = json_a3.get("scoring", {})
                    semantic_adj = int(a3_scoring.get("llm_semantic_adjustment", 0) or 0)
                    semantic_adj = max(-20, min(20, semantic_adj))
                    adj_reason = a3_scoring.get("adjustment_reason", "")

                    final_score = base_scoring.get("base_risk_score", 0) + semantic_adj
                    final_score = max(0, min(100, final_score))
                    risk_level, recommendation = risk_decision(final_score)

                    json_a3["scoring"] = {
                        **a3_scoring,
                        "llm_semantic_adjustment": semantic_adj,
                        "adjustment_reason": adj_reason,
                    }

                    final_finding = json_a3.get("finding") or "Agent 3 completed review without a narrative finding."
                    tools_used = list(set(
                        json_a1.get("provenance", {}).get("tools_used", [])
                        + json_a2.get("provenance", {}).get("tools_used", [])
                        + json_a3.get("provenance", {}).get("tools_used", [])
                    ))

                    findings.append({
                        "transaction_id": tid,
                        "related_transaction_ids": related_transaction_ids,
                        "finding": final_finding,
                        "provenance": {
                            "generated_by": "Agent_3_Final_Review",
                            "tools_used": tools_used,
                        },
                        "evidence": {
                            "objective": json_a1.get("evidence", {}).get("objective", [])
                            + json_a2.get("evidence", {}).get("objective", []),
                            "semantic": semantic_evidence,
                        },
                        "agent_results": {
                            "agent1": json_a1,
                            "agent2": json_a2,
                            "agent3": json_a3,
                        },
                        "scoring": {
                            "base_risk_score": base_scoring.get("base_risk_score", 0),
                            "agent_scores": base_scoring.get("agent_scores", []),
                            "objective_triggers": base_scoring.get("objective_triggers", []),
                            "llm_semantic_adjustment": semantic_adj,
                            "adjustment_reason": adj_reason,
                            "final_risk_score": final_score,
                            "risk_level": risk_level,
                            "recommendation": recommendation,
                        },
                    })

                except json.JSONDecodeError as e:
                    findings.append({
                        "transaction_id": tid,
                        "related_transaction_ids": related_transaction_ids,
                        "error": "Failed to parse agent response to JSON",
                        "raw_response": str(e),
                    })
                except Exception as e:
                    findings.append({
                        "transaction_id": tid,
                        "related_transaction_ids": related_transaction_ids,
                        "error": "Pipeline failed for transaction",
                        "raw_response": str(e),
                    })

            yield sse_event({"status": "complete", "findings": findings})

        except Exception as e:
            yield sse_event({"status": "error", "message": str(e)})
        finally:
            db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
