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


class Finding(Base):
    """
    Satu temuan. SATU per transaksi, selamanya.

    `transaction_id` unik: transaksi yang sudah punya temuan tidak akan pernah
    jadi kandidat lagi. Itu yang menghentikan penumpukan — sebelumnya tiap kali
    /analyze dijalankan seluruh temuan dibuat ulang, sehingga satu transaksi
    tersimpan lima kali dengan tiga skor berbeda.

    Isinya dipecah tiga karena punya pembaca dan umur yang berbeda:

        description  LLM     kalimat pertama yang dibaca orang keuangan
        evidence     PYTHON  trigger + median/MAD/ambang/n_baseline + skor
        analysis     LLM     narasi 3 agen, verdict, penyesuaian semantik

    `analysis` nullable dengan sengaja: saat LLM gagal (mis. kuota habis),
    `evidence` dan `risk_score` tetap terbit dan temuannya tetap sah.
    """
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"),
                            unique=True, nullable=False)
    # Seluruh transaksi yang tercakup temuan ini, termasuk jangkarnya.
    # Dipakai untuk memeriksa apakah sebuah pelanggaran sudah pernah dilaporkan.
    related_transaction_ids = Column(ARRAY(Integer), nullable=False)

    risk_score = Column(Integer, nullable=False)
    risk_level = Column(String(10), nullable=False)   # kode, lihat core.constants

    description = Column(Text, nullable=False)
    evidence = Column(JSONB, nullable=False)
    analysis = Column(JSONB, nullable=True)

    resolution = Column(String(20), nullable=True)     # NULL = belum ditangani
    resolution_note = Column(Text, nullable=True)
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    # Terisi saat grup bertambah anggota dan skornya dihitung ulang tanpa LLM.
    # UI memakainya untuk menandai narasi mungkin tertinggal dari angka.
    updated_at = Column(DateTime(timezone=True), nullable=True)


class TransactionAnalysis(Base):
    """
    Penanda transaksi mana yang sudah diperiksa, dan hasilnya apa.

    Tanpa tabel ini, "tidak ada di findings" berarti dua hal sekaligus: sudah
    diperiksa dan bersih, atau pemeriksaannya gagal. Untuk alat audit keduanya
    tidak boleh terlihat sama — kesalahan yang sama sudah kita hindari pada
    `insufficient_baseline`.

    Ini juga yang membuat angka "64 aman" di halaman punya arti.
    """
    __tablename__ = "transaction_analysis"

    transaction_id = Column(Integer, ForeignKey("transactions.id"),
                            primary_key=True)
    analyzed_at = Column(DateTime(timezone=True), nullable=False,
                         server_default=func.now())
    status = Column(String(12), nullable=False)   # clean | flagged | failed
    error = Column(Text, nullable=True)
