from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, text

from app.db.session import SessionLocal
from app.db.models import AnalysisRun, Finding, Transaction
from app.agents.agent1 import run_financial_investigator
from app.agents.agent2 import run_fraud_investigator
from app.agents.agent3 import run_evidence_reviewer
from app.agents.llm import pinned_model
from app.engine import detectors
from app.engine.detectors import AGENT_1, AGENT_2
from app.engine.scoring import calculate_base_score, finalize
from app.tools.financial import get_sales_trend, get_transaction_details

import json
import concurrent.futures

app = FastAPI(
    title="Sentinel Agent Server",
    description="Multi-Agent AI Financial Analyst",
    version="2.0.0"
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


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def extract_candidates(db, start_date: str, end_date: str) -> dict[int, list]:
    """
    Menjalankan seluruh detektor atas setiap transaksi expense dalam rentang.

    Deteksi dilakukan di Python, bukan SQL, karena detektor yang sama juga dipakai
    scoring engine — sehingga mustahil ekstraksi kandidat dan penilaian memakai
    definisi yang berbeda. Sebelumnya "transaksi ganda" punya tiga definisi yang
    saling bertentangan di tiga berkas.

    Skala target proyek ini ratusan transaksi (SCOPE 1.2), bukan jutaan.
    """
    # Rentang dibandingkan sebagai TANGGAL WIB, dan end_date bersifat INKLUSIF.
    #
    # Perbandingan langsung `created_at <= :end_date` salah dua kali:
    # (1) string tanggal dikonversi menjadi 00:00:00, sehingga rentang
    #     "12 Juni sampai 12 Juni" membuang transaksi pukul 14:20 pada hari itu
    #     dan mengembalikan nol temuan;
    # (2) konversinya memakai timezone sesi (GMT di server ini), bukan WIB,
    #     sehingga batas harinya bergeser 7 jam.
    ids = [r[0] for r in db.execute(text("""
        SELECT id FROM transactions
        WHERE type = 'expense'
          AND (created_at AT TIME ZONE 'Asia/Jakarta')::date
              BETWEEN CAST(:start_date AS DATE) AND CAST(:end_date AS DATE)
        ORDER BY id
    """), {"start_date": start_date, "end_date": end_date})]

    rows = (db.query(Transaction).filter(Transaction.id.in_(ids))
            .order_by(Transaction.id).all()) if ids else []

    candidates: dict[int, list] = {}
    for txn in rows:
        triggers = detectors.run_all(db, txn)
        if detectors.is_candidate(triggers):
            candidates[txn.id] = triggers
    return candidates


def build_groups(candidates: dict[int, list]) -> list[dict]:
    """
    Menggabungkan transaksi yang saling terkait menjadi satu temuan.

    Tiga transaksi split payment adalah SATU pelanggaran, bukan tiga; dua
    pembayaran atas faktur yang sama adalah SATU duplikasi. Keterkaitan dibaca
    dari `detail.transaction_ids` milik trigger itu sendiri, bukan ditebak dari
    kesamaan (vendor, nominal, tanggal) seperti sebelumnya — cara lama bergantung
    pada tanggal kalender, dan tanggal kalender bergantung pada zona waktu.
    """
    parent: dict[int, int] = {tid: tid for tid in candidates}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for tid, triggers in candidates.items():
        for trigger in triggers:
            for related in trigger.detail.get("transaction_ids", []):
                if related in parent:
                    union(tid, related)

    grouped: dict[int, list[int]] = {}
    for tid in candidates:
        grouped.setdefault(find(tid), []).append(tid)

    return [
        {"transaction_id": min(ids), "related_transaction_ids": sorted(ids)}
        for _, ids in sorted(grouped.items())
    ]


def build_facts(db, transaction_id: int, triggers: list, owner: str) -> dict:
    """
    Merakit paket fakta untuk seorang detektif.

    Agen hanya menerima trigger MILIKNYA (D3). Agent 1 tidak melihat trigger
    fraud dan sebaliknya, supaya tidak ada agen yang menarasikan fakta milik
    domain lain lalu tampak berselisih dengan rekannya di layar.

    `revenue_context` dihitung Python dan disisipkan langsung ke prompt.
    Alasannya konkret: saat tool ini masih harus dipanggil sendiri oleh agen,
    Agent 1 dan Agent 3 sama-sama melaporkan "revenue turun 12%" untuk bulan
    yang sebenarnya NAIK 43,39% — keduanya mencantumkan get_sales_trend di
    tools_used, lalu menyebut angka yang tidak pernah dikembalikan tool itu.
    Agent 3 kemudian menolak Agent 2 yang benar dan membalik hasilnya.

    Angka deterministik tidak boleh melewati pembacaan LLM. Kalau Python bisa
    menghitungnya, Python yang menaruhnya.
    """
    transaction = get_transaction_details(transaction_id)
    month = str(transaction.get("recorded_at", ""))[:7]

    return {
        "transaction": transaction,
        "revenue_context": get_sales_trend(month) if month else None,
        "triggers": [
            {"code": t.code, "points": t.points,
             "narrative": t.narrative, "detail": t.detail}
            for t in triggers if t.owner == owner
        ],
    }


def persist_run(db, start_date: str, end_date: str) -> int:
    run = AnalysisRun(start_date=start_date, end_date=end_date, status="running")
    db.add(run)
    db.commit()
    return run.id


def persist_finding(db, run_id: int, finding: dict) -> None:
    """
    Menyimpan temuan lengkap dengan provenance-nya.

    Inilah alasan proyek memilih PostgreSQL/JSONB. Tanpa ini hasil hanya
    di-stream ke browser dan hilang saat refresh, sehingga cerita "auditor
    menelusuri temuan lama" tidak mungkin.
    """
    scoring = finding.get("scoring", {})
    db.add(Finding(
        run_id=run_id,
        transaction_id=finding["transaction_id"],
        related_transaction_ids=finding.get("related_transaction_ids", []),
        final_risk_score=scoring.get("final_risk_score", 0),
        risk_level=scoring.get("risk_level", "Unknown"),
        payload=json.loads(json.dumps(finding, default=str)),
    ))
    db.commit()


def merge_group_triggers(candidates: dict[int, list], ids: list[int]) -> list:
    """
    Menyatukan trigger seluruh anggota grup, membuang duplikasi.

    Tiga transaksi split payment masing-masing memicu split_payment yang sama;
    kalau dijumlahkan begitu saja, satu pelanggaran dihitung tiga kali.
    """
    merged, seen = [], set()
    for tid in ids:
        for trigger in candidates.get(tid, []):
            key = (trigger.code, json.dumps(trigger.detail, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            merged.append(trigger)
    return merged


@app.post("/api/analyze")
async def run_analysis(request: AnalyzeRequest):
    """
    Dieksekusi saat user menekan Analyze di UI. Memakai SSE agar UI dapat
    menampilkan progres.

    Urutannya penting: FAKTA dan SKOR dihitung Python lebih dulu, baru LLM
    dipanggil. LLM tidak pernah menghasilkan angka — ia hanya menarasikan fakta
    dan boleh menggeser skor maksimal +/-20 dengan alasan tertulis.
    """
    def event_stream():
        db = SessionLocal()
        try:
            yield sse_event({
                "status": "progress",
                "node": "sql_extraction",
                "message": "Menjalankan detektor deterministik atas seluruh transaksi...",
            })

            run_id = persist_run(db, request.start_date, request.end_date)
            candidates = extract_candidates(db, request.start_date, request.end_date)
            if not candidates:
                _close_run(db, run_id, "complete")
                yield sse_event({"status": "complete", "run_id": run_id,
                                 "findings": [], "message": "No anomalies found"})
                return

            groups = build_groups(candidates)
            yield sse_event({
                "status": "info",
                "message": (f"Found {len(candidates)} candidate transactions across "
                            f"{len(groups)} finding groups. Starting analysis..."),
            })

            findings = []
            for group in groups:
                tid = group["transaction_id"]
                related = group["related_transaction_ids"]
                try:
                    # --- 1. SKOR OBJEKTIF (Python murni, tanpa LLM) -----------
                    yield sse_event({
                        "status": "progress", "node": "scoring",
                        "message": f"Menghitung skor objektif untuk Transaction ID {tid}...",
                    })
                    triggers = merge_group_triggers(candidates, related)
                    base_scoring = calculate_base_score(triggers)

                    # --- 2. DETEKTIF Agent 1 & 2 (paralel, tidak mempengaruhi skor)
                    # Masing-masing hanya menerima trigger MILIKNYA (D3).
                    yield sse_event({
                        "status": "progress", "node": "agent_1_2",
                        "message": f"Running Agent 1 and Agent 2 in parallel for Transaction ID {tid}...",
                    })
                    facts_a1 = build_facts(db, tid, triggers, AGENT_1)
                    facts_a2 = build_facts(db, tid, triggers, AGENT_2)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        f1 = executor.submit(run_financial_investigator, tid, facts_a1)
                        f2 = executor.submit(run_fraud_investigator, tid, facts_a2)
                        json_a1 = _safe_agent(f1)
                        json_a2 = _safe_agent(f2)

                    # --- 3. VERIFIKATOR Agent 3 (satu-satunya yang menggeser skor)
                    yield sse_event({
                        "status": "progress", "node": "agent_3",
                        "message": f"Running Agent 3 for Evidence Review on Transaction ID {tid}...",
                    })
                    json_a3 = _safe_agent_call(
                        run_evidence_reviewer, tid, json_a1, json_a2, base_scoring,
                        facts_a1)

                    a3_scoring = json_a3.get("scoring", {}) if json_a3 else {}
                    scoring = finalize(
                        base_scoring,
                        a3_scoring.get("llm_semantic_adjustment", 0),
                        a3_scoring.get("adjustment_reason", ""),
                    )
                    if json_a3.get("_failed"):
                        scoring["llm_review_failed"] = True

                    tools_used = sorted({
                        *json_a1.get("provenance", {}).get("tools_used", []),
                        *json_a2.get("provenance", {}).get("tools_used", []),
                        *json_a3.get("provenance", {}).get("tools_used", []),
                    })

                    finding = {
                        "transaction_id": tid,
                        "related_transaction_ids": related,
                        "finding": (json_a3.get("finding")
                                    or _fallback_narrative(base_scoring)),
                        # Putusan kedua detektif disimpan apa adanya, termasuk
                        # ketika Agent 3 menolaknya. Auditor harus bisa melihat
                        # di mana ketiganya berbeda pendapat.
                        "investigation": {
                            "agent1_verdict": json_a1.get("verdict"),
                            "agent1_confidence": json_a1.get("confidence"),
                            "agent2_verdict": json_a2.get("verdict"),
                            "agent2_confidence": json_a2.get("confidence"),
                            "verdict_review": json_a3.get("verdict_review", {}),
                        },
                        "provenance": {
                            "generated_by": "Agent_3_Final_Review",
                            "tools_used": tools_used,
                            "scored_by": "python_scoring_engine",
                            "llm_model": pinned_model(),
                            "llm_model_verifier": pinned_model("agent3"),
                        },
                        "evidence": {
                            # Bukti objektif berasal dari detektor Python, bukan
                            # dari JSON yang ditulis agen.
                            "objective": base_scoring["objective_triggers"],
                            "context": (json_a1.get("evidence", {}).get("context", [])
                                        + json_a2.get("evidence", {}).get("context", [])),
                            "semantic": json_a3.get("evidence", {}).get("semantic", []),
                        },
                        "agent_results": {
                            "agent1": json_a1, "agent2": json_a2, "agent3": json_a3,
                        },
                        "scoring": scoring,
                    }
                    persist_finding(db, run_id, finding)
                    findings.append(finding)

                except Exception as e:
                    findings.append({
                        "transaction_id": tid,
                        "related_transaction_ids": related,
                        "error": "Pipeline failed for transaction",
                        "raw_response": str(e),
                    })

            _close_run(db, run_id, "complete")
            yield sse_event({"status": "complete", "run_id": run_id,
                             "findings": findings})

        except Exception as e:
            try:
                _close_run(db, run_id, "error")
            except Exception:
                pass
            yield sse_event({"status": "error", "message": str(e)})
        finally:
            db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _safe_agent(future) -> dict:
    """Menjalankan agen; kegagalan LLM tidak boleh mematikan pipeline."""
    try:
        return parse_agent_json(future.result().choices[0].message.content)
    except Exception as e:
        return {"_failed": True, "error": str(e)}


def _safe_agent_call(fn, *args) -> dict:
    try:
        return parse_agent_json(fn(*args).choices[0].message.content)
    except Exception as e:
        # Skor objektif tetap diterbitkan; adjustment jadi 0.
        return {"_failed": True, "error": str(e)}


def _fallback_narrative(base_scoring: dict) -> str:
    """Narasi cadangan kalau LLM gagal, dirakit dari fakta Python."""
    lines = [t["narrative"] for t in base_scoring.get("objective_triggers", [])]
    return " ".join(lines) or "Tidak ada narasi yang dapat dihasilkan."


def _close_run(db, run_id: int, status: str) -> None:
    db.query(AnalysisRun).filter(AnalysisRun.id == run_id).update(
        {"status": status, "finished_at": func.now()})
    db.commit()


@app.get("/api/runs")
def list_runs(limit: int = 20):
    """Daftar analisis terdahulu, terbaru lebih dulu."""
    db = SessionLocal()
    try:
        runs = db.query(AnalysisRun).order_by(AnalysisRun.id.desc()).limit(limit).all()
        counts = dict(db.query(Finding.run_id, func.count(Finding.id))
                      .group_by(Finding.run_id).all())
        return [{
            "id": r.id,
            "start_date": str(r.start_date),
            "end_date": str(r.end_date),
            "started_at": str(r.started_at),
            "finished_at": str(r.finished_at) if r.finished_at else None,
            "status": r.status,
            "finding_count": counts.get(r.id, 0),
        } for r in runs]
    finally:
        db.close()


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    """
    Membuka kembali sebuah analisis lengkap dengan provenance-nya.

    Inilah yang membuat klaim "auditor dapat menelusuri temuan" benar: hasil
    tetap ada setelah browser di-refresh maupun server di-restart.
    """
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            return {"error": f"Run {run_id} tidak ditemukan."}
        findings = (db.query(Finding).filter(Finding.run_id == run_id)
                    .order_by(Finding.final_risk_score.desc()).all())
        return {
            "id": run.id,
            "start_date": str(run.start_date),
            "end_date": str(run.end_date),
            "started_at": str(run.started_at),
            "finished_at": str(run.finished_at) if run.finished_at else None,
            "status": run.status,
            "findings": [f.payload for f in findings],
        }
    finally:
        db.close()
