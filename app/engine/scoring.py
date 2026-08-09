"""
Scoring engine.

DILARANG membaca apa pun yang ditulis LLM. Masukan satu-satunya adalah daftar
Trigger dari app.engine.detectors, yang poinnya ditetapkan Python berdasarkan
nilai kembalian query.

Versi sebelumnya mencocokkan substring seperti "baru", "flagged", "mencurigakan"
dari JSON karangan LLM, sehingga skor yang diklaim deterministik sebenarnya bisa
dihalusinasikan — dan kalau LLM menulis "zscore" alih-alih "z_score", skor diam-
diam menjadi 0 tanpa ada yang tahu.

LLM hanya boleh mempengaruhi dua hal: narasi, dan llm_semantic_adjustment (+/-20).
"""
from app.core.constants import (
    RISK_ACTION,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)
from app.engine.detectors import Trigger

MAX_BASE_SCORE = 80
MAX_SEMANTIC_ADJUSTMENT = 20

# Trigger yang saling meniadakan: diambil poin tertinggi, tidak dijumlahkan.
# Keduanya menilai fakta yang sama (kelayakan vendor), jadi menjumlahkannya
# berarti membayar dua kali untuk satu fakta.
EXCLUSIVE_GROUPS = [
    {"vendor_flagged", "vendor_new"},
    {"duplicate_confirmed", "duplicate_suspected"},
    # Satu grup temuan bisa berisi beberapa transaksi yang sama-sama di luar jam
    # kerja (mis. faktur ganda pukul 23:15 dan 01:15). "Temuan ini terjadi di
    # luar jam kerja" adalah SATU fakta, bukan satu per transaksi.
    {"timing_outside_hours"},
    {"insufficient_baseline"},
]

# Ambang atas (eksklusif) -> kode tingkat risiko.
#
# Yang dikembalikan KODE, bukan "Critical Risk". Teks itu adalah tampilan:
# ia berbahasa Inggris sementara UI berbahasa Indonesia, dan menyimpannya di
# database berarti menyimpan copy antarmuka sebagai data. Labelnya ada di
# app/core/constants.py.
RISK_BANDS = [
    (40, RISK_LOW),
    (60, RISK_MEDIUM),
    (80, RISK_HIGH),
    (101, RISK_CRITICAL),
]


def resolve_exclusives(triggers: list[Trigger]) -> list[Trigger]:
    """Dalam tiap grup eksklusif, sisakan hanya trigger berpoin tertinggi."""
    dropped: set[int] = set()

    for group in EXCLUSIVE_GROUPS:
        members = [t for t in triggers if t.code in group]
        if len(members) < 2:
            continue
        winner = max(members, key=lambda t: t.points)
        for t in members:
            if t is not winner:
                dropped.add(id(t))

    return [t for t in triggers if id(t) not in dropped]


def calculate_base_score(triggers: list[Trigger]) -> dict:
    """
    Menjumlahkan poin trigger objektif, lalu menerapkan batas 80 SATU KALI.

    Versi sebelumnya meng-cap skor tiap agen di 80 lalu meng-cap hasil
    penjumlahannya di 80 lagi, sehingga hampir semua kombinasi dua trigger
    langsung jenuh dan band Critical praktis hanya tercapai lewat penilaian LLM —
    kebalikan dari yang diinginkan.

    Sisa 20 poin sengaja disisakan agar Agent 3 selalu punya ruang menaikkan
    maupun menurunkan. Kalau skor objektif bisa mencapai 100, review tidak lagi
    berarti apa-apa.
    """
    effective = resolve_exclusives(triggers)
    raw = sum(t.points for t in effective)
    base = min(raw, MAX_BASE_SCORE)

    by_agent: dict[str, int] = {}
    for t in effective:
        by_agent[t.owner] = by_agent.get(t.owner, 0) + t.points

    return {
        "base_risk_score": base,
        "raw_score": raw,
        "capped": raw > MAX_BASE_SCORE,
        "max_base_score": MAX_BASE_SCORE,
        "agent_scores": [
            {"agent": agent, "score": score} for agent, score in sorted(by_agent.items())
        ],
        "objective_triggers": [
            {
                "code": t.code,
                "points": t.points,
                "owner": t.owner,
                "amplifier_only": t.amplifier_only,
                "narrative": t.narrative,
                "detail": t.detail,
            }
            for t in effective
        ],
        # Disimpan supaya terlihat apa yang dibuang dan kenapa.
        "suppressed_triggers": [
            {"code": t.code, "points": t.points,
             "reason": "trigger eksklusif dengan poin lebih rendah"}
            for t in triggers if t not in effective
        ],
    }


def clamp_adjustment(value) -> int:
    """Membatasi usulan Agent 3 ke +/-20, apa pun yang dikirimnya."""
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(-MAX_SEMANTIC_ADJUSTMENT, min(MAX_SEMANTIC_ADJUSTMENT, number))


def risk_decision(final_score: int) -> tuple[str, str]:
    """Mengembalikan (kode_tingkat, tindakan_yang_disarankan)."""
    for ceiling, level in RISK_BANDS:
        if final_score < ceiling:
            return level, RISK_ACTION[level]
    return RISK_CRITICAL, RISK_ACTION[RISK_CRITICAL]


def finalize(base_scoring: dict, semantic_adjustment, adjustment_reason: str = "") -> dict:
    """Menggabungkan skor objektif dengan penilaian Agent 3."""
    base = base_scoring.get("base_risk_score", 0)
    adjustment = clamp_adjustment(semantic_adjustment)
    final = max(0, min(100, base + adjustment))
    level, recommendation = risk_decision(final)

    return {
        **base_scoring,
        "llm_semantic_adjustment": adjustment,
        "adjustment_reason": adjustment_reason,
        "final_risk_score": final,
        "risk_level": level,
        "recommendation": recommendation,
    }
