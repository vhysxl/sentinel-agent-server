"""
Kunci backfill berbasis baris.

Kenapa bukan advisory lock
--------------------------
Versi sebelumnya memakai `pg_try_advisory_lock()`. Dua hal membuatnya tidak bisa
dipakai di sini, dan keduanya sudah terbukti di produksi lewat 409 yang muncul
padahal tidak ada backfill yang berjalan:

1. Locknya tidak pernah lepas.
   `pg_try_advisory_lock()` mengambil kunci tingkat SESI, yang bertahan sampai
   dilepas eksplisit atau sampai koneksi Postgres benar-benar mati.
   `Session.close()` hanya mengembalikan koneksi ke pool SQLAlchemy — sesi
   Postgres di baliknya tetap hidup, jadi kuncinya ikut hidup. Backfill yang
   sudah selesai dengan sukses tetap meninggalkan kunci yang memblokir run
   berikutnya sampai `pool_recycle` kebetulan membuang koneksi itu.

2. DATABASE_URL memakai endpoint `-pooler` (PgBouncer, transaction pooling).
   Di mode itu satu koneksi klien tidak terikat ke satu backend Postgres, jadi
   kunci bisa diambil di backend A sementara panggilan berikutnya mendarat di
   backend B. Akibatnya dua arah sekaligus: penolakan palsu untuk run yang
   seharusnya boleh, DAN dua backfill bersamaan yang sama-sama lolos — persis
   yang kunci ini ditulis untuk dicegah.

Bentuk penggantinya
-------------------
Satu baris di tabel `backfill_lock`. Tidak ada state yang menempel pada koneksi,
jadi pooler tidak lagi berpengaruh: setiap operasi di bawah membuka sesi sendiri,
commit, lalu menutupnya.

Karena baris tidak hilang sendiri saat proses mati — kelebihan advisory lock yang
memang nyata dan kita kehilangannya di sini — kunci diberi DENYUT. Pemegang
memperbarui `heartbeat_at` tiap transaksi selesai, dan kunci yang denyutnya
berhenti lebih lama dari STALE_AFTER_SECONDS boleh diambil alih. Itu yang
mencegah satu proses yang di-kill meninggalkan kunci abadi.

`owner` bukan hiasan: release dan heartbeat menyertakannya, sehingga proses yang
sudah dianggap basi dan digantikan tidak bisa menghapus kunci milik penggantinya.
"""
import os
import socket
from uuid import uuid4

from sqlalchemy import text

from app.db.session import SessionLocal

# Tabel ini hanya pernah berisi satu baris. Dikunci lewat CHECK (id = 1) di
# migrate.py supaya "hanya satu backfill" jadi sifat skema, bukan sekadar
# kesepakatan antar pemanggil.
LOCK_ID = 1

# Kunci dianggap basi kalau denyutnya berhenti selama ini.
#
# Denyut hanya bisa terjadi di sela antar transaksi — saat LLM sedang dipanggil,
# tidak ada tempat untuk menyisipkannya. Jadi ambang ini harus melebihi durasi
# TERBURUK satu transaksi: 3 agen, masing-masing sampai MAX_ATTEMPTS percobaan
# dengan jeda bertambah (app/agents/llm.py). Lima menit memberi margin lebar.
#
# Sengaja longgar. Mengambil alih kunci terlalu cepat berarti dua backfill jalan
# bersamaan, dan itu lebih merugikan daripada menunggu beberapa menit setelah
# sebuah proses mati mendadak.
STALE_AFTER_SECONDS = 300

# Diambil alih HANYA kalau denyut terakhir sudah lewat ambang. Ditulis sebagai
# satu pernyataan supaya atomik: ON CONFLICT DO UPDATE memegang kunci baris
# selama WHERE dievaluasi, jadi dari dua pemanggil bersamaan tepat satu yang
# menerima baris balik.
_ACQUIRE = text("""
    INSERT INTO backfill_lock (id, owner, locked_at, heartbeat_at)
    VALUES (:id, :owner, now(), now())
    ON CONFLICT (id) DO UPDATE
       SET owner        = EXCLUDED.owner,
           locked_at    = EXCLUDED.locked_at,
           heartbeat_at = EXCLUDED.heartbeat_at
     WHERE backfill_lock.heartbeat_at < now() - make_interval(secs => :stale)
    RETURNING owner
""")

_HEARTBEAT = text("""
    UPDATE backfill_lock SET heartbeat_at = now()
    WHERE id = :id AND owner = :owner
    RETURNING heartbeat_at
""")

# Syarat `owner` inilah yang membuat run basi tidak bisa menghapus kunci milik
# penggantinya.
_RELEASE = text("DELETE FROM backfill_lock WHERE id = :id AND owner = :owner")

_HOLDER = text("""
    SELECT owner,
           locked_at,
           heartbeat_at,
           EXTRACT(EPOCH FROM now() - locked_at)::int    AS held_seconds,
           EXTRACT(EPOCH FROM now() - heartbeat_at)::int AS silent_seconds
    FROM backfill_lock WHERE id = :id
""")


def _owner_token() -> str:
    """
    Penanda pemegang kunci.

    UUID disertakan karena host dan PID saja tidak cukup: proses yang restart
    dapat memakai PID yang sama dan akan terlihat sebagai pemegang lama.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def acquire() -> str | None:
    """
    Mencoba mengambil kunci. Mengembalikan token pemilik, atau None kalau sedang
    dipegang proses lain yang masih berdenyut.

    Token yang dikembalikan WAJIB disimpan pemanggil dan diteruskan ke
    `heartbeat()` dan `release()`.
    """
    owner = _owner_token()
    db = SessionLocal()
    try:
        got = db.execute(_ACQUIRE, {"id": LOCK_ID, "owner": owner,
                                    "stale": STALE_AFTER_SECONDS}).scalar()
        db.commit()
        return owner if got else None
    finally:
        db.close()


def heartbeat(owner: str) -> bool:
    """
    Menyatakan kunci masih dipakai.

    False berarti kunci sudah BUKAN milik kita lagi — proses ini terlanjur
    dianggap basi dan diambil alih. Pemanggil harus berhenti saat itu juga;
    melanjutkan berarti dua backfill menulis findings yang sama.
    """
    db = SessionLocal()
    try:
        alive = db.execute(_HEARTBEAT, {"id": LOCK_ID, "owner": owner}).scalar()
        db.commit()
        return alive is not None
    finally:
        db.close()


def release(owner: str) -> None:
    """Melepas kunci. Aman dipanggil walau kunci sudah diambil alih."""
    db = SessionLocal()
    try:
        db.execute(_RELEASE, {"id": LOCK_ID, "owner": owner})
        db.commit()
    finally:
        db.close()


def holder() -> dict | None:
    """
    Siapa yang sedang memegang kunci, untuk isi pesan 409.

    Penolakan yang hanya berbunyi "sedang berjalan" tidak bisa dibedakan dari
    bug — persis situasi yang membuat versi advisory lock sulit didiagnosis.
    Dengan `held_seconds` dan `silent_seconds`, kunci yang menggantung langsung
    terlihat sebagai menggantung.
    """
    db = SessionLocal()
    try:
        row = db.execute(_HOLDER, {"id": LOCK_ID}).mappings().first()
        return dict(row) if row else None
    finally:
        db.close()
