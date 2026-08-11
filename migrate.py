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
`transactions` juga dimiliki aplikasi Next.js (Drizzle). Perubahan di bawah harus
sejalan dengan berkas skema Drizzle, kalau tidak migrasi berikutnya dari sana
akan mengembalikannya tanpa peringatan:
  - kolom `created_at` (timestamptz, DEFAULT now()) — satu-satunya waktu transaksi
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
    CREATE TABLE IF NOT EXISTS findings (
        id                      SERIAL PRIMARY KEY,
        transaction_id          INT UNIQUE NOT NULL REFERENCES transactions(id),
        related_transaction_ids INT[] NOT NULL,
        risk_score              INT NOT NULL,
        risk_level              VARCHAR(10) NOT NULL,
        description             TEXT NOT NULL,
        evidence                JSONB NOT NULL,
        analysis                JSONB,
        resolution              VARCHAR(20),
        resolution_note         TEXT,
        resolved_by             INT REFERENCES users(id),
        resolved_at             TIMESTAMPTZ,
        created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at              TIMESTAMPTZ,
        CONSTRAINT findings_risk_level_chk
            CHECK (risk_level IN ('low','medium','high','critical')),
        CONSTRAINT findings_resolution_chk
            CHECK (resolution IS NULL OR resolution IN
                   ('justified','false_positive','confirmed_fraud','escalated'))
    )
    """,
    # Membedakan "sudah diperiksa dan bersih" dari "belum pernah diperiksa".
    """
    CREATE TABLE IF NOT EXISTS transaction_analysis (
        transaction_id INT PRIMARY KEY REFERENCES transactions(id),
        analyzed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        status         VARCHAR(12) NOT NULL,
        error          TEXT,
        CONSTRAINT ta_status_chk CHECK (status IN ('clean','flagged','failed'))
    )
    """,
    # Menahan dua backfill berjalan bersamaan.
    #
    # Menggantikan pg_try_advisory_lock(), yang tidak bisa dipakai di endpoint
    # `-pooler` (PgBouncer transaction pooling) dan tidak pernah terlepas karena
    # Session.close() hanya mengembalikan koneksi ke pool. Lihat app/core/locking.py.
    #
    # CHECK (id = 1) membuat "hanya boleh ada satu kunci" jadi sifat skema, bukan
    # kesepakatan yang harus diingat tiap pemanggil.
    """
    CREATE TABLE IF NOT EXISTS backfill_lock (
        id           INT PRIMARY KEY,
        owner        TEXT NOT NULL,
        locked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT backfill_lock_single_row_chk CHECK (id = 1)
    )
    """,
]

# Perubahan pada tabel bersama `transactions`. Lihat PERINGATAN di atas.
SHARED_TABLE_DDL = [
    # Pembeda duplicate payment (faktur sama dibayar dua kali) dari split payment
    # (faktur berbeda, dipecah agar lolos ambang persetujuan).
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS invoice_no VARCHAR(50)",
    # SATU-SATUNYA waktu sebuah transaksi: kapan mutasi tercatat.
    #
    # Ditulis database, tidak pernah dikirim form — itulah yang membuat aturan
    # jam kerja tahan manipulasi. Kolom `transaction_date` yang dulu diketik
    # pengguna sudah dihapus; dua makna waktu berarti dua jawaban untuk satu
    # pertanyaan.
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
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
    # Indeks lama menempel pada kolom yang kini sudah dihapus.
    "DROP INDEX IF EXISTS idx_tx_date",
    "CREATE INDEX IF NOT EXISTS idx_tx_created_at ON transactions (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_tx_created_type ON transactions (created_at, type)",
    "CREATE INDEX IF NOT EXISTS idx_tx_invoice ON transactions (vendor_id, invoice_no)",
    # Dipakai tiap kali sebuah kandidat memeriksa "pelanggaran ini sudah pernah
    # dilaporkan belum?" lewat operator overlap array (&&).
    "CREATE INDEX IF NOT EXISTS idx_findings_covered ON findings USING GIN (related_transaction_ids)",
    "CREATE INDEX IF NOT EXISTS idx_findings_open ON findings (risk_score DESC) WHERE resolution IS NULL",
]


def drop_legacy_schema(conn) -> None:
    """
    Membuang skema lama, dan HANYA kalau memang skema lama yang ditemukan.

    `findings` dikenali dari kolom `payload` — kolom itu hanya ada di bentuk
    lama. Pemeriksaannya penting: `DROP TABLE findings` tanpa syarat akan
    menghapus temuan sungguhan setiap kali migrasi dijalankan, padahal migrasi
    memang dirancang untuk boleh dijalankan berulang.

    Dua tabel berikutnya dibuang tanpa syarat karena sudah tidak punya pembaca:

    `analysis_runs` — dengan transaction_id unik, run kedua tidak pernah
    menghasilkan temuan, jadi tabel dan riwayatnya kosong selamanya.

    `monthly_revenue` — pendapatan sekarang dibaca dari transactions
    (type='income'), sumber yang sama dengan biaya. Dua sumber angka pendapatan
    berarti satu pertanyaan bisa dijawab dua angka berbeda.
    """
    legacy = conn.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'findings' AND column_name = 'payload'
        )
    """)).scalar()

    if legacy:
        conn.execute(text("DROP TABLE IF EXISTS findings CASCADE"))
        print("  skema findings lama (kolom payload) dibuang")
    else:
        print("  skema findings lama tidak ditemukan, tidak ada yang dibuang")

    conn.execute(text("DROP TABLE IF EXISTS analysis_runs CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS monthly_revenue CASCADE"))


def run_migrations():
    print("Menghubungkan ke database...")

    with engine.begin() as conn:
        print("\n[0/3] Membersihkan skema lama")
        drop_legacy_schema(conn)

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
        for table in ("findings", "transaction_analysis", "backfill_lock"):
            exists = conn.execute(text(
                "SELECT to_regclass(:t) IS NOT NULL"
            ), {"t": f"public.{table}"}).scalar()
            print(f"  {table:20} {'ada' if exists else 'TIDAK ADA'}")
        gone = conn.execute(text(
            "SELECT to_regclass('public.analysis_runs') IS NULL")).scalar()
        print(f"  {'analysis_runs':20} {'dihapus' if gone else 'MASIH ADA'}")

        cols = {r[0]: r[1] for r in conn.execute(text("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = 'transactions'
        """))}
        print(f"  transactions.invoice_no       {cols.get('invoice_no', 'TIDAK ADA')}")
        print(f"  transactions.created_at       {cols.get('created_at', 'TIDAK ADA')}")

        checks = [r[0] for r in conn.execute(text("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'transactions'::regclass AND contype = 'c'
            ORDER BY conname
        """))]
        print(f"  CHECK constraints             {checks or 'TIDAK ADA'}")

    print("\nMigrasi selesai. Tabel `users` tidak disentuh.")


if __name__ == "__main__":
    run_migrations()
