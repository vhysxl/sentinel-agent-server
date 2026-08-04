from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy import text
from app.db.session import SessionLocal
from app.agents.agent1 import run_financial_investigator
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
        for tid in candidate_ids:
            try:
                # LLM melakukan reasoning & tool calling
                agent_response = run_financial_investigator(transaction_id=tid)
                content = agent_response.choices[0].message.content
                
                # Ekstrak JSON dari string
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                    
                finding_json = json.loads(content)
                findings.append(finding_json)
                
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
