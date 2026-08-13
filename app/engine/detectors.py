"""
Definisi kanonik seluruh trigger.

Ini SATU-SATUNYA tempat sebuah trigger didefinisikan. Ekstraksi kandidat dan
scoring engine memanggil fungsi yang sama persis, sehingga mustahil keduanya
memakai definisi berbeda.

Sebelumnya "transaksi ganda" punya tiga definisi berbeda yang saling bertentangan
(SQL kandidat memakai tanggal kalender, tool memakai +/-24 jam, proposal menulis
"< 24 jam"), dan akibatnya kasus split payment unggulan lolos dari cabang
duplikat lalu tertangkap hanya karena kebetulan status vendornya high_risk.

Kepemilikan trigger (D3): setiap trigger dimiliki TEPAT SATU agen. Tidak ada
fakta yang dinilai dua kali.
"""
from dataclasses import dataclass, field, asdict
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import text

from app.core.config import (
    APPROVAL_THRESHOLD,
    NEW_VENDOR_MAX_TRANSACTIONS,
    RISKY_VENDOR_STATUSES,
    SMURF_MIN_AMOUNT,
    SMURF_MIN_COUNT,
    SMURF_WINDOW_DAYS,
    SPLIT_BAND_FACTOR,
    SPLIT_MIN_COUNT,
    SPLIT_WINDOW_DAYS,
    WIB,
    WORK_END_HOUR,
    WORK_START_HOUR,
)
from app.core.format import kelipatan, ringkas, rupiah, tanggal
from app.engine import statistics as stats

AGENT_1 = "agent_1_financial"
AGENT_2 = "agent_2_fraud"


@dataclass
class Trigger:
    """
    Satu fakta objektif yang menambah skor.

    `points` ditetapkan di sini oleh Python berdasarkan `detail` — nilai
    kembalian query — dan TIDAK PERNAH berasal dari teks yang ditulis LLM.
    Inilah yang membuat setiap angka pada skor dapat ditelusuri ke satu baris
    data dan satu fungsi.
    """
    code: str
    points: int
    owner: str
    narrative: str
    detail: dict = field(default_factory=dict)
    # Trigger amplifier tidak pernah membuat transaksi menjadi kandidat; ia hanya
    # menambah bobot pada transaksi yang sudah terpilih karena alasan lain.
    amplifier_only: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Agent 1 — analitik finansial
# ---------------------------------------------------------------------------

def _z_points(z: float) -> int:
    """
    Pita skor untuk modified z-score.

    Lonjakan statistik sendirian hanya mencapai band Medium; ia naik ke High
    setelah Agent 3 memeriksa konteks dan tidak menemukan pembenaran. Itu
    disengaja: nominal tak biasa berarti *tak biasa*, belum tentu *salah*.
    """
    if z >= 20:
        return 50
    if z >= 10:
        return 40
    if z >= 5:
        return 30
    return 20


def detect_amount_anomaly(db, txn) -> Optional[Trigger]:
    """Nominal jauh menyimpang dari kebiasaan vendor (atau kategori)."""
    vendor_baseline = [float(r[0]) for r in db.execute(text("""
        SELECT amount FROM transactions
        WHERE vendor_id = :v AND type = 'expense' AND id <> :i
    """), {"v": txn.vendor_id, "i": txn.id})] if txn.vendor_id else []

    category_baseline = [float(r[0]) for r in db.execute(text("""
        SELECT amount FROM transactions
        WHERE category = :c AND type = 'expense' AND id <> :i
    """), {"c": txn.category, "i": txn.id})]

    result = stats.evaluate(float(txn.amount), vendor_baseline, category_baseline)

    # insufficient_baseline diterbitkan sebagai trigger 0 poin, bukan didiamkan:
    # temuan harus bisa membedakan "diperiksa dan bersih" dari "tak terperiksa".
    if result.method == stats.METHOD_INSUFFICIENT:
        n = max(len(vendor_baseline), len(category_baseline))
        return Trigger(
            code="insufficient_baseline", points=0, owner=AGENT_1,
            narrative=(
                f"Not enough history yet to compare this amount — only "
                f"{n} similar transactions recorded so far. This means the "
                f"amount can't be assessed yet, not that it's already fine."
            ),
            detail=result.to_dict(), amplifier_only=True,
        )

    if not result.is_anomaly:
        return None

    z = result.computed_value
    points = _z_points(z) if z is not None else 20
    amount = float(txn.amount)
    acuan = "this vendor" if result.scope == "vendor" else f"the {txn.category} category"
    lipat = kelipatan(amount, result.median) if result.median else ""

    return Trigger(
        code="z_score_anomaly", points=points, owner=AGENT_1,
        narrative=(
            f"The amount {rupiah(amount)} is far above what's usual for {acuan}, "
            f"which is usually around {ringkas(result.median)} per transaction "
            f"(from {result.n_baseline} prior transactions)"
            + (f" — {lipat}." if lipat else ".")
        ),
        detail=result.to_dict(),
    )


def detect_timing(db, txn) -> Optional[Trigger]:
    """
    Baris dicatat ke sistem di luar jam kerja (Senin-Jumat 08:00-17:59 WIB).

    Membaca `created_at` — satu-satunya waktu yang dimiliki sistem ini.
    Transaksi berasal dari API bank, jadi saat bank mencatatnya ITULAH saat
    transaksi terjadi. Tidak ada tanggal terpisah yang diketik pengguna, dan
    karena itu tidak ada yang bisa dipalsukan dengan mengetik.

    AMPLIFIER SAJA. Transaksi tidak pernah menjadi temuan hanya karena jamnya.
    Setiap tim keuangan punya orang yang lembur menjelang tutup buku; kalau jam
    saja bisa menciptakan temuan, daftar temuan akan didominasi entri yang
    semuanya sah dan orang berhenti membacanya.
    """
    local = txn.created_at.astimezone(WIB)
    if local.weekday() < 5 and WORK_START_HOUR <= local.hour < WORK_END_HOUR:
        return None

    return Trigger(
        code="timing_outside_hours", points=20, owner=AGENT_1, amplifier_only=True,
        narrative=(
            f"Recorded {tanggal(local)} WIB — outside working hours "
            f"(Mon-Fri {WORK_START_HOUR:02d}:00-{WORK_END_HOUR - 1:02d}:59)."
        ),
        detail={
            "evaluated_field": "created_at",
            "reason": "recording time is written by the server, cannot be edited via the form",
            "recorded_at_wib": local.strftime("%Y-%m-%d %H:%M:%S"),
            "time_wib": local.strftime("%H:%M:%S"),
            "weekday": local.strftime("%A"),
            "timezone": "Asia/Jakarta",
            "work_hours": f"{WORK_START_HOUR:02d}:00-{WORK_END_HOUR - 1:02d}:59",
        },
    )


# ---------------------------------------------------------------------------
# Agent 2 — pola fraud
# ---------------------------------------------------------------------------

def detect_duplicate(db, txn) -> Optional[Trigger]:
    """
    Faktur yang sama dibayar dua kali.

    Dua tingkat keyakinan, dan itu penting: fakta dan dugaan tidak boleh bernilai
    sama.

      confirmed  invoice_no sama pada vendor sama  -> FAKTA, +50
      suspected  vendor+nominal sama dalam 24 jam, invoice_no kosong -> DUGAAN, +30

    Aturan lemah hanya berlaku kalau nomor faktur tidak diisi, dan narasinya wajib
    memakai kata "indikasi" — bukan "terdeteksi". Tanpa nomor faktur, biaya rutin
    identik (sewa, langganan) salah tertangkap, dan faktur sama yang dibayar
    selang seminggu justru lolos.
    """
    if not txn.vendor_id:
        return None

    if txn.invoice_no:
        rows = db.execute(text("""
            SELECT id, amount,
                   to_char(created_at AT TIME ZONE 'Asia/Jakarta',
                           'YYYY-MM-DD HH24:MI') AS wib
            FROM transactions
            WHERE vendor_id = :v AND invoice_no = :inv AND type = 'expense'
            ORDER BY created_at
        """), {"v": txn.vendor_id, "inv": txn.invoice_no}).fetchall()

        if len(rows) > 1:
            total = sum(float(r[1]) for r in rows)
            return Trigger(
                code="duplicate_confirmed", points=50, owner=AGENT_2,
                narrative=(
                    f"Invoice {txn.invoice_no} was paid {len(rows)} times to the same "
                    f"vendor. Total paid out is {rupiah(total)} for a single invoice, "
                    f"meaning {rupiah(total - float(txn.amount))} is a potential "
                    f"overpayment. Payments on {', '.join(r[2] for r in rows)} WIB."
                ),
                detail={
                    "rule": "same_invoice_no",
                    "invoice_no": txn.invoice_no,
                    "match_count": len(rows),
                    "total_amount": total,
                    "transaction_ids": [r[0] for r in rows],
                },
            )
        return None

    # Tidak ada nomor faktur: turun ke aturan lemah.
    rows = db.execute(text("""
        SELECT id, to_char(created_at AT TIME ZONE 'Asia/Jakarta',
                           'YYYY-MM-DD HH24:MI')
        FROM transactions
        WHERE vendor_id = :v AND amount = :a AND type = 'expense'
          AND created_at BETWEEN :lo AND :hi
        ORDER BY created_at
    """), {
        "v": txn.vendor_id, "a": txn.amount,
        "lo": txn.created_at - timedelta(hours=24),
        "hi": txn.created_at + timedelta(hours=24),
    }).fetchall()

    if len(rows) > 1:
        return Trigger(
            code="duplicate_suspected", points=30, owner=AGENT_2,
            narrative=(
                f"Indication of a duplicate payment: {len(rows)} transactions with the "
                f"same amount ({rupiah(txn.amount)}) to the same vendor within 24 "
                f"hours, on {', '.join(r[1] for r in rows)} WIB. The invoice number "
                f"is empty, so this is only a suspicion — check against the invoice."
            ),
            detail={
                "rule": "same_vendor_amount_24h",
                "match_count": len(rows),
                "transaction_ids": [r[0] for r in rows],
                "note": "invoice_no empty; medium confidence",
            },
        )
    return None


def detect_split_payment(db, txn) -> Optional[Trigger]:
    """
    Pembayaran dipecah agar masing-masing lolos dari ambang persetujuan.

    Syarat, semuanya harus terpenuhi:
      - nominal berada di pita [BAND_FACTOR * T, T)
      - ada >= MIN_COUNT transaksi seperti itu ke vendor sama dalam WINDOW hari
      - totalnya >= T

    Pita nominal itu yang membedakannya dari belanja rutin. Tanpa pita, 26
    transaksi @1 juta juga berjumlah di atas 25 juta dan ikut tertandai. Tanda
    tangan structuring bukan "totalnya besar", melainkan "nominalnya menempel
    tepat di bawah garis".
    """
    if not txn.vendor_id:
        return None

    low = APPROVAL_THRESHOLD * SPLIT_BAND_FACTOR
    amount = float(txn.amount)
    if not (low <= amount < APPROVAL_THRESHOLD):
        return None

    rows = db.execute(text("""
        SELECT id, amount, invoice_no,
               to_char(created_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD')
        FROM transactions
        WHERE vendor_id = :v AND type = 'expense'
          AND amount >= :low AND amount < :high
          AND created_at BETWEEN :lo AND :hi
        ORDER BY created_at
    """), {
        "v": txn.vendor_id, "low": low, "high": APPROVAL_THRESHOLD,
        "lo": txn.created_at - timedelta(days=SPLIT_WINDOW_DAYS),
        "hi": txn.created_at + timedelta(days=SPLIT_WINDOW_DAYS),
    }).fetchall()

    if len(rows) < SPLIT_MIN_COUNT:
        return None

    total = sum(float(r[1]) for r in rows)
    if total < APPROVAL_THRESHOLD:
        return None

    return Trigger(
        code="split_payment", points=50, owner=AGENT_2,
        narrative=(
            f"{len(rows)} payments of {rupiah(amount)} each to the same vendor "
            f"within {SPLIT_WINDOW_DAYS} days, totaling {rupiah(total)}. Each "
            f"payment stops just under the round number {rupiah(APPROVAL_THRESHOLD)} "
            f"— a pattern commonly used to make one large expense look like "
            f"several small ones, when the total is well past that figure."
        ),
        detail={
            "rule": "below_round_number_threshold",
            "reference_threshold": APPROVAL_THRESHOLD,
            "band_low": low,
            "window_days": SPLIT_WINDOW_DAYS,
            "match_count": len(rows),
            "total_amount": total,
            "transaction_ids": [r[0] for r in rows],
            "invoice_numbers": [r[2] for r in rows],
        },
    )


def detect_smurfing(db, txn) -> Optional[Trigger]:
    """
    Nominal identik dibayar berulang ke vendor yang sama.

    Beda dengan split_payment: di sini nominalnya TIDAK harus dekat garis bulat
    manapun. Sinyalnya murni FREKUENSI nominal identik yang tidak lazim —
    pola khas "smurfing", memecah satu pengeluaran besar jadi banyak pecahan
    kecil yang identik, disebar dalam beberapa hari.

    Syarat, semuanya harus terpenuhi:
      - nominal > SMURF_MIN_AMOUNT (floor, bukan pita — pecahan sekecil uang
        parkir/konsumsi terlalu umum berulang secara wajar untuk jadi sinyal)
      - ada >= SMURF_MIN_COUNT transaksi bernominal SAMA PERSIS ke vendor yang
        sama dalam WINDOW hari

    MIN_COUNT sengaja 3, bukan 2 — dua pembayaran identik berdekatan masih
    wajar (mis. cicilan dua termin). Tiga kali atau lebih dengan nominal
    identik dalam seminggu jarang terjadi pada pengeluaran yang sah.
    """
    if not txn.vendor_id:
        return None

    amount = float(txn.amount)
    if amount <= SMURF_MIN_AMOUNT:
        return None

    rows = db.execute(text("""
        SELECT id, to_char(created_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI')
        FROM transactions
        WHERE vendor_id = :v AND type = 'expense' AND amount = :a
          AND created_at BETWEEN :lo AND :hi
        ORDER BY created_at
    """), {
        "v": txn.vendor_id, "a": txn.amount,
        "lo": txn.created_at - timedelta(days=SMURF_WINDOW_DAYS),
        "hi": txn.created_at + timedelta(days=SMURF_WINDOW_DAYS),
    }).fetchall()

    if len(rows) < SMURF_MIN_COUNT:
        return None

    total = amount * len(rows)
    return Trigger(
        code="smurfing_pattern", points=40, owner=AGENT_2,
        narrative=(
            f"{len(rows)} payments of an identical amount ({rupiah(amount)}) to the "
            f"same vendor within {SMURF_WINDOW_DAYS} days, on "
            f"{', '.join(r[1] for r in rows)} WIB. Total {rupiah(total)}. This "
            f"frequency of identical payments is unusual for a single vendor — "
            f"an indication of one large expense deliberately split evenly."
        ),
        detail={
            "rule": "repeated_identical_amount",
            "min_amount_floor": SMURF_MIN_AMOUNT,
            "window_days": SMURF_WINDOW_DAYS,
            "match_count": len(rows),
            "total_amount": total,
            "transaction_ids": [r[0] for r in rows],
        },
    )


def detect_vendor_backdated(db, txn) -> Optional[Trigger]:
    """
    Uang keluar ke vendor SEBELUM vendor itu terdaftar.

    Pola vendor fiktif yang klasik: pembayaran terjadi lebih dulu, lalu catatan
    vendornya dibuat belakangan supaya pembayaran itu punya penerima yang
    terlihat sah.

    AMPLIFIER, bukan pemicu kandidat — dan alasannya konkret. Setiap migrasi
    data membuat SELURUH vendor punya pola ini sekaligus, karena baris vendor
    dibuat saat impor sementara transaksinya bertanggal mundur. Kalau ini bisa
    menciptakan temuan sendirian, satu kali impor akan membanjiri antrean dengan
    temuan yang semuanya palsu.

    Sebagai penguat ia tetap berguna: pada temuan yang memang sudah bermasalah,
    fakta bahwa vendornya didaftarkan belakangan menambah bobot nyata.

    Kolom ini dulu ditampilkan ke agen tanpa aturan apa pun di belakangnya.
    Akibatnya Agent 2 menafsirkannya sendiri dan menyebutnya "sangat
    mencurigakan" — kebetulan benar, tapi tanpa skor dan tanpa jejak. Sekarang
    Python yang memutuskan.
    """
    if not txn.vendor_id:
        return None

    # Perbandingan dilakukan sebagai TANGGAL WIB di sisi SQL.
    #
    # `vendors.join_date` bertipe timestamp NAIF sedangkan `transactions.created_at`
    # timestamptz. Membandingkan keduanya langsung membuat Postgres menafsirkan
    # yang naif memakai timezone sesi — GMT di server ini — sehingga batas harinya
    # meleset 7 jam. Dan `AT TIME ZONE` bekerja ke arah BERLAWANAN pada keduanya:
    # pada kolom naif ia menambahkan zona, pada kolom sadar-zona ia melepasnya.
    row = db.execute(text("""
        SELECT v.vendor_name,
               v.join_date::date,
               (t.created_at AT TIME ZONE 'Asia/Jakarta')::date,
               v.join_date::date - (t.created_at AT TIME ZONE 'Asia/Jakarta')::date
        FROM vendors v, transactions t
        WHERE v.id = :v AND t.id = :i AND v.join_date IS NOT NULL
          AND (t.created_at AT TIME ZONE 'Asia/Jakarta')::date < v.join_date::date
    """), {"v": txn.vendor_id, "i": txn.id}).fetchone()
    if not row:
        return None

    name, joined, paid, gap = row

    return Trigger(
        code="vendor_registered_after_payment", points=20, owner=AGENT_2,
        amplifier_only=True,
        narrative=(
            f"Payment to {name} was recorded {gap} days BEFORE this vendor "
            f"was registered in the master data. The money went out first, "
            f"the vendor record followed."
        ),
        detail={
            "rule": "payment_precedes_vendor_registration",
            "vendor_id": txn.vendor_id,
            "vendor_name": name,
            "vendor_joined_wib": joined.strftime("%Y-%m-%d"),
            "payment_recorded_wib": paid.strftime("%Y-%m-%d"),
            "gap_days": gap,
            "note": ("a data migration can produce this pattern across all "
                     "vendors at once; that's why it never triggers a "
                     "candidate on its own"),
        },
    )


def detect_vendor_risk(db, txn) -> Optional[Trigger]:
    """
    Vendor nonaktif yang masih dibayar, atau vendor yang riwayatnya masih terlalu tipis.

    Status vendor di app HANYA 'active'/'inactive' (lihat
    vendor.validation.js) — tidak ada tingkatan risiko lain di data master.
    Dibayar padahal berstatus nonaktif berarti: lupa dinonaktifkan, atau
    sengaja tetap dipakai walau semestinya sudah berhenti. Keduanya layak
    diperiksa.

    Kedua cabang tidak pernah dijumlahkan — diambil yang tertinggi. Dimiliki
    Agent 2 saja. Sebelumnya Agent 1 dan Agent 2 sama-sama menilai vendor,
    sehingga fakta yang sama dihitung dua kali, lalu ditambal dengan
    mem-parsing string "(+30)" dari teks agen untuk membatalkannya.
    """
    if not txn.vendor_id:
        return None

    row = db.execute(text("""
        SELECT v.vendor_name, v.status,
               (SELECT count(*) FROM transactions t WHERE t.vendor_id = v.id)
        FROM vendors v WHERE v.id = :v
    """), {"v": txn.vendor_id}).fetchone()
    if not row:
        return None

    name, status, total = row[0], (row[1] or "").lower(), int(row[2] or 0)

    if status in RISKY_VENDOR_STATUSES:
        return Trigger(
            code="vendor_flagged", points=30, owner=AGENT_2,
            narrative=(f"Vendor {name} is marked inactive in the master data, "
                       f"but this transaction was still paid out to them."),
            detail={"vendor_id": txn.vendor_id, "vendor_name": name,
                    "vendor_status": status, "total_transactions": total},
        )

    if total < NEW_VENDOR_MAX_TRANSACTIONS:
        return Trigger(
            code="vendor_new", points=20, owner=AGENT_2,
            narrative=(
                f"Vendor {name} has only transacted {total} times so far, so "
                f"there isn't enough track record to rely on yet."
            ),
            detail={"vendor_id": txn.vendor_id, "vendor_name": name,
                    "vendor_status": status, "total_transactions": total},
        )

    return None


# ---------------------------------------------------------------------------
# Orkestrasi
# ---------------------------------------------------------------------------

def run_all(db, txn) -> list[Trigger]:
    """Menjalankan seluruh detektor atas satu transaksi."""
    triggers: list[Trigger] = []

    for detector in (detect_amount_anomaly, detect_timing, detect_duplicate,
                     detect_vendor_risk, detect_vendor_backdated,
                     detect_split_payment, detect_smurfing):
        found = detector(db, txn)
        if found:
            triggers.append(found)

    return triggers


def is_candidate(triggers: list[Trigger]) -> bool:
    """
    Sebuah transaksi layak diperiksa kalau punya minimal satu trigger yang
    BUKAN amplifier. Jam dan insufficient_baseline tidak pernah cukup sendirian.
    """
    return any(t.points > 0 and not t.amplifier_only for t in triggers)


def to_payload(triggers: list[Trigger]) -> list[dict]:
    return [t.to_dict() for t in triggers]
