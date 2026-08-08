"""
Seed data berlabel untuk Sentinel.

ATURAN KERAS
------------
1. Tabel `users` TIDAK PERNAH disentuh. Tidak ada INSERT, UPDATE, apalagi DELETE.
   `input_by_user_id` diambil dari id yang sudah ada di database.
   (Versi lama berkas ini menjalankan `db.query(User).delete()` yang akan
   menghapus seluruh akun login.)
2. `vendors` lama tidak pernah dihapus. Vendor baru hanya ditambahkan kalau
   namanya belum ada.
3. Hanya `transactions` dan `monthly_revenue` yang dikosongkan lalu diisi ulang,
   sehingga skrip ini dapat dijalankan berulang dengan hasil identik.
4. Semua waktu SADAR-ZONA WIB. Kolom transaction_date bertipe timestamptz dan
   server database ber-timezone GMT; menulis waktu naif akan bergeser 7 jam.

Menanam 6 kasus uji dengan jawaban yang sudah diketahui. Lihat PLAN.md §10.
"""
import os
import sys
import json
import random
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.core.config import WIB, APPROVAL_THRESHOLD

random.seed(42)  # deterministik: dua kali jalan menghasilkan data identik

# --- User yang SUDAH ADA di database. Jangan pernah membuat yang baru. -------
LEAD_IDS = [7, 8]           # is_admin = true  -> finance_lead
STAFF_IDS = [9, 10, 11]     # is_admin = false -> finance_staff

# --- Titik acuan waktu ------------------------------------------------------
# Semua tanggal dihitung mundur dari sini agar seed selalu jatuh di rentang yang
# masuk akal untuk dianalisis, tanpa bergantung pada tanggal hari ini.
ANCHOR = datetime(2026, 8, 1, 0, 0, 0, tzinfo=WIB)


def wib(days_before_anchor: int, hour: int, minute: int = 0) -> datetime:
    """Waktu WIB, sekian hari sebelum ANCHOR."""
    d = ANCHOR - timedelta(days=days_before_anchor)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0)


def to_weekday(dt: datetime) -> datetime:
    """Geser maju ke Senin kalau jatuh di akhir pekan."""
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt


def to_weekend(dt: datetime) -> datetime:
    """Geser maju ke Sabtu terdekat."""
    while dt.weekday() != 5:
        dt += timedelta(days=1)
    return dt


def get_or_create_vendor(db, name: str, status: str) -> int:
    row = db.execute(
        text("SELECT id FROM vendors WHERE vendor_name = :n"), {"n": name}
    ).fetchone()
    if row:
        db.execute(
            text("UPDATE vendors SET status = :s WHERE id = :i"),
            {"s": status, "i": row[0]},
        )
        return row[0]
    return db.execute(
        text("""
            INSERT INTO vendors (vendor_name, bank_account, join_date, status)
            VALUES (:n, :b, now(), :s) RETURNING id
        """),
        {"n": name, "b": f"{random.randint(1000000000, 9999999999)}", "s": status},
    ).scalar()


def insert_tx(db, *, date, amount, ttype, category, description,
              vendor_id, user_id, invoice_no=None) -> int:
    return db.execute(
        text("""
            INSERT INTO transactions
                (transaction_date, amount, type, category, description,
                 invoice_no, vendor_id, input_by_user_id)
            VALUES (:d, :a, :t, :c, :desc, :inv, :v, :u)
            RETURNING id
        """),
        {"d": date, "a": amount, "t": ttype, "c": category, "desc": description,
         "inv": invoice_no, "v": vendor_id, "u": user_id},
    ).scalar()


def seed():
    db = SessionLocal()
    cases = {}
    try:
        users = [r[0] for r in db.execute(
            text("SELECT id FROM users ORDER BY id")).fetchall()]
        missing = [u for u in LEAD_IDS + STAFF_IDS if u not in users]
        if missing:
            raise SystemExit(
                f"User id {missing} tidak ada di database. Seed dibatalkan agar "
                f"tidak membuat user baru. User yang tersedia: {users}"
            )

        print("Mengosongkan transactions & monthly_revenue (users/vendors TIDAK disentuh)...")
        db.execute(text("DELETE FROM findings"))
        db.execute(text("DELETE FROM analysis_runs"))
        db.execute(text("DELETE FROM transactions"))
        db.execute(text("DELETE FROM monthly_revenue"))
        db.commit()

        v_atk = get_or_create_vendor(db, "CV. ATK Sejahtera", "active")
        v_it = get_or_create_vendor(db, "PT. Solusi Teknologi Makmur", "active")
        v_ops = get_or_create_vendor(db, "PT Ashiap Kita", "active")
        v_fake = get_or_create_vendor(db, "CV Fiktif Sukses Makmur", "high_risk")
        v_split = get_or_create_vendor(db, "PT Konsultan Bayangan", "active")
        db.commit()
        print(f"Vendor: atk={v_atk} it={v_it} ops={v_ops} fiktif={v_fake} split={v_split}")

        # ------------------------------------------------------------------
        # KASUS C — 30 transaksi ATK rutin. Baseline sekaligus kontrol negatif:
        # tidak satu pun boleh muncul sebagai finding.
        # ------------------------------------------------------------------
        case_c_ids = []
        for i in range(30):
            d = to_weekday(wib(150 - i * 4, random.randint(9, 16), random.randint(0, 59)))
            case_c_ids.append(insert_tx(
                db, date=d, amount=round(random.uniform(1_000_000, 3_000_000), 2),
                ttype="expense", category="Office Supplies",
                description="Pembelian ATK rutin kantor",
                vendor_id=v_atk, user_id=random.choice(STAFF_IDS)))
        cases["C"] = {"transaction_ids": case_c_ids}

        # ------------------------------------------------------------------
        # KASUS A — lonjakan 45 jt terhadap baseline ATK ~2 jt.
        # Jam kerja, vendor lama: satu-satunya pemicu adalah z-score.
        # Ditaruh di bulan yang revenue-nya STAGNAN agar Agent 3 tidak menemukan
        # pembenaran dan menaikkan skornya ke High.
        # ------------------------------------------------------------------
        a_date = to_weekday(wib(20, 16, 45))
        case_a_id = insert_tx(
            db, date=a_date, amount=45_000_000, ttype="expense",
            category="Office Supplies",
            description="Pembelian borongan ATK karena demand operasional sangat tinggi akhir bulan",
            vendor_id=v_atk, user_id=9)
        cases["A"] = {"transaction_ids": [case_a_id], "date_wib": str(a_date)}

        # ------------------------------------------------------------------
        # Baseline IT untuk Kasus D.
        # ------------------------------------------------------------------
        for i in range(18):
            d = to_weekday(wib(160 - i * 8, random.randint(9, 16), random.randint(0, 59)))
            insert_tx(db, date=d, amount=round(random.uniform(5_000_000, 15_000_000), 2),
                      ttype="expense", category="IT Services",
                      description="Biaya layanan infrastruktur IT bulanan",
                      vendor_id=v_it, user_id=random.choice(STAFF_IDS))

        # ------------------------------------------------------------------
        # KASUS D — false positive yang HARUS dibatalkan Agent 3.
        # Lonjakan besar, tetapi jatuh di bulan dengan revenue naik ~42%.
        # ------------------------------------------------------------------
        d_date = to_weekday(wib(50, 14, 20))
        case_d_id = insert_tx(
            db, date=d_date, amount=120_000_000, ttype="expense",
            category="IT Services",
            description="Penambahan kapasitas server dan lisensi untuk menopang lonjakan order",
            vendor_id=v_it, user_id=random.choice(STAFF_IDS))
        cases["D"] = {"transaction_ids": [case_d_id], "date_wib": str(d_date)}

        # ------------------------------------------------------------------
        # Noise operasional + KASUS E.
        # ------------------------------------------------------------------
        for i in range(12):
            d = to_weekday(wib(140 - i * 10, random.randint(9, 16), random.randint(0, 59)))
            insert_tx(db, date=d, amount=round(random.uniform(2_000_000, 3_000_000), 2),
                      ttype="expense", category="Operational",
                      description="Biaya operasional rutin",
                      vendor_id=v_ops, user_id=random.choice(STAFF_IDS))

        # KASUS E — lembur yang sah. Di luar jam kerja (21:30) tetapi nominalnya
        # wajar dan vendornya bersih. Membuktikan timing = amplifier, bukan
        # pemicu kandidat: transaksi ini TIDAK BOLEH menjadi finding.
        e_date = to_weekday(wib(15, 21, 30))
        case_e_id = insert_tx(
            db, date=e_date, amount=2_400_000, ttype="expense",
            category="Operational",
            description="Pembayaran biaya operasional, diinput saat lembur tutup buku",
            vendor_id=v_ops, user_id=random.choice(STAFF_IDS))
        cases["E"] = {"transaction_ids": [case_e_id], "date_wib": str(e_date)}

        # ------------------------------------------------------------------
        # KASUS B — duplicate payment. Faktur SAMA dibayar dua kali, vendor
        # berstatus high_risk, pukul 23:15 WIB.
        # ------------------------------------------------------------------
        b_invoice = "INV-2026-0731"
        b1 = wib(12, 23, 15)
        b2 = b1 + timedelta(hours=2)
        case_b_ids = [
            insert_tx(db, date=b1, amount=25_000_000, ttype="expense",
                      category="Consulting", invoice_no=b_invoice,
                      description=f"Pembayaran jasa konsultasi IT ({b_invoice})",
                      vendor_id=v_fake, user_id=11),
            insert_tx(db, date=b2, amount=25_000_000, ttype="expense",
                      category="Consulting", invoice_no=b_invoice,
                      description=f"Pembayaran jasa konsultasi IT ({b_invoice})",
                      vendor_id=v_fake, user_id=11),
        ]
        cases["B"] = {"transaction_ids": case_b_ids, "invoice_no": b_invoice,
                      "date_wib": str(b1)}

        # ------------------------------------------------------------------
        # KASUS B2 — split payment. Faktur BERBEDA, masing-masing 24 jt (tepat
        # di bawah ambang 25 jt), total 72 jt, dalam 5 hari, diinput STAFF.
        #
        # Jarak antar transaksi sengaja >= 24 jam (hari ke-1, 3, 5) supaya tidak
        # ikut memicu aturan duplicate — nominalnya sama, jadi kalau berdekatan
        # akan tertangkap sebagai duplikat dan kasusnya jadi ambigu.
        # ------------------------------------------------------------------
        case_b2_ids = []
        for i, day_offset in enumerate([40, 38, 36]):
            d = to_weekday(wib(day_offset, 10 + i, 15))
            case_b2_ids.append(insert_tx(
                db, date=d, amount=24_000_000, ttype="expense",
                category="Consulting", invoice_no=f"INV-2026-06{i + 1:02d}",
                description=f"Pembayaran jasa konsultasi tahap {i + 1}",
                vendor_id=v_split, user_id=10))
        cases["B2"] = {
            "transaction_ids": case_b2_ids,
            "total": 72_000_000,
            "approval_threshold": APPROVAL_THRESHOLD,
        }

        # ------------------------------------------------------------------
        # monthly_revenue — 6 bulan.
        # Bulan Kasus A stagnan (+1%), bulan Kasus D tumbuh (+42%).
        # ------------------------------------------------------------------
        a_month = a_date.strftime("%Y-%m-01")
        d_month = d_date.strftime("%Y-%m-01")
        base = 800_000_000
        months = [(ANCHOR - timedelta(days=30 * (5 - i))).strftime("%Y-%m-01")
                  for i in range(6)]

        # Dasar: tumbuh ~1% per bulan (stagnan). Lalu satu langkah naik +42% di
        # bulan Kasus D yang BERTAHAN di bulan-bulan berikutnya.
        #
        # Kenaikan harus bertahan, bukan sekadar puncak sesaat: kalau revenue
        # turun lagi setelahnya, bulan Kasus A justru terbaca 'declining' dan
        # kasusnya berubah makna. Yang dibutuhkan Kasus A adalah bulan STAGNAN.
        revenues = {}
        for i, m in enumerate(months):
            value = base + i * 8_000_000
            if m >= d_month:
                value *= 1.42
            revenues[m] = round(value, 2)

        for month, revenue in sorted(revenues.items()):
            db.execute(text(
                "INSERT INTO monthly_revenue (month, revenue) VALUES (:m, :r) "
                "ON CONFLICT (month) DO UPDATE SET revenue = EXCLUDED.revenue"
            ), {"m": month, "r": revenue})

        cases["A"]["revenue_month"] = a_month
        cases["D"]["revenue_month"] = d_month
        db.commit()

        total = db.execute(text("SELECT count(*) FROM transactions")).scalar()
        print(f"\nSeed selesai: {total} transaksi, {len(revenues)} bulan revenue.")
        print(f"  A  z-spike 45jt          tx={cases['A']['transaction_ids']}  bulan={a_month} (stagnan)")
        print(f"  B  duplicate {b_invoice} tx={case_b_ids}")
        print(f"  B2 split 3x24jt          tx={case_b2_ids}")
        print(f"  C  30 ATK rutin          tx={len(case_c_ids)} baris")
        print(f"  D  lonjakan terjustifikasi tx={cases['D']['transaction_ids']}  bulan={d_month} (+42%)")
        print(f"  E  lembur sah 21:30      tx={cases['E']['transaction_ids']}")

        write_expectations(cases)
    finally:
        db.close()


def write_expectations(cases: dict):
    """Jawaban yang diketahui, dipakai untuk kalibrasi bobot di Fase 3."""
    out = {
        "generated_by": "scripts/seed_transactions.py",
        "timezone": "Asia/Jakarta",
        "approval_threshold": APPROVAL_THRESHOLD,
        "cases": [
            {
                "id": "A", "name": "Lonjakan pengeluaran",
                "transaction_ids": cases["A"]["transaction_ids"],
                "expected_triggers": ["z_score_anomaly"],
                "expected_base_score": 50,
                "expected_adjustment": "positive",
                "expected_band": "High Risk",
                "why": "45jt vs baseline vendor ~2jt; revenue bulan itu stagnan sehingga klaim 'demand tinggi' tidak terdukung.",
            },
            {
                "id": "B", "name": "Duplicate payment",
                "transaction_ids": cases["B"]["transaction_ids"],
                "invoice_no": cases["B"]["invoice_no"],
                "expected_triggers": ["duplicate_confirmed", "vendor_flagged", "timing_outside_hours"],
                "expected_base_score": 80,
                "expected_adjustment": "zero",
                "expected_band": "Critical Risk",
                "why": "invoice_no sama dibayar dua kali; vendor high_risk; 23:15 WIB. Critical tercapai tanpa bantuan LLM.",
            },
            {
                "id": "B2", "name": "Split payment",
                "transaction_ids": cases["B2"]["transaction_ids"],
                "expected_triggers": ["split_payment", "role_bypass"],
                "expected_base_score": 60,
                "expected_adjustment": "zero",
                "expected_band": "High Risk",
                "why": "3 x 24jt (pita 20-25jt) total 72jt melewati ambang 25jt, diinput finance_staff.",
            },
            {
                "id": "C", "name": "Transaksi normal",
                "transaction_ids": cases["C"]["transaction_ids"],
                "expected_triggers": [],
                "expected_band": None,
                "expected_finding": False,
                "why": "Tidak ada aturan kandidat yang terpicu; tidak boleh masuk pipeline sama sekali.",
            },
            {
                "id": "D", "name": "False positive yang harus dibatalkan",
                "transaction_ids": cases["D"]["transaction_ids"],
                "expected_triggers": ["z_score_anomaly"],
                "expected_base_score": 50,
                "expected_adjustment": "negative",
                "expected_band": "Low Risk",
                "why": "Lonjakan besar tetapi revenue bulan itu naik ~42%; Agent 3 wajib menurunkan skor dan menyebut angka revenue.",
            },
            {
                "id": "E", "name": "Lembur yang sah",
                "transaction_ids": cases["E"]["transaction_ids"],
                "expected_triggers": [],
                "expected_band": None,
                "expected_finding": False,
                "why": "Di luar jam kerja (21:30) tetapi nominal wajar dan vendor bersih. Membuktikan timing adalah amplifier, bukan pemicu kandidat.",
            },
        ],
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_expectations.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nEkspektasi ditulis ke {path}")


if __name__ == "__main__":
    seed()
