"""
Migrasi skema untuk agent server.

ATURAN KERAS
------------
1. Tabel `users` TIDAK PERNAH disentuh. Tabel itu dimiliki dan ditulis aplikasi
   Next.js. Versi lama berkas ini menjalankan
   `ALTER TABLE users ADD COLUMN password ...` — itu dihapus dan tidak boleh
   dikembalikan.

2. `Base.metadata.create_all()` TIDAK dipakai. Fungsi itu akan ikut mencoba
   membuat `users`, `vendors`, dan `transactions` dari definisi model kita,
   padahal ketiganya sudah ada dan dimiliki aplikasi lain. Pembuatan tabel
   dilakukan eksplisit, hanya untuk tabel milik agent server.

3. Idempoten: aman dijalankan berulang kali.

PERINGATAN untuk tabel `transactions`
-------------------------------------
`transactions` juga dimiliki aplikasi Next.js (Drizzle). Dua perubahan di bawah
harus DISALIN ke berkas skema Drizzle, kalau tidak `drizzle-kit push` berikutnya
akan mengembalikannya tanpa peringatan:
  - `transaction_date` bertipe timestamptz  (varian withTimezone)
  - kolom `invoice_no`
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.db.session import engine

# Tabel milik agent server sepenuhnya. Boleh dibuat dan diubah dari sini.
OWNED_TABLES_DDL = [
    """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        id           SERIAL PRIMARY KEY,
        start_date   DATE NOT NULL,
        end_date     DATE NOT NULL,
        started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at  TIMESTAMPTZ,
        status       VARCHAR(20) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        id                      SERIAL PRIMARY KEY,
        run_id                  INT REFERENCES analysis_runs(id),
        transaction_id          INT REFERENCES transactions(id),
        related_transaction_ids INT[],
        final_risk_score        INT NOT NULL,
        risk_level              VARCHAR(20) NOT NULL,
        payload                 JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monthly_revenue (
        month    DATE PRIMARY KEY,
        revenue  NUMERIC(15,2) NOT NULL
    )
    """,
]

# Perubahan pada tabel bersama `transactions`. Lihat PERINGATAN di atas.
SHARED_TABLE_DDL = [
    # Pembeda duplicate payment (faktur sama dibayar dua kali) dari split payment
    # (faktur berbeda, dipecah agar lolos ambang persetujuan).
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS invoice_no VARCHAR(50)",
    # `type` bebas teks membuat WHERE type='expense' diam-diam membuang baris
    # yang tertulis 'Expense' — baris terbuang tidak pernah dianalisis.
    """
    DO $$ BEGIN
        ALTER TABLE transactions ADD CONSTRAINT tx_type_chk
            CHECK (type IN ('income','expense'));
    EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """,
    # Mengunci konvensi tanda. Tanpa ini "apakah expense disimpan negatif?"
    # tidak pernah punya jawaban pasti, dan median/MAD mewarisi keraguan itu.
    """
    DO $$ BEGIN
        ALTER TABLE transactions ADD CONSTRAINT tx_amount_chk
            CHECK (amount > 0);
    EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """,
]

# Indeks pendukung deteksi.
INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_tx_vendor_type ON transactions (vendor_id, type)",
    "CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions (transaction_date)",
    "CREATE INDEX IF NOT EXISTS idx_tx_invoice ON transactions (vendor_id, invoice_no)",
]


def run_migrations():
    print("Menghubungkan ke database...")

    with engine.begin() as conn:
        print("\n[1/3] Tabel milik agent server")
        for ddl in OWNED_TABLES_DDL:
            conn.execute(text(ddl))
            print("  ok:", ddl.strip().splitlines()[0].strip())

        print("\n[2/3] Perubahan pada tabel bersama `transactions`")
        for ddl in SHARED_TABLE_DDL:
            conn.execute(text(ddl))
            print("  ok:", ddl.strip().splitlines()[0].strip()[:70])

        print("\n[3/3] Indeks")
        for ddl in INDEX_DDL:
            conn.execute(text(ddl))
            print("  ok:", ddl.strip()[:70])

    # Laporan akhir supaya hasilnya bisa dilihat, bukan diasumsikan.
    with engine.connect() as conn:
        print("\nHasil:")
        for table in ("analysis_runs", "findings", "monthly_revenue"):
            exists = conn.execute(text(
                "SELECT to_regclass(:t) IS NOT NULL"
            ), {"t": f"public.{table}"}).scalar()
            print(f"  {table:18} {'ada' if exists else 'TIDAK ADA'}")

        cols = {r[0]: r[1] for r in conn.execute(text("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = 'transactions'
        """))}
        print(f"  transactions.invoice_no       {cols.get('invoice_no', 'TIDAK ADA')}")
        print(f"  transactions.transaction_date {cols.get('transaction_date')}")

        checks = [r[0] for r in conn.execute(text("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'transactions'::regclass AND contype = 'c'
            ORDER BY conname
        """))]
        print(f"  CHECK constraints             {checks or 'TIDAK ADA'}")

    print("\nMigrasi selesai. Tabel `users` tidak disentuh.")


if __name__ == "__main__":
    run_migrations()
