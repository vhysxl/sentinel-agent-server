"""
Pemformatan untuk narasi yang dibaca manusia.

Dipisahkan karena ada dua pembaca dengan kebutuhan berbeda:

    narasi  -> orang keuangan. "biasanya sekitar 20 juta, ini 45 juta"
    detail  -> auditor yang menelusuri. method, median, MAD, ambang, z

Angka teknis tidak dibuang, hanya dipindah ke tempat yang tepat. Menyebut
"modified z-score 158.44" di kalimat utama tidak membantu siapa pun yang harus
memutuskan apakah pembayaran ini perlu ditahan.
"""
from datetime import datetime

HARI = {0: "Senin", 1: "Selasa", 2: "Rabu", 3: "Kamis",
        4: "Jumat", 5: "Sabtu", 6: "Minggu"}

BULAN = {1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei",
         6: "Juni", 7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober",
         11: "November", 12: "Desember"}


def rupiah(value: float) -> str:
    """1234567.89 -> 'Rp1.234.568'. Sen dibuang; tidak ada yang membacanya."""
    return "Rp" + f"{round(float(value)):,}".replace(",", ".")


def ringkas(value: float) -> str:
    """
    Nominal dalam satuan yang langsung terbayang: 'Rp45 juta', 'Rp1,2 miliar'.

    Dipakai saat yang penting adalah SKALA, bukan angka persisnya. Untuk angka
    persis tetap pakai rupiah().
    """
    v = float(value)
    if v >= 1_000_000_000:
        return f"Rp{v / 1_000_000_000:.1f}".replace(".", ",").rstrip("0").rstrip(",") + " miliar"
    if v >= 1_000_000:
        return f"Rp{v / 1_000_000:.1f}".replace(".", ",").rstrip("0").rstrip(",") + " juta"
    if v >= 1_000:
        return f"Rp{v / 1_000:.0f} ribu"
    return rupiah(v)


def tanggal(dt: datetime) -> str:
    """'Senin, 20 Juli 2026 pukul 23:15'"""
    return (f"{HARI[dt.weekday()]}, {dt.day} {BULAN[dt.month]} {dt.year} "
            f"pukul {dt:%H:%M}")


def tanggal_pendek(dt: datetime) -> str:
    """'20 Juli, 23:15'"""
    return f"{dt.day} {BULAN[dt.month]}, {dt:%H:%M}"


def kelipatan(amount: float, baseline: float) -> str:
    """
    'sekitar 26 kali lipat' — cara orang membandingkan besaran.

    Kelipatan dipakai di narasi, bukan z-score, karena langsung terbayang.
    z-score tetap disimpan di detail untuk penelusuran.
    """
    if not baseline:
        return ""
    ratio = float(amount) / float(baseline)
    if ratio >= 10:
        return f"sekitar {ratio:.0f} kali lipat"
    if ratio >= 2:
        return f"sekitar {ratio:.1f} kali lipat".replace(".", ",")
    if ratio > 1:
        return f"{(ratio - 1) * 100:.0f}% lebih tinggi"
    return f"{(1 - ratio) * 100:.0f}% lebih rendah"
