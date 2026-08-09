"""
Seed terisolasi untuk menguji SATU aturan saja: z-score anomali nominal.

Kenapa payroll yang dipakai
---------------------------
Payroll tidak punya vendor. Akibatnya seluruh trigger lain mati dengan
sendirinya — `detect_duplicate`, `detect_split_payment`, dan `detect_vendor_risk`
semuanya langsung `return None` ketika `vendor_id` kosong. Ditambah jam kerja
untuk mematikan `timing_outside_hours`, yang tersisa hanya z_score_anomaly.
Kalau skornya bergerak, penyebabnya hanya bisa satu hal.

Kenapa nominalnya TIDAK dibuat persis identik
---------------------------------------------
Kalau 20 payroll bernilai sama persis, MAD = 0 dan MeanAD = 0, sehingga
statistics.evaluate() jatuh ke cabang `constant_baseline` — jalur yang TIDAK
menghasilkan angka z sama sekali (hanya "berbeda / tidak berbeda"). Itu edge case
yang sah, tapi bukan yang ingin diuji di sini.

Variasi +/-1% mencerminkan payroll sungguhan (lembur, potongan, pembulatan
pajak) dan membuat MAD kecil tapi positif. Konsekuensinya justru bagus: pada
baseline yang rapat, penyimpangan kecil pun menghasilkan z besar.

Baseline yang dipakai
---------------------
Tanpa vendor, `detect_amount_anomaly` turun ke baseline KATEGORI. Minimumnya 20
(MIN_CATEGORY_BASELINE), dan baseline selalu mengecualikan transaksi yang sedang
dinilai. Karena itu jumlahnya harus 21: 20 normal + 1 outlier. Menilai outlier
memakai 20 pembanding; menilai satu payroll normal juga memakai 20.
Kurang dari itu dan seluruhnya berstatus `insufficient_baseline`.

Dua mode, satu transaksi
------------------------
Nominal, tanggal, dan z-score outlier PERSIS SAMA di kedua mode. Yang berbeda
hanya konteks bisnisnya. Jadi kalau hasil akhirnya berbeda, penyebabnya hanya
bisa penilaian Agent 3 — bukan perhitungan, bukan data.

    tanpa --boom : revenue stagnan. Bonus tidak punya penjelasan.
                   Agent 3 semestinya memberi 0 atau positif.
    dengan --boom: revenue melonjak di bulan yang sama. Bonus jadi masuk akal.
                   Agent 3 semestinya memberi NEGATIF dan skornya turun.

Pakai
-----
    python scripts/z_score_seed_test.py                    # outlier default
    python scripts/z_score_seed_test.py 45000000           # outlier sesuai keinginan
    python scripts/z_score_seed_test.py 45000000 --boom    # + revenue melonjak

MENGHAPUS SELURUH transaksi. Untuk mengembalikan data 6 kasus penerimaan:
    python scripts/seed_transactions.py
"""
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.core.config import MIN_CATEGORY_BASELINE, MODIFIED_Z_THRESHOLD, WIB
from app.db.session import SessionLocal
from app.engine import statistics as stats

random.seed(42)  # deterministik: dua kali jalan menghasilkan angka identik

CATEGORY = "Payroll & Benefits"
NORMAL_COUNT = 20
BASE_AMOUNT = 20_000_000
VARIATION = 0.01                 # +/-1%
DEFAULT_OUTLIER = 45_000_000
ANCHOR = datetime(2026, 8, 1, tzinfo=WIB)

# User yang SUDAH ADA. Skrip ini tidak pernah membuat atau menghapus user.
PAYROLL_INPUTTERS = [9, 10, 11]


def to_weekday(dt: datetime) -> datetime:
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt


def insert(db, *, date, amount, description, user_id):
    return db.execute(text("""
        INSERT INTO transactions
            (transaction_date, created_at, amount, type, category,
             description, vendor_id, input_by_user_id)
        VALUES (:d, :d, :a, 'expense', :c, :desc, NULL, :u)
        RETURNING id
    """), {"d": date, "a": amount, "c": CATEGORY,
           "desc": description, "u": user_id}).scalar()


REVENUE_BASE = 800_000_000
REVENUE_DRIFT = 1.01      # ~1% per bulan: stagnan
REVENUE_BOOM = 1.48       # lonjakan di bulan outlier


def seed_revenue(db, months: list[datetime], boom_month: str | None):
    """
    Mengisi monthly_revenue untuk seluruh bulan payroll.

    Kalau `boom_month` diberikan, revenue melonjak di bulan itu DAN BERTAHAN
    di bulan-bulan sesudahnya. Lonjakan yang tidak bertahan akan terbaca sebagai
    kejatuhan pada bulan berikutnya, dan justru memberatkan transaksi yang
    seharusnya dibela.
    """
    keys = sorted({m.strftime("%Y-%m-01") for m in months})
    for i, key in enumerate(keys):
        value = REVENUE_BASE * (REVENUE_DRIFT ** i)
        if boom_month and key >= boom_month:
            value *= REVENUE_BOOM
        db.execute(text("""
            INSERT INTO monthly_revenue (month, revenue) VALUES (:m, :r)
            ON CONFLICT (month) DO UPDATE SET revenue = EXCLUDED.revenue
        """), {"m": key, "r": round(value, 2)})
    return keys


def seed(outlier_amount: int, boom: bool = False):
    db = SessionLocal()
    try:
        users = [r[0] for r in db.execute(text("SELECT id FROM users ORDER BY id"))]
        missing = [u for u in PAYROLL_INPUTTERS if u not in users]
        if missing:
            raise SystemExit(f"User id {missing} tidak ada. Tersedia: {users}")

        print("Mengosongkan transaksi (users & vendors TIDAK disentuh)...")
        db.execute(text("DELETE FROM findings"))
        db.execute(text("DELETE FROM transaction_analysis"))
        db.execute(text("DELETE FROM transactions"))
        db.execute(text("DELETE FROM monthly_revenue"))
        db.commit()

        # --- 20 payroll bulanan, jam kerja, nominal rapat --------------------
        normal_ids, amounts, months = [], [], []
        for i in range(NORMAL_COUNT):
            month = ANCHOR - timedelta(days=30 * (NORMAL_COUNT - i))
            date = to_weekday(month.replace(hour=10, minute=0, second=0, microsecond=0))
            months.append(date)
            amount = round(random.uniform(BASE_AMOUNT * (1 - VARIATION),
                                          BASE_AMOUNT * (1 + VARIATION)), 2)
            amounts.append(amount)
            normal_ids.append(insert(
                db, date=date, amount=amount, user_id=random.choice(PAYROLL_INPUTTERS),
                description=f"Gaji bulanan periode {date:%Y-%m}"))

        # --- 1 outlier, jam kerja juga -------------------------------------
        # Sengaja di jam kerja: kalau di luar jam kerja, timing_outside_hours
        # ikut menyala dan skornya bukan lagi hasil z-score murni.
        outlier_date = to_weekday(
            (ANCHOR - timedelta(days=5)).replace(hour=11, minute=0, second=0, microsecond=0))
        outlier_id = insert(
            db, date=outlier_date, amount=outlier_amount, user_id=PAYROLL_INPUTTERS[0],
            description=("Pembayaran gaji beserta bonus kinerja karyawan "
                         "atas pencapaian penjualan periode ini"))
        months.append(outlier_date)

        boom_month = outlier_date.strftime("%Y-%m-01") if boom else None
        seed_revenue(db, months, boom_month)
        db.commit()

        report(amounts, outlier_amount, outlier_id, normal_ids, outlier_date, boom)
    finally:
        db.close()


def report(amounts, outlier_amount, outlier_id, normal_ids, outlier_date, boom=False):
    """Menghitung ulang dengan modul yang sama dengan scoring engine."""
    from app.engine.detectors import _z_points
    from app.tools.financial import get_sales_trend

    print(f"\nDitanam {len(normal_ids)} payroll normal + 1 outlier "
          f"= {len(normal_ids) + 1} transaksi.\n")

    result = stats.evaluate(float(outlier_amount), [], [float(a) for a in amounts])
    print("OUTLIER")
    print(f"  id                 : {outlier_id}")
    print(f"  tanggal (WIB)      : {outlier_date:%Y-%m-%d %H:%M} ({outlier_date:%A})")
    print(f"  nominal            : {outlier_amount:,.0f}")
    print(f"  baseline           : {result.method} (n={result.n_baseline})")
    print(f"  median             : {result.median:,.2f}")
    print(f"  MAD                : {result.mad:,.2f}")
    print(f"  modified z-score   : {result.computed_value}")
    print(f"  ambang             : {MODIFIED_Z_THRESHOLD}")
    if result.is_anomaly:
        pts = _z_points(result.computed_value)
        band = "Low Risk" if pts < 40 else "Medium Risk" if pts < 60 else "High Risk"
        print(f"  -> KANDIDAT, z_score_anomaly +{pts}  (base {pts} = {band})")
    else:
        print("  -> BUKAN kandidat")

    # Kontrol negatif: satu payroll normal tidak boleh ikut tertandai.
    others = [float(a) for a in amounts[1:]] + [float(outlier_amount)]
    normal = stats.evaluate(float(amounts[0]), [], others)
    print(f"\nKONTROL NEGATIF (satu payroll normal, id {normal_ids[0]})")
    print(f"  nominal            : {amounts[0]:,.0f}")
    print(f"  modified z-score   : {normal.computed_value}")
    print(f"  -> {'KANDIDAT (SALAH!)' if normal.is_anomaly else 'bukan kandidat (benar)'}")

    # Nominal yang diperlukan untuk mencapai tiap pita skor, dihitung dari MAD
    # yang benar-benar terbentuk — supaya pengujian per pita tidak perlu menebak.
    if result.mad:
        print("\nNominal outlier untuk menyentuh tiap pita skor:")
        for z, points in [(MODIFIED_Z_THRESHOLD, 20), (5, 30), (10, 40), (20, 50)]:
            need = result.median + (z * result.mad / stats.MAD_SCALE)
            print(f"  z >= {z:<5} -> +{points}   nominal >= {need:,.0f}")
        print(f"\n  python scripts/z_score_seed_test.py <nominal>")

    # Konteks bisnis: inilah satu-satunya yang berbeda antara kedua mode.
    trend = get_sales_trend(outlier_date.strftime("%Y-%m"))
    print(f"\nKONTEKS REVENUE bulan {outlier_date:%Y-%m}  "
          f"[mode: {'BOOM' if boom else 'stagnan'}]")
    if trend.get("trend_percentage") is not None:
        print(f"  revenue            : {trend['revenue']:,.0f}")
        print(f"  bulan sebelumnya   : {trend['previous_month_revenue']:,.0f}")
        print(f"  pertumbuhan        : {trend['trend_percentage']:+.2f}%")
        print(f"  status             : {trend['status']}")
    else:
        print(f"  {trend.get('message', trend.get('status'))}")

    print("\nYANG DIHARAPKAN dari Agent 3:")
    if boom:
        print("  Bonus terjustifikasi oleh lonjakan penjualan di bulan yang sama.")
        print("  -> adjustment NEGATIF, skor akhir TURUN dari base.")
        print("  -> Agent 3 wajib menyebut angka revenue di atas. Kalau tidak,")
        print("     ia menebak, bukan memverifikasi.")
    else:
        print("  Klaim bonus tidak didukung data penjualan.")
        print("  -> adjustment 0 atau POSITIF, skor akhir TETAP atau NAIK.")

    print("\nRentang analisis yang mencakup seluruh data:")
    print(f"  {(ANCHOR - timedelta(days=30 * 21)):%Y-%m-%d}  s/d  {ANCHOR:%Y-%m-%d}")
    print(f"\nHanya kategori '{CATEGORY}' tanpa vendor, jadi trigger duplikat, "
          f"split payment, dan vendor mati sendiri.\nSeluruhnya jam kerja, jadi "
          f"timing juga mati. Yang tersisa murni z-score.")
    if len(normal_ids) < MIN_CATEGORY_BASELINE:
        print(f"\nPERINGATAN: baseline kategori butuh minimal "
              f"{MIN_CATEGORY_BASELINE}; sekarang {len(normal_ids)}.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    amount = int(args[0]) if args else DEFAULT_OUTLIER
    seed(amount, boom="--boom" in sys.argv)
