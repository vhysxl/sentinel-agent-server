from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy import text
from app.db.session import SessionLocal
from app.agents.agent1 import run_financial_investigator
from app.agents.agent2 import run_fraud_investigator
from app.engine.scoring import calculate_base_score
import json

app = FastAPI(
    title="Sentinel Agent Server",
    description="Multi-Agent AI Financial Analyst",
    version="1.0.0"
)

class AnalyzeRequest(BaseModel):
    start_date: str
    end_date: str

@app.get("/")
def read_root():
    return {"message": "Sentinel Agent Server is running"}

@app.post("/api/analyze")
def run_analysis(request: AnalyzeRequest):
    """
    Endpoint ini dieksekusi saat user menekan tombol 'Analyze' di UI.
    Alur kerja:
    1. SQL Filter mengekstrak kandidat anomali berdasarkan rentang waktu.
    2. LLM (Agent 1) menginvestigasi secara mendalam untuk setiap kandidat.
    """
    db = SessionLocal()
    try:
        # 1. SQL Extraction (Mencari transaksi dengan Z-Score ekstrem)
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
            return {
                "message": "No extreme statistical anomalies found in this time range via SQL.", 
                "candidates_checked": 0, 
                "findings": []
            }
            
        findings = []
        # 2. Agent Orchestration (Menjalankan LLM untuk setiap kandidat)
        import concurrent.futures
        for tid in candidate_ids:
            try:
                # LLM melakukan reasoning & tool calling secara paralel (Agent 1 & Agent 2)
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future_a1 = executor.submit(run_financial_investigator, transaction_id=tid)
                    future_a2 = executor.submit(run_fraud_investigator, transaction_id=tid)
                    
                    response_a1 = future_a1.result()
                    response_a2 = future_a2.result()
                
                content_a1 = response_a1.choices[0].message.content
                content_a2 = response_a2.choices[0].message.content
                
                # Ekstrak JSON dari string Agent 1
                if "```json" in content_a1:
                    content_a1 = content_a1.split("```json")[1].split("```")[0].strip()
                elif "```" in content_a1:
                    content_a1 = content_a1.split("```")[1].split("```")[0].strip()
                json_a1 = json.loads(content_a1)

                # Ekstrak JSON dari string Agent 2
                if "```json" in content_a2:
                    content_a2 = content_a2.split("```json")[1].split("```")[0].strip()
                elif "```" in content_a2:
                    content_a2 = content_a2.split("```")[1].split("```")[0].strip()
                json_a2 = json.loads(content_a2)
                    
                # Merge temuan dari Agent 1 dan Agent 2
                merged_finding = {
                    "finding": f"[Agent 1]: {json_a1.get('finding', '')} | [Agent 2]: {json_a2.get('finding', '')}",
                    "provenance": {
                        "generated_by": "Agent_1_and_Agent_2_Parallel",
                        "tools_used": list(set(json_a1.get("provenance", {}).get("tools_used", []) + json_a2.get("provenance", {}).get("tools_used", [])))
                    },
                    "evidence": {
                        "objective": json_a1.get("evidence", {}).get("objective", []) + json_a2.get("evidence", {}).get("objective", [])
                    }
                }
                
                # 3. Python Scoring Engine
                scoring_result = calculate_base_score(merged_finding)
                merged_finding["scoring"] = scoring_result
                
                findings.append(merged_finding)
                
            except Exception as e:
                # Jika LLM berhalusinasi atau gagal memformat JSON
                findings.append({
                    "transaction_id": tid,
                    "error": "Failed to parse agent response to JSON",
                    "raw_response": str(e)
                })
                
        return {
            "message": "Analysis complete",
            "candidates_checked": len(candidate_ids),
            "findings": findings
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
