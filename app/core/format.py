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

HARI = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
        4: "Friday", 5: "Saturday", 6: "Sunday"}

BULAN = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May",
         6: "June", 7: "July", 8: "August", 9: "September", 10: "October",
         11: "November", 12: "December"}


def rupiah(value: float) -> str:
    """1234567.89 -> 'Rp1,234,568'. Cents dropped; nobody reads them."""
    return "Rp" + f"{round(float(value)):,}"


def ringkas(value: float) -> str:
    """
    Amount in units that are immediately concrete: 'Rp45 million', 'Rp1.2 billion'.

    Used when what matters is SCALE, not the exact figure. For an exact figure
    use rupiah() instead.
    """
    v = float(value)
    if v >= 1_000_000_000:
        return f"Rp{v / 1_000_000_000:.1f}".rstrip("0").rstrip(".") + " billion"
    if v >= 1_000_000:
        return f"Rp{v / 1_000_000:.1f}".rstrip("0").rstrip(".") + " million"
    if v >= 1_000:
        return f"Rp{v / 1_000:.0f} thousand"
    return rupiah(v)


def tanggal(dt: datetime) -> str:
    """'Monday, 20 July 2026 at 23:15'"""
    return (f"{HARI[dt.weekday()]}, {dt.day} {BULAN[dt.month]} {dt.year} "
            f"at {dt:%H:%M}")


def tanggal_pendek(dt: datetime) -> str:
    """'20 July, 23:15'"""
    return f"{dt.day} {BULAN[dt.month]}, {dt:%H:%M}"


def kelipatan(amount: float, baseline: float) -> str:
    """
    'about 26 times' — how people compare magnitudes.

    Used in the narrative instead of z-score, because it's immediately
    concrete. The z-score itself is still kept in `detail` for tracing.
    """
    if not baseline:
        return ""
    ratio = float(amount) / float(baseline)
    if ratio >= 10:
        return f"about {ratio:.0f}x"
    if ratio >= 2:
        return f"about {ratio:.1f}x"
    if ratio > 1:
        return f"{(ratio - 1) * 100:.0f}% higher"
    return f"{(1 - ratio) * 100:.0f}% lower"
