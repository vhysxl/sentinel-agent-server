"""
Sentinel Agent Server.

Model pemicu: transaksi masuk -> dianalisis sendiri di latar belakang. Pengguna
tidak pernah menunggu, dan tidak pernah menekan tombol "jalankan".

Yang menahan biaya: dua paruh pipeline ini beda ongkosnya jauh.

    deteksi (Python)  ~80 ms per transaksi, deterministik, tanpa kuota
    LLM (3 agen)      ~15 detik, hanya untuk yang jadi kandidat

Pada data seed: 68 transaksi -> 7 kandidat. 61 sisanya berhenti di Python dan
tidak pernah menyentuh LLM sama sekali.
"""
import concurrent.futures
import json
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, text

from app.agents.agent1 import run_financial_investigator
from app.agents.agent2 import run_fraud_investigator
from app.agents.agent3 import run_evidence_reviewer
from app.agents.ask import ask_sentinel
from app.agents.llm import pinned_model
from app.core.config import WIB
from app.core.security import require_internal_key
from app.core.constants import (
    RESOLUTIONS,
    RISK_ACTION,
    RISK_LABEL,
    RISK_ORDER,
    STATUS_CLEAN,
    STATUS_FAILED,
    STATUS_FLAGGED,
)
from app.db.models import Finding, Transaction, TransactionAnalysis
from app.db.session import SessionLocal
from app.engine import detectors
from app.engine.detectors import AGENT_1, AGENT_2
from app.engine.scoring import calculate_base_score, finalize
from app.tools.financial import get_sales_trend, get_transaction_details

app = FastAPI(
    title="Sentinel Agent Server",
    description="Multi-Agent AI Financial Analyst",
    version="3.0.0",
)

# Didaftarkan SEBELUM CORS supaya CORS berada di lapisan luar: preflight OPTIONS
# dijawab CORSMiddleware dan tidak pernah sampai ke pemeriksaan kunci.
app.middleware("http")(require_internal_key)

# allow_origins tetap "*" karena tidak ada browser yang boleh memanggil layanan
# ini sama sekali — yang menghalanginya sekarang kunci di atas, bukan CORS.
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


class AskRequest(BaseModel):
    question: str


class ResolveRequest(BaseModel):
    resolution: str
    note: str | None = None
    resolved_by: int | None = None


@app.get("/")
def read_root():
    return {"message": "Sentinel Agent Server is running"}


# ---------------------------------------------------------------------------
# Utilitas
# ---------------------------------------------------------------------------

def parse_agent_json(content: str) -> dict:
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0].strip()
    return json.loads(content)


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _safe_agent(future) -> dict:
    """Kegagalan satu agen tidak boleh mematikan pipeline."""
    try:
        return parse_agent_json(future.result().choices[0].message.content)
    except Exception as e:
        return {"_failed": True, "error": str(e)}


def _safe_agent_call(fn, *args) -> dict:
    try:
        return parse_agent_json(fn(*args).choices[0].message.content)
    except Exception as e:
        return {"_failed": True, "error": str(e)}


def _fallback_narrative(base_scoring: dict) -> str:
    """
    Narasi cadangan saat LLM gagal, dirakit dari fakta Python.

    Inilah yang membuat temuan tetap terbit dan tetap sah ketika kuota habis —
    sudah terbukti: duplikasi faktur tetap mencapai Critical dengan ketiga agen
    mati.
    """
    lines = [t["narrative"] for t in base_scoring.get("objective_triggers", [])]
    return " ".join(lines) or "Tidak ada narasi yang dapat dihasilkan."


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


def build_facts(db, transaction_id: int, triggers: list, owner: str) -> dict:
    """
    Merakit paket fakta untuk seorang detektif.

    Agen hanya menerima trigger MILIKNYA (D3), supaya tidak ada agen yang
    menarasikan fakta milik domain lain lalu tampak berselisih dengan rekannya.

    `revenue_context` dihitung Python dan disisipkan langsung. Alasannya konkret:
    saat tool ini masih dipanggil sendiri oleh agen, Agent 1 dan Agent 3
    sama-sama melaporkan "revenue turun 12%" untuk bulan yang sebenarnya NAIK
    43,39%. Angka deterministik tidak boleh melewati pembacaan LLM.
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


def group_ids_of(transaction_id: int, triggers: list) -> list[int]:
    """
    Seluruh transaksi yang terlibat dalam pelanggaran ini.

    Diambil dari `detail.transaction_ids` milik trigger itu sendiri — tiga
    pembayaran split payment, atau dua pembayaran atas faktur yang sama. Bukan
    ditebak dari kesamaan (vendor, nominal, tanggal).
    """
    ids = {transaction_id}
    for t in triggers:
        ids.update(t.detail.get("transaction_ids", []))
    return sorted(ids)


# ---------------------------------------------------------------------------
# Persistensi
# ---------------------------------------------------------------------------

def _record_analysis(db, transaction_id: int, status: str, error: str | None = None):
    """
    Menandai bahwa transaksi ini sudah diperiksa, dan hasilnya apa.

    Tanpa ini, "tidak ada di findings" berarti dua hal sekaligus: sudah
    diperiksa dan bersih, atau pemeriksaannya gagal.
    """
    row = db.get(TransactionAnalysis, transaction_id)
    if row:
        row.status, row.error = status, error
        row.analyzed_at = func.now()
    else:
        db.add(TransactionAnalysis(transaction_id=transaction_id,
                                   status=status, error=error))
    db.commit()


def _split_payload(base_scoring: dict, scoring: dict, json_a1: dict,
                   json_a2: dict, json_a3: dict) -> tuple[dict, dict]:
    """
    Memisahkan bukti (Python) dari analisis (LLM).

    Keduanya punya pembaca dan umur berbeda: `evidence` adalah yang ditelusuri
    auditor dan tetap sah walau LLM gagal; `analysis` adalah cara AI menyimpulkan.
    """
    evidence = {
        "triggers": base_scoring["objective_triggers"],
        "suppressed_triggers": base_scoring["suppressed_triggers"],
        "base_risk_score": base_scoring["base_risk_score"],
        "raw_score": base_scoring["raw_score"],
        "capped": base_scoring["capped"],
        "agent_scores": base_scoring["agent_scores"],
        "provenance": {
            "scored_by": "python_scoring_engine",
            "llm_model": pinned_model(),
            "llm_model_verifier": pinned_model("agent3"),
        },
    }
    analysis = {
        "agents": {"agent1": json_a1, "agent2": json_a2, "agent3": json_a3},
        "investigation": {
            "agent1_verdict": json_a1.get("verdict"),
            "agent1_confidence": json_a1.get("confidence"),
            "agent2_verdict": json_a2.get("verdict"),
            "agent2_confidence": json_a2.get("confidence"),
            "verdict_review": json_a3.get("verdict_review", {}),
        },
        "semantic": json_a3.get("evidence", {}).get("semantic", []),
        "context": (json_a1.get("evidence", {}).get("context", [])
                    + json_a2.get("evidence", {}).get("context", [])),
        "llm_semantic_adjustment": scoring["llm_semantic_adjustment"],
        "adjustment_reason": scoring["adjustment_reason"],
        "llm_review_failed": bool(json_a3.get("_failed")),
    }
    return json.loads(json.dumps(evidence, default=str)), \
        json.loads(json.dumps(analysis, default=str))


def _extend_finding(db, finding: Finding, group_ids: list[int], triggers: list):
    """
    Memperbarui temuan yang sudah ada saat grupnya bertambah anggota.

    LLM TIDAK dipanggil. Skor dan bukti dihitung ulang Python (murah,
    deterministik); narasi lama dipertahankan.

    Kenapa bukan membuat temuan baru: pembayaran ketiga split payment bukan
    pelanggaran baru — ia pelanggaran yang sama yang bertambah besar. Dua kartu
    di layar untuk satu pemecahan pembayaran hanya membingungkan.

    Kenapa bukan diabaikan: kalau dibiarkan, temuan tetap berbunyi "2 pembayaran,
    total 48 juta" padahal kenyataannya sudah 72 juta. Untuk alat audit, angka
    yang lebih kecil dari kenyataan itu berbahaya.
    """
    before = set(finding.related_transaction_ids or [])
    merged = sorted(before | set(group_ids))

    base_scoring = calculate_base_score(triggers)
    scoring = finalize(base_scoring, 0, "")
    evidence, _ = _split_payload(base_scoring, scoring, {}, {}, {})

    finding.related_transaction_ids = merged
    finding.risk_score = base_scoring["base_risk_score"]
    finding.risk_level = scoring["risk_level"]
    finding.evidence = evidence

    # `updated_at` HANYA diisi kalau cakupannya benar-benar bertambah. Ia menjadi
    # penanda "narasi mungkin tertinggal dari angka" di UI, jadi mengisinya
    # setiap kali fungsi ini dipanggil akan menandai temuan yang sebenarnya utuh.
    #
    # Ini terjadi pada kasus duplikasi: saat transaksi pertama dianalisis,
    # detektor sudah menemukan kedua barisnya sekaligus, sehingga analisis
    # transaksi kedua tidak menambah apa pun.
    if set(merged) != before:
        finding.updated_at = func.now()

    db.commit()
    return finding


# ---------------------------------------------------------------------------
# Inti: analisis satu transaksi
# ---------------------------------------------------------------------------

def analyze_transaction(db, transaction_id: int) -> dict:
    """
    Memeriksa satu transaksi. Inilah yang dipanggil saat transaksi masuk.

    LLM hanya disentuh kalau `is_candidate` bernilai True. Trigger amplifier
    (jam di luar kerja, baseline tidak cukup) tidak pernah cukup sendirian —
    itulah yang membuat 61 dari 68 transaksi berhenti gratis di Python.
    """
    try:
        txn = db.get(Transaction, transaction_id)
        if txn is None:
            _record_analysis(db, transaction_id, STATUS_FAILED, "transaksi tidak ditemukan")
            return {"status": STATUS_FAILED, "error": "transaksi tidak ditemukan"}

        if txn.type != "expense":
            _record_analysis(db, transaction_id, STATUS_CLEAN)
            return {"status": STATUS_CLEAN, "reason": "bukan pengeluaran"}

        triggers = detectors.run_all(db, txn)
        if not detectors.is_candidate(triggers):
            _record_analysis(db, transaction_id, STATUS_CLEAN)
            return {"status": STATUS_CLEAN, "triggers": len(triggers)}

        group = group_ids_of(transaction_id, triggers)

        # Pelanggaran ini sudah pernah dilaporkan? Pakai indeks GIN.
        existing = (db.query(Finding)
                    .filter(Finding.related_transaction_ids.overlap(group))
                    .first())
        if existing:
            _extend_finding(db, existing, group, triggers)
            _record_analysis(db, transaction_id, STATUS_FLAGGED)
            return {"status": "updated", "finding_id": existing.id,
                    "covered": existing.related_transaction_ids}

        finding = _create_finding(db, transaction_id, group, triggers)
        _record_analysis(db, transaction_id, STATUS_FLAGGED)
        return {"status": "created", "finding_id": finding.id,
                "risk_level": finding.risk_level, "risk_score": finding.risk_score}

    except Exception as e:
        db.rollback()
        _record_analysis(db, transaction_id, STATUS_FAILED, str(e)[:500])
        return {"status": STATUS_FAILED, "error": str(e)}


def _create_finding(db, transaction_id: int, group: list[int], triggers: list) -> Finding:
    """Temuan baru: skor dihitung Python dulu, baru LLM dipanggil untuk narasi."""
    base_scoring = calculate_base_score(triggers)

    facts_a1 = build_facts(db, transaction_id, triggers, AGENT_1)
    facts_a2 = build_facts(db, transaction_id, triggers, AGENT_2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_financial_investigator, transaction_id, facts_a1)
        f2 = executor.submit(run_fraud_investigator, transaction_id, facts_a2)
        json_a1, json_a2 = _safe_agent(f1), _safe_agent(f2)

    json_a3 = _safe_agent_call(run_evidence_reviewer, transaction_id,
                               json_a1, json_a2, base_scoring, facts_a1)

    a3 = json_a3.get("scoring", {}) or {}
    scoring = finalize(base_scoring, a3.get("llm_semantic_adjustment", 0),
                       a3.get("adjustment_reason", ""))
    evidence, analysis = _split_payload(base_scoring, scoring,
                                        json_a1, json_a2, json_a3)

    finding = Finding(
        transaction_id=transaction_id,
        related_transaction_ids=group,
        risk_score=scoring["final_risk_score"],
        risk_level=scoring["risk_level"],
        description=json_a3.get("finding") or _fallback_narrative(base_scoring),
        evidence=evidence,
        analysis=analysis,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.post("/api/transactions/{transaction_id}/analyze", status_code=202)
def analyze_one(transaction_id: int, background: BackgroundTasks):
    """
    Fire-and-forget. Dipanggil aplikasi setelah transaksi masuk dari bank.

    Kembali seketika; pekerjaannya berjalan setelah respons terkirim. Pengguna
    yang menyimpan transaksi tidak boleh menunggu 15 detik.
    """
    def job():
        db = SessionLocal()
        try:
            analyze_transaction(db, transaction_id)
        finally:
            db.close()

    background.add_task(job)
    return {"accepted": True, "transaction_id": transaction_id}


def _finding_row(f: Finding, detail: bool = False) -> dict:
    row = {
        "id": f.id,
        "transaction_id": f.transaction_id,
        "related_transaction_ids": f.related_transaction_ids,
        "risk_score": f.risk_score,
        "risk_level": f.risk_level,
        "risk_label": RISK_LABEL.get(f.risk_level, f.risk_level),
        "recommended_action": RISK_ACTION.get(f.risk_level, ""),
        "description": f.description,
        "resolution": f.resolution,
        "resolution_note": f.resolution_note,
        "resolved_by": f.resolved_by,
        "resolved_at": str(f.resolved_at) if f.resolved_at else None,
        "created_at": str(f.created_at),
        "updated_at": str(f.updated_at) if f.updated_at else None,
        # Narasi dirakit saat temuan dibuat; kalau grupnya bertambah setelah itu,
        # angkanya sudah diperbarui tetapi kalimatnya belum.
        "narrative_stale": f.updated_at is not None,
    }
    if detail:
        row["evidence"] = f.evidence
        row["analysis"] = f.analysis
    return row


@app.get("/api/findings")
def list_findings(status: str = "open", risk_level: str | None = None,
                  limit: int = 50):
    """
    Antrean kerja. `status`: open | resolved | all.

    Diurutkan berdasarkan keparahan lalu skor — yang perlu ditindak lebih dulu
    muncul lebih dulu.
    """
    db = SessionLocal()
    try:
        q = db.query(Finding)
        if status == "open":
            q = q.filter(Finding.resolution.is_(None))
        elif status == "resolved":
            q = q.filter(Finding.resolution.isnot(None))
        if risk_level:
            q = q.filter(Finding.risk_level == risk_level)

        rows = q.order_by(Finding.risk_score.desc()).limit(limit).all()
        rows.sort(key=lambda f: (-RISK_ORDER.get(f.risk_level, 0), -f.risk_score))
        return [_finding_row(f) for f in rows]
    finally:
        db.close()


@app.get("/api/findings/{finding_id}")
def get_finding(finding_id: int):
    db = SessionLocal()
    try:
        f = db.get(Finding, finding_id)
        if not f:
            return {"error": f"Temuan {finding_id} tidak ditemukan."}
        return _finding_row(f, detail=True)
    finally:
        db.close()


@app.patch("/api/findings/{finding_id}/resolve")
def resolve_finding(finding_id: int, request: ResolveRequest):
    """
    Menutup sebuah temuan dengan ALASAN, bukan sekadar centang.

    Kenapa alasannya wajib: kalau lead menutup sepuluh temuan z-score
    berturut-turut sebagai `justified`, itu sinyal ambangnya kekencangan.
    Boolean tidak memberi informasi itu.
    """
    if request.resolution not in RESOLUTIONS:
        return {"error": f"resolution harus salah satu dari {list(RESOLUTIONS)}"}

    db = SessionLocal()
    try:
        f = db.get(Finding, finding_id)
        if not f:
            return {"error": f"Temuan {finding_id} tidak ditemukan."}
        f.resolution = request.resolution
        f.resolution_note = request.note
        f.resolved_by = request.resolved_by
        f.resolved_at = func.now()
        db.commit()
        db.refresh(f)
        return _finding_row(f)
    finally:
        db.close()


@app.get("/api/summary")
def summary():
    """
    Baris pertama yang dilihat orang keuangan saat membuka tab.

    "aman" hanya bisa dihitung karena ada transaction_analysis — tanpa itu,
    sudah-diperiksa-dan-bersih tidak bisa dibedakan dari belum-pernah-dilihat.
    """
    db = SessionLocal()
    try:
        counts = dict(db.query(TransactionAnalysis.status,
                               func.count()).group_by(TransactionAnalysis.status).all())
        open_by_level = dict(db.query(Finding.risk_level, func.count())
                             .filter(Finding.resolution.is_(None))
                             .group_by(Finding.risk_level).all())
        total_tx = db.query(func.count(Transaction.id)).filter(
            Transaction.type == "expense").scalar() or 0
        analyzed = sum(counts.values())

        return {
            "perlu_tindakan": open_by_level.get("critical", 0),
            "perlu_ditinjau": open_by_level.get("high", 0) + open_by_level.get("medium", 0),
            "aman": counts.get(STATUS_CLEAN, 0),
            "gagal": counts.get(STATUS_FAILED, 0),
            "belum_diperiksa": max(total_tx - analyzed, 0),
            "total_transaksi": total_tx,
            "per_tingkat": open_by_level,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Ask Sentinel
# ---------------------------------------------------------------------------

@app.post("/api/ask")
def ask(request: AskRequest):
    """
    Tanya jawab keuangan. Sekali tanya, sekali jawab — tanpa riwayat percakapan.

    Model tidak pernah menerima baris transaksi, dan tidak pernah memanggil tool
    sendiri: ia memilih tool + periode, Python menjalankannya, lalu model
    menarasikan angka yang sudah jadi. `figures` dirakit Python dari hasil tool,
    bukan oleh model.
    """
    question = (request.question or "").strip()
    if not question:
        return {"error": "Pertanyaan kosong."}

    db = SessionLocal()
    try:
        span = db.execute(text("""
            SELECT to_char(MIN(created_at AT TIME ZONE 'Asia/Jakarta'), 'YYYY-MM'),
                   to_char(MAX(created_at AT TIME ZONE 'Asia/Jakarta'), 'YYYY-MM')
            FROM transactions
        """)).fetchone()
        data_range = (f"{span[0]} s/d {span[1]}" if span and span[0]
                      else "belum ada transaksi")
    finally:
        db.close()

    result = ask_sentinel(question, datetime.now(WIB).strftime("%d-%m-%Y"), data_range)
    unsourced = result.get("unsourced_figures") or []
    return {
        "question": question, "data_range": data_range, **result,
        "warning": ("Ada angka di jawaban yang tidak berasal dari hasil query."
                    if unsourced else None),
    }


# ---------------------------------------------------------------------------
# Backfill: menganalisis rentang tanggal sekaligus
# ---------------------------------------------------------------------------

@app.post("/api/analyze")
async def run_backfill(request: AnalyzeRequest):
    """
    Memeriksa seluruh transaksi dalam rentang, satu per satu, dengan progres SSE.

    Dipertahankan untuk seed dan demo. Ia memanggil `analyze_transaction` yang
    sama persis dengan jalur otomatis — supaya kedua jalur tidak pernah bisa
    memberi hasil berbeda.
    """
    def event_stream():
        db = SessionLocal()
        try:
            ids = [r[0] for r in db.execute(text("""
                SELECT id FROM transactions
                WHERE type = 'expense'
                  AND (created_at AT TIME ZONE 'Asia/Jakarta')::date
                      BETWEEN CAST(:start AS DATE) AND CAST(:end AS DATE)
                ORDER BY created_at, id
            """), {"start": request.start_date, "end": request.end_date})]

            yield sse_event({"status": "info",
                             "message": f"{len(ids)} transaksi akan diperiksa."})

            created = updated = clean = failed = 0
            for i, tid in enumerate(ids, 1):
                result = analyze_transaction(db, tid)
                st = result.get("status")
                if st == "created":
                    created += 1
                    yield sse_event({
                        "status": "progress", "node": "finding",
                        "message": (f"[{i}/{len(ids)}] Temuan baru untuk transaksi {tid} "
                                    f"— {result.get('risk_level')} "
                                    f"({result.get('risk_score')})"),
                    })
                elif st == "updated":
                    updated += 1
                    yield sse_event({
                        "status": "progress", "node": "finding",
                        "message": (f"[{i}/{len(ids)}] Transaksi {tid} bergabung ke temuan "
                                    f"#{result.get('finding_id')} — tanpa LLM"),
                    })
                elif st == STATUS_FAILED:
                    failed += 1
                    yield sse_event({"status": "progress", "node": "error",
                                     "message": f"[{i}/{len(ids)}] Transaksi {tid} GAGAL"})
                else:
                    clean += 1

            yield sse_event({
                "status": "complete",
                "summary": {"diperiksa": len(ids), "temuan_baru": created,
                            "temuan_diperbarui": updated, "bersih": clean,
                            "gagal": failed},
                "findings": [_finding_row(f) for f in
                             db.query(Finding).filter(Finding.resolution.is_(None))
                             .order_by(Finding.risk_score.desc()).all()],
            })
        except Exception as e:
            yield sse_event({"status": "error", "message": str(e)})
        finally:
            db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
