from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, Date, Boolean, ForeignKey, Text
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .session import Base


class User(Base):
    """
    HANYA-BACA. Tabel `users` dimiliki dan ditulis oleh aplikasi Next.js.

    Agent server tidak pernah INSERT / UPDATE / DELETE / ALTER tabel ini.
    Kolom di bawah adalah kolom yang benar-benar ada di database; definisi lama
    (username / password / role / department) tidak pernah ada dan membuat setiap
    query yang menyentuh users gagal dengan UndefinedColumn.

    Peran diturunkan dari `is_admin` lewat app.core.roles.role_of().
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False)
    fullname = Column(String, nullable=False)
    is_admin = Column(Boolean, nullable=False)
    is_active = Column(Boolean, nullable=False)


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True)
    vendor_name = Column(String(100), nullable=False)
    bank_account = Column(String(50), nullable=False)
    join_date = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(20), default="active")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    # WARISAN. Kolom ini dimiliki aplikasi Next.js dan NOT NULL, jadi tidak bisa
    # dihapus dari sini. Agent server TIDAK PERNAH membacanya. Seed mengisinya
    # sama dengan created_at semata-mata agar constraint terpenuhi.
    transaction_date = Column(DateTime(timezone=True), nullable=False)
    # SATU-SATUNYA waktu yang dipakai sistem ini.
    #
    # Transaksi masuk dari API bank: saat bank mencatatnya ITULAH saat transaksi
    # terjadi. Tidak ada tanggal terpisah yang diketik pengguna, jadi tidak ada
    # dua makna waktu yang perlu didamaikan dan tidak ada yang bisa dipalsukan
    # dengan mengetik.
    #
    # Dipakai untuk SEGALANYA: aturan jam kerja, jendela duplikat 24 jam,
    # jendela split payment 7 hari, pengelompokan bulan, dan penyaringan rentang.
    #
    # timestamptz. Konvensi baca: SELALU konversi ke WIB sebelum menilai jam,
    # karena server database ber-timezone GMT. Lihat app.core.config.WIB.
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    amount = Column(Numeric(15, 2), nullable=False)
    type = Column(String(10), nullable=False)
    category = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    # Pembeda duplicate payment (faktur sama dibayar dua kali) dari split payment
    # (faktur berbeda, dipecah untuk menghindari ambang persetujuan).
    invoice_no = Column(String(50), nullable=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    input_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    vendor = relationship("Vendor")
    user = relationship("User")


class AnalysisRun(Base):
    """Satu kali eksekusi POST /api/analyze atas sebuah rentang tanggal."""
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False)  # running | complete | error


class Finding(Base):
    """
    Satu temuan yang diterbitkan sebuah run.

    `payload` menyimpan provenance, evidence, skor per-trigger, dan baseline yang
    dipakai menilainya — inilah alasan proyek ini memilih PostgreSQL/JSONB.
    """
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("analysis_runs.id"))
    transaction_id = Column(Integer, ForeignKey("transactions.id"))
    related_transaction_ids = Column(ARRAY(Integer))
    final_risk_score = Column(Integer, nullable=False)
    risk_level = Column(String(20), nullable=False)
    payload = Column(JSONB, nullable=False)


class MonthlyRevenue(Base):
    """
    Revenue bulanan, dipakai Agent 3 untuk membatalkan false positive:
    lonjakan biaya yang sejalan dengan pertumbuhan revenue bukan anomali.
    """
    __tablename__ = "monthly_revenue"

    month = Column(Date, primary_key=True)
    revenue = Column(Numeric(15, 2), nullable=False)
