"""
Unit test helper murni dari app/main.py: rakitan event SSE, pemisahan muatan
evidence/analysis, narasi cadangan, dan dedup trigger grup.

Sengaja TANPA database dan TANPA LLM: fungsi yang diuji di sini adalah
kandang-kandang logika yang dipakai endpoint, dan justru yang dulu tidak punya
test sama sekali. Yang menyentuh Postgres (JSONB/ARRAY) diuji lewat jalur
end-to-end, bukan di unit test ini.

Jalankan:  python -m pytest tests/test_api_helpers.py -v
"""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import (
    _day_bounds_wib,
    _fallback_narrative,
    _progress_event,
    _split_payload,
    merge_group_triggers,
    parse_agent_json,
    sse_event,
)
from app.core.config import WIB
from app.core.constants import (
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)
from app.engine.detectors import AGENT_1, AGENT_2, Trigger


def trigger(code, points, owner=AGENT_2, narrative=None, detail=None):
    return Trigger(code=code, points=points, owner=owner,
                   narrative=narrative or code, detail=detail or {})


# ---------------------------------------------------------------------------
# Parsing keluaran LLM
# ---------------------------------------------------------------------------

class TestParseAgentJson:
    def test_plain_json(self):
        assert parse_agent_json('{"verdict": "risk"}') == {"verdict": "risk"}

    def test_fenced_json(self):
        content = '```json\n{"verdict": "clean"}\n```'
        assert parse_agent_json(content) == {"verdict": "clean"}

    def test_bare_fence(self):
        content = '```\n{"a": 1}\n```'
        assert parse_agent_json(content) == {"a": 1}


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

class TestSseEvent:
    def test_frame_ends_with_double_newline(self):
        assert sse_event({"status": "info"}).endswith("\n\n")

    def test_payload_is_json(self):
        frame = sse_event({"status": "progress", "index": 3})
        assert frame.startswith("data: ")
        import json
        payload = json.loads(frame[len("data: "):])
        assert payload == {"status": "progress", "index": 3}


# ---------------------------------------------------------------------------
# Rakitan event progres
# ---------------------------------------------------------------------------

class TestProgressEvent:
    def test_created_carries_finding_fields(self):
        event = _progress_event(3, 10, 42, {
            "status": "created", "finding_id": 7,
            "risk_level": RISK_CRITICAL, "risk_score": 80,
        })
        assert event["status"] == "progress"
        assert event["index"] == 3
        assert event["total"] == 10
        assert event["transaction_id"] == 42
        assert event["node"] == "finding"
        assert event["phase"] == "created"
        assert event["finding_id"] == 7
        assert event["risk_level"] == RISK_CRITICAL
        assert event["risk_score"] == 80

    def test_updated_merges_without_llm(self):
        event = _progress_event(1, 2, 55, {"status": "updated", "finding_id": 9})
        assert event["phase"] == "updated"
        assert event["finding_id"] == 9
        assert event["risk_level"] is None
        assert "tanpa LLM" in event["message"]

    def test_failed(self):
        event = _progress_event(1, 1, 3, {"status": "failed"})
        assert event["node"] == "error"
        assert event["phase"] == "failed"
        assert "GAGAL" in event["message"]

    def test_clean_is_published(self):
        # Dulu baris bersih tidak menerbitkan apa pun, sehingga ~90% progres
        # tidak terlihat. Kini ia ikut terbit — ini yang diuji.
        event = _progress_event(2, 2, 8, {"status": "clean"})
        assert event["node"] == "clean"
        assert event["phase"] == "clean"
        assert "bersih" in event["message"]


# ---------------------------------------------------------------------------
# Narasi cadangan saat LLM mati
# ---------------------------------------------------------------------------

class TestFallbackNarrative:
    def test_assembles_trigger_narratives(self):
        base = {"objective_triggers": [
            {"narrative": "A"}, {"narrative": "B"},
        ]}
        out = _fallback_narrative(base)
        assert out == "A B"

    def test_empty_triggers_have_fallback_line(self):
        assert _fallback_narrative({"objective_triggers": []}) == \
            "Tidak ada narasi yang dapat dihasilkan."


# ---------------------------------------------------------------------------
# Pemisahan muatan evidence (Python) vs analysis (LLM)
# ---------------------------------------------------------------------------

class TestSplitPayload:
    def _base(self):
        return {
            "objective_triggers": [{"code": "x", "points": 20}],
            "suppressed_triggers": [],
            "base_risk_score": 50,
            "raw_score": 50,
            "capped": False,
            "agent_scores": [{"agent": AGENT_1, "score": 20}],
        }

    def test_evidence_carries_python_facts_only(self):
        scoring = {**self._base(), "llm_semantic_adjustment": 0,
                   "adjustment_reason": ""}
        evidence, analysis = _split_payload(self._base(), scoring, {}, {}, {})
        assert evidence["provenance"]["scored_by"] == "python_scoring_engine"
        assert "provenance" in evidence
        assert "llm_model" in evidence["provenance"]
        assert evidence["base_risk_score"] == 50
        # analysis punya struktur agen; evidence tidak
        assert "agents" in analysis
        assert "agents" not in evidence

    def test_analysis_tracks_agent_failure(self):
        scoring = {**self._base(), "llm_semantic_adjustment": 0,
                   "adjustment_reason": "kuota habis"}
        json_a3 = {"_failed": True, "error": "429"}
        _, analysis = _split_payload(self._base(), scoring, {}, {}, json_a3)
        assert analysis["llm_review_failed"] is True
        assert analysis["adjustment_reason"] == "kuota habis"


# ---------------------------------------------------------------------------
# Dedup trigger grup (split payment 3x harus dihitung sekali)
# ---------------------------------------------------------------------------

class TestMergeGroupTriggers:
    def test_duplicate_trigger_across_members_is_deduplicated(self):
        shared = trigger("split_payment", 50, AGENT_2, detail={"transaction_ids": [1, 2, 3]})
        candidates = {
            1: [shared],
            2: [trigger("split_payment", 50, AGENT_2, detail={"transaction_ids": [1, 2, 3]})],
            3: [shared],
        }
        merged = merge_group_triggers(candidates, [1, 2, 3])
        assert len(merged) == 1

    def test_distinct_triggers_are_kept(self):
        a = trigger("split_payment", 50, AGENT_2, detail={"transaction_ids": [1]})
        b = trigger("z_score_anomaly", 30, AGENT_1, detail={})
        merged = merge_group_triggers({1: [a], 2: [b]}, [1, 2])
        assert {t.code for t in merged} == {"split_payment", "z_score_anomaly"}


# ---------------------------------------------------------------------------
# Batas tanggal riwayat Ask Sentinel (WIB, end_date inklusif)
# ---------------------------------------------------------------------------

class TestDayBoundsWib:
    def test_no_dates_returns_no_bounds(self):
        assert _day_bounds_wib(None, None) == (None, None)

    def test_start_date_is_midnight_wib(self):
        start, end = _day_bounds_wib(date(2026, 8, 12), None)
        assert start == datetime(2026, 8, 12, tzinfo=WIB)
        assert end is None

    def test_end_date_is_exclusive_start_of_next_day(self):
        """
        A naive `created_at <= end_date` would compare against midnight of
        end_date itself, silently dropping anything logged later that same
        day. The bound here must be the *next* day's midnight instead.
        """
        _, end = _day_bounds_wib(None, date(2026, 8, 12))
        assert end == datetime(2026, 8, 13, tzinfo=WIB)

    def test_end_of_day_entry_falls_within_bounds(self):
        _, end = _day_bounds_wib(None, date(2026, 8, 12))
        almost_midnight = datetime(2026, 8, 12, 23, 59, 59, tzinfo=WIB)
        assert almost_midnight < end

    def test_next_day_entry_falls_outside_bounds(self):
        _, end = _day_bounds_wib(None, date(2026, 8, 12))
        next_day = datetime(2026, 8, 13, 0, 0, 0, tzinfo=WIB)
        assert not (next_day < end)
