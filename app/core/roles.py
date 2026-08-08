"""
Peran pengguna.

Aplikasi ini dipakai satu tim keuangan dengan dua peran saja, sehingga tidak ada
konsep departemen. Peran diturunkan dari kolom `users.is_admin` yang sudah ada —
tidak disimpan sebagai kolom baru, karena tabel `users` dimiliki aplikasi Next.js
dan agent server tidak boleh mengubahnya.

Peran hanya dipakai untuk satu hal: menilai split payment. Staff yang memecah
pembayaran di bawah ambang persetujuan berarti menghindari kontrol; lead yang
melakukan hal sama tidak memperoleh apa-apa karena ia memang berwenang.
"""

FINANCE_LEAD = "finance_lead"
FINANCE_STAFF = "finance_staff"


def role_of(is_admin: bool | None) -> str:
    """Menerjemahkan users.is_admin menjadi nama peran."""
    return FINANCE_LEAD if is_admin else FINANCE_STAFF


def is_lead(is_admin: bool | None) -> bool:
    return role_of(is_admin) == FINANCE_LEAD
