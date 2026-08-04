from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List
from sqlalchemy import text
from app.db.session import SessionLocal
from app.agents.agent1 import run_financial_investigator
from app.agents.agent2 import run_fraud_investigator
from app.agents.agent3 import run_evidence_reviewer
from app.engine.scoring import calculate_base_score
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

@app.post("/api/analyze")
async def run_analysis(request: AnalyzeRequest):
    """
    Endpoint ini dieksekusi saat user menekan tombol 'Analyze' di UI.
    Menggunakan Server-Sent Events (SSE) agar UI bisa menampilkan progress real-time.
    """
    def event_stream():
        db = SessionLocal()
        try:
            yield f"data: {json.dumps({'status': 'progress', 'node': 'sql_extraction', 'message': 'Extracting candidates via SQL...'})}\n\n"
            
            # 1. SQL Extraction
            sql = text("""
                WITH stats AS (
                    SELECT vendor_id, AVG(amount) as mean, COALESCE(NULLIF(STDDEV(amount), 0), 1) as std
                    FROM transactions
                    WHERE type = 'expense'
                    GROUP BY vendor_id
                )
                SELECT t.id
                FROM transactions t
                JOIN stats s ON t.vendor_id = s.vendor_id
                WHERE t.type = 'expense' 
                  AND t.transaction_date >= :start_date 
                  AND t.transaction_date <= :end_date
                  AND (t.amount - s.mean) / s.std > 3
            """)
            
            result = db.execute(sql, {"start_date": request.start_date, "end_date": request.end_date}).fetchall()
            candidate_ids = [row[0] for row in result]
            
            if not candidate_ids:
                yield f"data: {json.dumps({'status': 'complete', 'findings': [], 'message': 'No anomalies found'})}\n\n"
                return
            
            yield f"data: {json.dumps({'status': 'info', 'message': f'Found {len(candidate_ids)} candidates. Starting analysis...'})}\n\n"
            
            findings = []
            # 2. Agent Orchestration
            for tid in candidate_ids:
                try:
                    yield f"data: {json.dumps({'status': 'progress', 'node': 'agent_1_2', 'message': f'Running Agent 1 and Agent 2 in parallel for Transaction ID {tid}...'})}\n\n"
                    
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future_a1 = executor.submit(run_financial_investigator, transaction_id=tid)
                        future_a2 = executor.submit(run_fraud_investigator, transaction_id=tid)
                        
                        response_a1 = future_a1.result()
                        response_a2 = future_a2.result()
                    
                    content_a1 = response_a1.choices[0].message.content
                    content_a2 = response_a2.choices[0].message.content
                    
                    if "```json" in content_a1:
                        content_a1 = content_a1.split("```json")[1].split("```")[0].strip()
                    elif "```" in content_a1:
                        content_a1 = content_a1.split("```")[1].split("```")[0].strip()
                    json_a1 = json.loads(content_a1)

                    if "```json" in content_a2:
                        content_a2 = content_a2.split("```json")[1].split("```")[0].strip()
                    elif "```" in content_a2:
                        content_a2 = content_a2.split("```")[1].split("```")[0].strip()
                    json_a2 = json.loads(content_a2)
                        
                    merged_finding = {
                        "finding": f"🔹 Agent 1 (Finansial):\n{json_a1.get('finding', '')}\n\n🔹 Agent 2 (Fraud):\n{json_a2.get('finding', '')}",
                        "provenance": {
                            "generated_by": "Agent_1_and_Agent_2_Parallel",
                            "tools_used": list(set(json_a1.get("provenance", {}).get("tools_used", []) + json_a2.get("provenance", {}).get("tools_used", [])))
                        },
                        "evidence": {
                            "objective": json_a1.get("evidence", {}).get("objective", []) + json_a2.get("evidence", {}).get("objective", [])
                        }
                    }
                    
                    yield f"data: {json.dumps({'status': 'progress', 'node': 'scoring', 'message': f'Calculating risk score for Transaction ID {tid}...'})}\n\n"
                    
                    # 3. Python Scoring Engine
                    scoring_result = calculate_base_score(merged_finding)
                    
                    yield f"data: {json.dumps({'status': 'progress', 'node': 'agent_3', 'message': f'Running Agent 3 for Evidence Review on Transaction ID {tid}...'})}\n\n"
                    
                    # 4. Agent 3: Evidence Review & Decision
                    response_a3 = run_evidence_reviewer(tid, json_a1, json_a2, scoring_result)
                    content_a3 = response_a3.choices[0].message.content
                    
                    if "```json" in content_a3:
                        content_a3 = content_a3.split("```json")[1].split("```")[0].strip()
                    elif "```" in content_a3:
                        content_a3 = content_a3.split("```")[1].split("```")[0].strip()
                    json_a3 = json.loads(content_a3)
                    
                    # Merge Agent 3 findings
                    merged_finding["finding"] += f"\n\n🔹 Agent 3 (Evidence Review):\n{json_a3.get('finding', '')}"
                    merged_finding["provenance"]["tools_used"] = list(set(merged_finding["provenance"].get("tools_used", []) + json_a3.get("provenance", {}).get("tools_used", [])))
                    
                    semantic_evidence = json_a3.get("evidence", {}).get("semantic", [])
                    merged_finding["evidence"]["semantic"] = semantic_evidence
                    
                    a3_scoring = json_a3.get("scoring", {})
                    semantic_adj = a3_scoring.get("llm_semantic_adjustment", 0)
                    adj_reason = a3_scoring.get("adjustment_reason", "")
                    
                    final_score = scoring_result.get("base_risk_score", 0) + semantic_adj
                    # bound final_score between 0 and 100
                    final_score = max(0, min(100, final_score))
                    
                    if final_score < 40:
                        risk_level = "Low Risk"
                        recommendation = "Transaksi wajar. Otomatis disetujui (No Action)."
                    elif final_score < 60:
                        risk_level = "Medium Risk"
                        recommendation = "Anomali ringan. Dicatat ke dalam audit report bulanan."
                    elif final_score < 80:
                        risk_level = "High Risk"
                        recommendation = "Indikasi kecurangan. Butuh verifikasi manual (Manual Review)."
                    else:
                        risk_level = "Critical Risk"
                        recommendation = "Indikasi fraud fatal. Eskalasi darurat ke Manajer/CFO."
                    
                    merged_finding["scoring"] = {
                        "base_risk_score": scoring_result.get("base_risk_score", 0),
                        "objective_triggers": scoring_result.get("objective_triggers", []),
                        "llm_semantic_adjustment": semantic_adj,
                        "adjustment_reason": adj_reason,
                        "final_risk_score": final_score,
                        "risk_level": risk_level,
                        "recommendation": recommendation
                    }
                    
                    findings.append(merged_finding)
                    
                except Exception as e:
                    findings.append({
                        "transaction_id": tid,
                        "error": "Failed to parse agent response to JSON",
                        "raw_response": str(e)
                    })
                    
            yield f"data: {json.dumps({'status': 'complete', 'findings': findings})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        finally:
            db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
