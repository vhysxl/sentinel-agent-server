"""
Seed terisolasi untuk menguji duplicate payment + jam pencatatan.

Dua trigger, dua sifat berbeda
------------------------------
    duplicate_confirmed  (+50)  FAKTA. Faktur yang sama dibayar dua kali.
                                Uang keluar dua kali untuk satu kewajiban.
    timing_outside_hours (+20)  AMPLIFIER. Dicatat di luar jam kerja.
                                Memperkuat kecurigaan, tidak pernah menciptakannya.

Isolasinya dijaga dengan sengaja
--------------------------------
    - Vendor punya 15 transaksi  -> vendor_new mati (ambangnya < 3)
    - Status vendor 'active'     -> vendor_flagged mati
    - Nominal sama dengan kebiasaan vendor -> z_score_anomaly mati
    - Nominalnya di luar pita split payment -> split_payment mati
Jadi kalau skor bergerak, penyebabnya hanya duplikat dan jam.

Satu waktu saja
---------------
Transaksi masuk dari API bank, jadi saat bank mencatatnya ITULAH saat transaksi
terjadi. Tidak ada tanggal terpisah yang diketik pengguna. `created_at` menjawab
segalanya: kapan terjadi, dan apakah di dalam jam kerja.

Mode
----
    python scripts/duplicate_seed_test.py              # dicatat 02:30 dini hari
    python scripts/duplicate_seed_test.py --weekend    # dicatat Sabtu siang
    python scripts/duplicate_seed_test.py --workhours  # dicatat jam kerja (timing mati)

Mode terakhir adalah pembandingnya: duplikat yang persis sama tanpa amplifier
jam, untuk melihat berapa poin yang benar-benar disumbang faktornya.

MENGHAPUS SELURUH transaksi. Untuk mengembalikan 6 kasus penerimaan:
    python scripts/seed_transactions.py
"""
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.core.config import (
    APPROVAL_THRESHOLD,
    NEW_VENDOR_MAX_TRANSACTIONS,
    SPLIT_BAND_FACTOR,
    WIB,
    WORK_END_HOUR,
    WORK_START_HOUR,
)
from app.db.session import SessionLocal

random.seed(7)

VENDOR_NAME = "PT Mitra Logistik Nusantara"
CATEGORY = "Logistics"
BASELINE_COUNT = 15
BASE_AMOUNT = 10_000_000
VARIATION = 0.15
DUPLICATE_AMOUNT = 10_000_000
INVOICE = "INV-2026-0815"
ANCHOR = datetime(2026, 8, 1, tzinfo=WIB)
STAFF_IDS = [9, 10, 11]


def to_weekday(dt):
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt


def to_saturday(dt):
    while dt.weekday() != 5:
        dt += timedelta(days=1)
    return dt


def get_or_create_vendor(db, name, status="active"):
    row = db.execute(text("SELECT id FROM vendors WHERE vendor_name = :n"),
                     {"n": name}).fetchone()
    if row:
        db.execute(text("UPDATE vendors SET status = :s WHERE id = :i"),
                   {"s": status, "i": row[0]})
        return row[0]
    return db.execute(text("""
        INSERT INTO vendors (vendor_name, bank_account, join_date, status)
        VALUES (:n, :b, now(), :s) RETURNING id
    """), {"n": name, "b": str(random.randint(1000000000, 9999999999)),
           "s": status}).scalar()


def insert(db, *, date, amount, description,
           vendor_id, user_id, invoice_no=None):
    """`date` ditulis ke created_at (dibaca sistem) sekaligus transaction_date
    (kolom warisan yang NOT NULL). Keduanya selalu sama."""
    return db.execute(text("""
        INSERT INTO transactions
            (transaction_date, created_at, amount, type, category,
             description, invoice_no, vendor_id, input_by_user_id)
        VALUES (:d, :d, :a, 'expense', :c, :desc, :inv, :v, :u)
        RETURNING id
    """), {"d": date, "a": amount, "c": CATEGORY,
           "desc": description, "inv": invoice_no,
           "v": vendor_id, "u": user_id}).scalar()


def seed(mode: str):
    db = SessionLocal()
    try:
        users = [r[0] for r in db.execute(text("SELECT id FROM users ORDER BY id"))]
        missing = [u for u in STAFF_IDS if u not in users]
        if missing:
            raise SystemExit(f"User id {missing} tidak ada. Tersedia: {users}")

        print("Mengosongkan transaksi (users & vendors TIDAK disentuh)...")
        for table in ("findings", "transaction_analysis", "transactions", "monthly_revenue"):
            db.execute(text(f"DELETE FROM {table}"))
        db.commit()

        vendor_id = get_or_create_vendor(db, VENDOR_NAME)
        db.commit()

        # --- Baseline: 15 transaksi wajar, semuanya dicatat di jam kerja ------
        amounts = []
        for i in range(BASELINE_COUNT):
            d = to_weekday((ANCHOR - timedelta(days=150 - i * 9))
                           .replace(hour=random.randint(9, 16), minute=random.randint(0, 59),
                                    second=0, microsecond=0))
            amount = round(random.uniform(BASE_AMOUNT * (1 - VARIATION),
                                          BASE_AMOUNT * (1 + VARIATION)), 2)
            amounts.append(amount)
            insert(db, date=d, amount=amount,
                   description=f"Biaya pengiriman rutin periode {d:%Y-%m}",
                   vendor_id=vendor_id, user_id=random.choice(STAFF_IDS))

        # --- Duplikat: faktur SAMA dibayar dua kali --------------------------
        base_day = to_weekday((ANCHOR - timedelta(days=10))
                              .replace(hour=10, minute=0, second=0, microsecond=0))

        if mode == "workhours":
            rec1 = base_day.replace(hour=10, minute=15)
            rec2 = base_day.replace(hour=14, minute=40)
            label = "jam kerja (amplifier jam MATI)"
        elif mode == "weekend":
            sat = to_saturday(base_day)
            rec1 = sat.replace(hour=14, minute=5)
            rec2 = sat.replace(hour=15, minute=50)
            label = "Sabtu siang"
        else:
            rec1 = base_day.replace(hour=2, minute=30)
            rec2 = base_day.replace(hour=3, minute=10)
            label = "dini hari 02:30"

        dup_ids = [
            insert(db, date=rec1, amount=DUPLICATE_AMOUNT,
                   description=f"Pembayaran jasa pengiriman ({INVOICE})",
                   invoice_no=INVOICE, vendor_id=vendor_id, user_id=STAFF_IDS[0]),
            insert(db, date=rec2, amount=DUPLICATE_AMOUNT,
                   description=f"Pembayaran jasa pengiriman ({INVOICE})",
                   invoice_no=INVOICE, vendor_id=vendor_id, user_id=STAFF_IDS[0]),
        ]

        # --- Kontrol negatif: dicatat dini hari, tapi tidak ada pelanggaran ---
        # Membuktikan jam TIDAK PERNAH menciptakan temuan sendirian.
        ctrl_date = to_weekday((ANCHOR - timedelta(days=6))
                               .replace(hour=11, minute=0, second=0, microsecond=0))
        ctrl_id = insert(
            db, date=ctrl_date.replace(hour=1, minute=45),
            amount=round(random.uniform(BASE_AMOUNT * 0.9, BASE_AMOUNT * 1.1), 2),
            description="Biaya pengiriman, diinput saat lembur tutup buku",
            invoice_no="INV-2026-0899", vendor_id=vendor_id,
            user_id=random.choice(STAFF_IDS))
        db.commit()

        report(db, vendor_id, amounts, dup_ids, ctrl_id, rec1, label, mode)
    finally:
        db.close()


def report(db, vendor_id, amounts, dup_ids, ctrl_id, rec1, label, mode):
    from app.db.models import Transaction
    from app.engine import detectors
    from app.engine.scoring import calculate_base_score, risk_decision

    total = BASELINE_COUNT + len(dup_ids) + 1
    print(f"\nDitanam {total} transaksi ke satu vendor.  Mode jam: {label}\n")

    print("KONFIGURASI ISOLASI")
    print(f"  transaksi vendor    : {total}  (> {NEW_VENDOR_MAX_TRANSACTIONS}, jadi vendor_new mati)")
    print(f"  status vendor       : active  (vendor_flagged mati)")
    print(f"  nominal duplikat    : {DUPLICATE_AMOUNT:,.0f}  (setara kebiasaan, z_score mati)")
    print(f"  pita split payment  : {APPROVAL_THRESHOLD * SPLIT_BAND_FACTOR:,.0f}"
          f"-{APPROVAL_THRESHOLD:,.0f}  (nominal di luar pita, split mati)")
    print(f"  jam kerja           : Senin-Jumat "
          f"{WORK_START_HOUR:02d}:00-{WORK_END_HOUR - 1:02d}:59 WIB")

    print(f"\nDUPLIKAT  id {dup_ids}")
    print(f"  faktur              : {INVOICE}  (SAMA pada kedua baris)")
    print(f"  tercatat dari bank  : {rec1:%Y-%m-%d %H:%M} ({rec1:%A})")

    for tid in dup_ids + [ctrl_id]:
        txn = db.query(Transaction).filter(Transaction.id == tid).first()
        triggers = detectors.run_all(db, txn)
        is_cand = detectors.is_candidate(triggers)
        tag = "DUPLIKAT" if tid in dup_ids else "KONTROL NEGATIF"
        print(f"\n{tag}  id {tid}")
        if not triggers:
            print("  (tidak ada trigger)")
        for t in triggers:
            amp = " [amplifier]" if t.amplifier_only else ""
            print(f"  +{t.points:<3} {t.code}{amp}")
        print(f"  -> {'KANDIDAT' if is_cand else 'BUKAN kandidat'}"
              f"{'  (benar: jam saja tidak cukup)' if not is_cand and tid == ctrl_id else ''}")

    # Skor grup: kedua baris duplikat digabung jadi SATU temuan.
    txns = [db.query(Transaction).filter(Transaction.id == i).first() for i in dup_ids]
    merged, seen = [], set()
    for txn in txns:
        for t in detectors.run_all(db, txn):
            key = (t.code, str(t.detail))
            if key not in seen:
                seen.add(key)
                merged.append(t)
    s = calculate_base_score(merged)
    print(f"\nSKOR GRUP (kedua baris duplikat = SATU temuan)")
    for t in s["objective_triggers"]:
        print(f"  +{t['points']:<3} {t['code']}")
    for t in s["suppressed_triggers"]:
        print(f"  ({t['code']} dibuang: {t['reason']})")
    print(f"  base = {s['base_risk_score']}  ->  {risk_decision(s['base_risk_score'])[0]}")

    if mode != "workhours":
        print(f"\n  Bandingkan: python scripts/duplicate_seed_test.py --workhours")
        print(f"  (duplikat yang sama tanpa amplifier jam)")

    print(f"\nRentang analisis: {(ANCHOR - timedelta(days=155)):%Y-%m-%d} s/d {ANCHOR:%Y-%m-%d}")


if __name__ == "__main__":
    mode = ("workhours" if "--workhours" in sys.argv
            else "weekend" if "--weekend" in sys.argv else "night")
    seed(mode)
