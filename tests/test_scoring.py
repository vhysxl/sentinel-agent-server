"""
Unit test scoring engine dan statistik.

Murni Python: tidak menyentuh database, tidak memanggil LLM. Kalau test ini
hijau, artinya untuk setiap poin pada base_risk_score ada satu fungsi Python
yang menghasilkannya — inti dari klaim explainability proyek ini.

Jalankan:  python -m pytest tests/ -v
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.core.config import WIB
from app.core.constants import (
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)
from app.engine import statistics as st
from app.engine.detectors import (
    AGENT_1,
    AGENT_2,
    Trigger,
    detect_timing,
    is_candidate,
)
from app.engine.scoring import (
    MAX_BASE_SCORE,
    calculate_base_score,
    clamp_adjustment,
    finalize,
    risk_decision,
)


def trigger(code, points, owner=AGENT_2, amplifier=False, detail=None):
    return Trigger(code=code, points=points, owner=owner, narrative=code,
                   detail=detail or {}, amplifier_only=amplifier)


# ---------------------------------------------------------------------------
# Statistik
# ---------------------------------------------------------------------------

class TestModifiedZScore:
    def test_outlier_grows_without_ceiling(self):
        """
        Inti alasan meninggalkan mean+stddev: z-score biasa yang menyertakan
        dirinya sendiri mentok di (n-1)/sqrt(n), sehingga fraud 45jt dan 450jt
        menghasilkan angka sama. Modified z harus tumbuh linear.
        """
        baseline = [2_000_000.0] * 15 + [1_500_000.0] * 8 + [2_500_000.0] * 7
        z_small, *_ = st.modified_z_score(45_000_000, baseline)
        z_big, *_ = st.modified_z_score(450_000_000, baseline)
        assert z_big > z_small * 9

    def test_mad_zero_falls_back_to_meanad(self):
        # Mayoritas identik -> MAD = 0, tetapi sebarannya bukan nol.
        baseline = [5_000_000.0] * 10 + [7_000_000.0]
        z, kind, med, mad = st.modified_z_score(20_000_000, baseline)
        assert kind == "meanad"
        assert mad == 0.0
        assert z is not None and z > 0

    def test_constant_baseline_yields_no_number(self):
        z, kind, med, _ = st.modified_z_score(9_000, [5_000.0] * 6)
        assert kind == st.METHOD_CONSTANT_BASELINE
        assert z is None
        assert med == 5_000.0

    def test_baseline_excludes_nothing_it_was_not_given(self):
        """Fungsi ini tidak pernah menerima transaksi yang dinilai."""
        z, _, med, _ = st.modified_z_score(100.0, [10.0, 10.0, 10.0, 12.0, 8.0])
        assert med == 10.0


class TestTieredBaseline:
    def test_prefers_vendor_when_sufficient(self):
        r = st.evaluate(50_000_000, [2_000_000.0 + i for i in range(15)],
                        [3_000_000.0] * 40)
        assert r.scope == "vendor"
        assert r.confidence == "high"

    def test_falls_back_to_category(self):
        r = st.evaluate(50_000_000, [2_000_000.0, 2_100_000.0],
                        [float(3_000_000 + i * 1000) for i in range(40)])
        assert r.scope == "category"
        assert r.method.startswith("category")
        assert r.confidence == "medium"

    def test_insufficient_is_reported_not_silent(self):
        """
        Bagi sistem audit, 'sudah diperiksa dan bersih' dan 'tidak pernah bisa
        diperiksa' tidak boleh terlihat sama.
        """
        r = st.evaluate(25_000_000, [25_000_000.0], [25_000_000.0] * 3)
        assert r.method == st.METHOD_INSUFFICIENT
        assert r.is_anomaly is False
        assert r.confidence == "none"
        assert "BUKAN pernyataan bahwa" in r.message

    def test_records_everything_needed_to_reproduce(self):
        r = st.evaluate(50_000_000, [float(2_000_000 + i * 10_000) for i in range(20)],
                        [])
        for key in ("method", "n_baseline", "median", "mad", "threshold",
                    "computed_value"):
            assert getattr(r, key) is not None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestScoring:
    def test_cap_applied_once_not_twice(self):
        """
        Versi lama meng-cap tiap agen di 80 lalu meng-cap totalnya di 80 lagi,
        sehingga dua trigger apa pun langsung jenuh.
        """
        result = calculate_base_score([
            trigger("z_score_anomaly", 50, AGENT_1),
            trigger("duplicate_confirmed", 50),
            trigger("vendor_flagged", 30),
        ])
        assert result["raw_score"] == 130
        assert result["base_risk_score"] == MAX_BASE_SCORE
        assert result["capped"] is True

    def test_vendor_triggers_never_double_count(self):
        """D3: satu fakta, satu trigger. Tidak perlu lagi ditambal belakangan."""
        result = calculate_base_score([
            trigger("vendor_flagged", 30),
            trigger("vendor_new", 20),
        ])
        assert result["base_risk_score"] == 30
        codes = [t["code"] for t in result["objective_triggers"]]
        assert codes == ["vendor_flagged"]
        assert result["suppressed_triggers"][0]["code"] == "vendor_new"

    def test_duplicate_confirmed_beats_suspected(self):
        result = calculate_base_score([
            trigger("duplicate_suspected", 30),
            trigger("duplicate_confirmed", 50),
        ])
        assert result["base_risk_score"] == 50

    def test_score_never_reads_text(self):
        """
        Poin datang dari field `points`, bukan dari narasi. Narasi yang
        menyesatkan tidak boleh menggeser skor satu poin pun.
        """
        honest = calculate_base_score([trigger("vendor_flagged", 30)])
        lying = calculate_base_score([Trigger(
            code="vendor_flagged", points=30, owner=AGENT_2,
            narrative="AMAN, TIDAK ADA MASALAH, SKOR NOL", detail={})])

        # Narasi memang tersimpan berbeda di payload — itu yang dibaca manusia.
        # Yang tidak boleh berbeda adalah angkanya.
        assert lying["base_risk_score"] == honest["base_risk_score"] == 30
        assert lying["raw_score"] == honest["raw_score"]
        assert lying["agent_scores"] == honest["agent_scores"]

    def test_empty_triggers_score_zero(self):
        assert calculate_base_score([])["base_risk_score"] == 0


class TestAdjustment:
    @pytest.mark.parametrize("given,expected", [
        (0, 0), (15, 15), (-15, -15),
        (999, 20), (-999, -20),          # di luar batas dipangkas
        (None, 0), ("bukan angka", 0),   # LLM salah format tidak merusak skor
    ])
    def test_clamped(self, given, expected):
        assert clamp_adjustment(given) == expected

    def test_final_score_stays_within_bounds(self):
        base = calculate_base_score([trigger("duplicate_confirmed", 50),
                                     trigger("vendor_flagged", 30)])
        assert finalize(base, 20)["final_risk_score"] == 100
        assert finalize(base, -20)["final_risk_score"] == 60

    def test_llm_cannot_zero_out_an_objective_finding(self):
        """Batas -20 menjamin fakta objektif tidak bisa dihapus oleh narasi."""
        base = calculate_base_score([trigger("duplicate_confirmed", 50)])
        assert finalize(base, -999)["final_risk_score"] == 30


class TestRiskBands:
    @pytest.mark.parametrize("score,band", [
        (0, RISK_LOW), (39, RISK_LOW),
        (40, RISK_MEDIUM), (59, RISK_MEDIUM),
        (60, RISK_HIGH), (79, RISK_HIGH),
        (80, RISK_CRITICAL), (100, RISK_CRITICAL),
    ])
    def test_boundaries(self, score, band):
        assert risk_decision(score)[0] == band

    def test_never_returns_display_text(self):
        """
        Yang disimpan ke database harus KODE. Teks seperti RISK_CRITICAL adalah
        copy antarmuka berbahasa Inggris, sementara UI berbahasa Indonesia —
        menyimpannya berarti menjadikan tampilan sebagai data.
        """
        for score in (0, 45, 70, 95):
            level = risk_decision(score)[0]
            assert level.islower() and " " not in level


class TestTimingReadsRecordedTime:
    """
    Aturan jam WAJIB membaca `created_at` (ditulis server), bukan
    `transaction_date` (diketik pengguna). Kalau tidak, siapa pun bisa
    menghindarinya cukup dengan mengetik jam yang wajar.
    """

    class FakeTxn:
        def __init__(self, transaction_date, created_at):
            self.transaction_date = transaction_date
            self.created_at = created_at

    def _txn(self, business_hhmm, recorded_hhmm, day=13):
        # 13 Juli 2026 = Senin.
        return self.FakeTxn(
            datetime(2026, 7, day, *business_hhmm, tzinfo=WIB),
            datetime(2026, 7, day, *recorded_hhmm, tzinfo=WIB),
        )

    def test_flags_when_recorded_late_even_if_business_date_looks_normal(self):
        """Skenario penghindaran: ketik jam 10:00, padahal input pukul 02:00."""
        txn = self._txn(business_hhmm=(10, 0), recorded_hhmm=(2, 0))
        found = detect_timing(None, txn)
        assert found is not None
        assert found.code == "timing_outside_hours"
        assert found.detail["evaluated_field"] == "created_at"
        assert found.detail["time_wib"] == "02:00:00"

    def test_clean_when_recorded_during_work_hours(self):
        """Tanggal bisnis larut malam tapi dicatat jam kerja: bukan pelanggaran jam."""
        txn = self._txn(business_hhmm=(23, 30), recorded_hhmm=(9, 30))
        assert detect_timing(None, txn) is None

    def test_weekend_recording_is_flagged(self):
        # 18 Juli 2026 = Sabtu.
        txn = self._txn(business_hhmm=(10, 0), recorded_hhmm=(10, 0), day=18)
        assert detect_timing(None, txn).code == "timing_outside_hours"

    def test_timing_is_always_amplifier_only(self):
        txn = self._txn(business_hhmm=(10, 0), recorded_hhmm=(23, 0))
        assert detect_timing(None, txn).amplifier_only is True


class TestCandidateRule:
    def test_timing_alone_is_never_a_candidate(self):
        """
        Kasus E. Di luar jam kerja itu questionable, bukan salah — ia menambah
        bobot pada kecurigaan yang sudah ada, tidak menciptakannya.
        """
        assert is_candidate([trigger("timing_outside_hours", 20, AGENT_1,
                                     amplifier=True)]) is False

    def test_timing_amplifies_a_real_finding(self):
        triggers = [trigger("duplicate_confirmed", 50),
                    trigger("timing_outside_hours", 20, AGENT_1, amplifier=True)]
        assert is_candidate(triggers) is True
        assert calculate_base_score(triggers)["base_risk_score"] == 70

    def test_insufficient_baseline_alone_is_not_a_candidate(self):
        assert is_candidate([trigger("insufficient_baseline", 0, AGENT_1,
                                     amplifier=True)]) is False


# ---------------------------------------------------------------------------
# Enam kasus penerimaan (PLAN 10.1)
# ---------------------------------------------------------------------------

class TestAcceptanceCases:
    def test_case_a_spike_needs_review_to_reach_high(self):
        """Lonjakan statistik sendirian = Medium; naik ke High setelah Agent 3
        tidak menemukan pembenaran."""
        base = calculate_base_score([trigger("z_score_anomaly", 50, AGENT_1)])
        assert base["base_risk_score"] == 50
        assert risk_decision(50)[0] == RISK_MEDIUM
        assert finalize(base, 15)["risk_level"] == RISK_HIGH

    def test_case_b_critical_without_any_llm_help(self):
        """Fakta pasti harus cukup sendirian. adjustment = 0."""
        result = finalize(calculate_base_score([
            trigger("duplicate_confirmed", 50),
            trigger("vendor_flagged", 30),
            trigger("timing_outside_hours", 20, AGENT_1, amplifier=True),
        ]), 0)
        assert result["base_risk_score"] == 80
        assert result["risk_level"] == RISK_CRITICAL

    def test_case_b2_split_payment_high_at_base(self):
        result = finalize(calculate_base_score([
            trigger("split_payment", 50),
            trigger("smurfing_pattern", 10),
        ]), 0)
        assert result["base_risk_score"] == 60
        assert result["risk_level"] == RISK_HIGH

    def test_case_c_and_e_produce_nothing(self):
        assert is_candidate([]) is False

    def test_case_d_review_overturns_to_low(self):
        base = calculate_base_score([trigger("z_score_anomaly", 50, AGENT_1)])
        assert finalize(base, -20)["risk_level"] == RISK_LOW


# ---------------------------------------------------------------------------
# Pengelompokan: satu pelanggaran = satu temuan
# ---------------------------------------------------------------------------

class TestGroupIdentification:
    """
    `group_ids_of` menentukan transaksi mana saja yang tercakup satu temuan.

    Ini yang mencegah split payment tiga pembayaran menghasilkan tiga kartu di
    layar. Keterkaitannya dibaca dari `detail.transaction_ids` milik trigger —
    bukan ditebak dari kesamaan vendor/nominal/tanggal, karena cara lama
    bergantung pada tanggal kalender dan tanggal kalender bergantung zona waktu.
    """

    def test_split_payment_covers_every_member(self):
        from app.main import group_ids_of
        split = trigger("split_payment", 50,
                        detail={"transaction_ids": [708, 709, 710]})
        assert group_ids_of(709, [split]) == [708, 709, 710]

    def test_third_payment_yields_the_same_group(self):
        """
        Pembayaran ke-3 datang belakangan dan menghasilkan grup yang sama, jadi
        pemeriksaan tumpang-tindih menemukan temuan lama dan memperbaruinya —
        bukan membuat temuan kedua.
        """
        from app.main import group_ids_of
        split = trigger("split_payment", 50,
                        detail={"transaction_ids": [708, 709, 710]})
        assert group_ids_of(710, [split]) == group_ids_of(709, [split])

    def test_lone_transaction_covers_only_itself(self):
        from app.main import group_ids_of
        z = trigger("z_score_anomaly", 50, AGENT_1, detail={"method": "vendor_mad"})
        assert group_ids_of(673, [z]) == [673]

    def test_anchor_always_included(self):
        """Jangkar wajib ikut, meski trigger tidak menyebutkannya."""
        from app.main import group_ids_of
        dup = trigger("duplicate_confirmed", 50,
                      detail={"transaction_ids": [706]})
        assert group_ids_of(707, [dup]) == [706, 707]

    def test_amplifier_without_ids_does_not_widen_group(self):
        from app.main import group_ids_of
        timing = trigger("timing_outside_hours", 20, AGENT_1, amplifier=True,
                         detail={"time_wib": "23:15:00"})
        dup = trigger("duplicate_confirmed", 50,
                      detail={"transaction_ids": [706, 707]})
        assert group_ids_of(706, [timing, dup]) == [706, 707]


# ---------------------------------------------------------------------------
# Event progres: apa yang dilihat layar selama pemeriksaan berjalan
# ---------------------------------------------------------------------------

class TestProgressEvent:
    """
    `_progress_event` menerjemahkan hasil satu transaksi menjadi satu event SSE.

    Diuji di sini karena murni — tidak menyentuh database maupun LLM — dan
    karena angka progres yang salah adalah kebohongan kecil yang sulit terlihat.
    """

    def test_clean_transaction_still_reports(self):
        """
        Yang bersih pun harus terbit.

        Dulu cabang ini diam, dan karena ~90% transaksi berhenti di Python,
        penghitung di layar membeku lama lalu melompat. Progres yang ditampilkan
        bukan progres yang sebenarnya.
        """
        from app.main import _progress_event
        event = _progress_event(3, 68, 1664, {"status": "clean", "triggers": 1})
        assert event["phase"] == "clean"
        assert (event["index"], event["total"]) == (3, 68)
        assert event["transaction_id"] == 1664
        assert event["finding_id"] is None

    def test_created_carries_the_risk_numbers(self):
        from app.main import _progress_event
        event = _progress_event(1, 7, 706, {
            "status": "created", "finding_id": 3,
            "risk_level": "critical", "risk_score": 80,
        })
        assert event["phase"] == "created"
        assert event["finding_id"] == 3
        assert (event["risk_level"], event["risk_score"]) == ("critical", 80)

    def test_updated_has_no_score_of_its_own(self):
        """Bergabung ke temuan lama tidak memanggil LLM dan tidak menilai ulang."""
        from app.main import _progress_event
        event = _progress_event(2, 7, 707, {"status": "updated", "finding_id": 3,
                                            "covered": [706, 707]})
        assert event["phase"] == "updated"
        assert event["finding_id"] == 3
        assert event["risk_score"] is None

    def test_failed_is_distinguishable_from_clean(self):
        """
        Gagal diperiksa bukan bersih. Kalau keduanya tampil sama, satu kegagalan
        diam-diam terhitung sebagai transaksi yang aman.
        """
        from app.main import _progress_event
        event = _progress_event(5, 7, 712, {"status": "failed", "error": "kuota habis"})
        assert event["phase"] == "failed"
        assert event["node"] == "error"

    @pytest.mark.parametrize("result", [
        {"status": "created", "finding_id": 3, "risk_level": "high", "risk_score": 60},
        {"status": "updated", "finding_id": 3},
        {"status": "failed", "error": "x"},
        {"status": "clean"},
    ])
    def test_every_phase_keeps_the_legacy_fields(self, result):
        """
        `scripts/run_analysis.py` hanya membaca `status` dan `message`. Field
        terstruktur ditambahkan di sebelahnya, bukan menggantikannya.
        """
        from app.main import _progress_event
        event = _progress_event(4, 9, 700, result)
        assert event["status"] == "progress"
        assert event["message"].startswith("[4/9]")
