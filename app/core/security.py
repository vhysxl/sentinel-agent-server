"""
Handshake antar-service.

Agent server tidak punya konsep pengguna dan tidak akan pernah punya: otorisasi
sudah selesai di Express sebelum permintaan sampai ke sini. Yang dijaga berkas
ini hanya satu hal — memastikan pemanggilnya memang Express, bukan orang luar
yang kebetulan menemukan URL-nya.

Kenapa kunci bersama dan bukan daftar IP: Express berjalan di Vercel dengan IP
egress yang berubah-ubah, jadi daftar IP tidak mungkin dipelihara.

Kenapa ini bukan pengganti isolasi jaringan: kunci adalah lapisan TERAKHIR.
Agent server tetap sebaiknya tidak dapat dijangkau publik.
"""
import os
import secrets

from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import JSONResponse

load_dotenv()

HEADER_NAME = "X-Internal-Key"

INTERNAL_API_KEY = (os.getenv("INTERNAL_API_KEY") or "").strip()

# Kunci pendek nyaris tidak lebih baik daripada tidak ada. Ini peringatan, bukan
# penolakan, supaya tidak ada yang terkunci di luar gara-gara selisih satu huruf.
if INTERNAL_API_KEY and len(INTERNAL_API_KEY) < 32:
    print(
        f"[SECURITY] PERINGATAN: INTERNAL_API_KEY hanya {len(INTERNAL_API_KEY)} karakter. "
        "Hasilkan yang layak dengan: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# Hanya health check yang terbuka. Platform deploy memanggilnya tanpa kredensial
# apa pun, dan isinya tidak mengungkap apa-apa.
#
# /docs dan /openapi.json SENGAJA ikut terkunci — keduanya memetakan seluruh
# permukaan API. Konsekuensinya /docs tidak bisa dibuka lewat browser biasa;
# untuk eksplorasi lokal pakai koleksi di postman/ yang bisa mengirim header.
OPEN_PATHS = frozenset({"/"})


def _rejected(reason: str) -> JSONResponse:
    # Alasan lengkap hanya masuk log. Yang di luar cukup tahu bahwa ia ditolak;
    # membedakan "kunci salah" dari "kunci belum dipasang" memberi penyerang
    # informasi gratis.
    print(f"[SECURITY] ditolak: {reason}")
    return JSONResponse(status_code=401, content={"error": "Unauthorised."})


async def require_internal_key(request: Request, call_next):
    if request.url.path in OPEN_PATHS:
        return await call_next(request)

    if not INTERNAL_API_KEY:
        # GAGAL TERTUTUP, dan ini disengaja.
        #
        # Kalau variabelnya lupa dipasang saat deploy, layanan ini harus mati
        # total. Alternatifnya — membiarkan lewat saat kunci kosong — akan
        # mengembalikan keadaan lama persis: terbuka lebar, tanpa satu pun
        # gejala, sementara semua orang mengira sudah terlindungi.
        return _rejected("INTERNAL_API_KEY belum dipasang di environment agent server")

    presented = request.headers.get(HEADER_NAME, "")

    # compare_digest, bukan ==, supaya lamanya pembandingan tidak membocorkan
    # berapa karakter awal yang sudah tertebak benar. Dibandingkan sebagai bytes
    # karena versi str-nya menolak karakter non-ASCII.
    if not secrets.compare_digest(presented.encode("utf-8"), INTERNAL_API_KEY.encode("utf-8")):
        detail = "tidak dikirim" if not presented else "tidak cocok"
        return _rejected(f"{HEADER_NAME} {detail} pada {request.method} {request.url.path}")

    return await call_next(request)
