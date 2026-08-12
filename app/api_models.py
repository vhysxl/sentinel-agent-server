"""
Model Pydantic untuk endpoint temuan.

Satu-satunya tujuan berkas ini: membuat /openapi.json memuat enum dan bentuk
response yang sebenarnya, supaya konsumen (sentinel-backend, sentinel) bisa
men-generate kontrak mereka dari satu sumber — bukan meniru-niru nilai enum
dengan tangan lalu melenceng.

Kode adalah DATA (masuk database, difilter, diperiksa constraint); label adalah
TAMPILAN. Enum di sini memakai kode, persis seperti app.core.constants.
"""
from typing import Literal

from pydantic import BaseModel

from app.core.constants import (
    FILTER_ALL,
    FILTER_OPEN,
    FILTER_RESOLVED,
    RESOLUTION_CONFIRMED_FRAUD,
    RESOLUTION_ESCALATED,
    RESOLUTION_FALSE_POSITIVE,
    RESOLUTION_JUSTIFIED,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)

RiskLevel = Literal[RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL]

Resolution = Literal[
    RESOLUTION_JUSTIFIED,
    RESOLUTION_FALSE_POSITIVE,
    RESOLUTION_CONFIRMED_FRAUD,
    RESOLUTION_ESCALATED,
]

FindingStatus = Literal[FILTER_OPEN, FILTER_RESOLVED, FILTER_ALL]


class FindingRow(BaseModel):
    """Satu baris temuan, bentuk tetap dari `_finding_row` di main.py."""

    id: int
    transaction_id: int
    related_transaction_ids: list[int]
    risk_score: int
    risk_level: RiskLevel
    risk_label: str
    recommended_action: str
    description: str
    resolution: Resolution | None
    resolution_note: str | None
    resolved_by: int | None
    resolved_at: str | None
    created_at: str
    updated_at: str | None
    narrative_stale: bool
    # Hanya terisi pada detail (get_finding); daftar memakai None.
    evidence: dict | None = None
    analysis: dict | None = None


class FindingSummary(BaseModel):
    """Bentuk tetap dari endpoint /api/summary."""

    perlu_tindakan: int
    perlu_ditinjau: int
    aman: int
    gagal: int
    belum_diperiksa: int
    total_transaksi: int
    per_tingkat: dict[RiskLevel, int]
    selesai: int
    total_temuan: int
    clear_rate: int | None


class AskHistoryRow(BaseModel):
    """Satu baris riwayat pertanyaan Ask Sentinel."""

    id: int
    question: str
    answer: str
    data_range: str | None
    figures: list[dict] | None
    tools_used: list[str] | None
    steps: list[dict] | None
    unsourced_figures: list[dict] | None
    warning: str | None
    created_at: str
