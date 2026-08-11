"""
Pengurai periode.

Kosakata sengaja dibuat sempit, dan Python yang menghitung tanggalnya — bukan
LLM. Model hanya boleh memilih salah satu bentuk di bawah; ia tidak pernah
menyusun rentang tanggal sendiri.

    "2023"                    setahun penuh
    "2023-09"                 satu bulan
    "2023-Q3"                 satu kuartal
    "2023-09-01..2023-09-15"  rentang eksplisit
    "2023-03..2023-08"        rentang bulan
    "bulan-ini" "bulan-lalu"  relatif terhadap hari ini
    "kuartal-ini" "kuartal-lalu" "tahun-ini" "tahun-lalu"
    "6-bulan-terakhir" "30-hari-terakhir"

Alasannya sama dengan alasan angka tidak boleh dihitung LLM: begitu model
menyusun tanggal sendiri, tidak ada yang bisa memastikan "September" yang ia
maksud sama dengan September yang di-query. Bentuk yang tidak dikenali DITOLAK,
tidak ditebak — jawaban atas periode yang salah lebih berbahaya daripada
jawaban "saya tidak paham periodenya".

Kenapa bentuk relatif ada di sini, bukan di prompt
--------------------------------------------------
Sebelumnya kosakata ini hanya berisi bentuk absolut, sehingga "bulan ini"
terpaksa diterjemahkan MODEL menjadi "2026-08" — aritmetika tanggal yang justru
dilarang berkas ini di paragraf atas. Kebetulan sering benar, dan itu yang
membuatnya berbahaya: tidak ada yang memeriksa, dan salahnya tak bersuara.
Sekarang model cukup meneruskan kata relatifnya; Python yang memetakan ke
tanggal.

`raw` sebuah periode relatif diisi bentuk absolut hasil pemetaan ("2026-08"),
bukan kata aslinya, supaya jawaban dan daftar `figures` menyebut periode yang
benar-benar di-query — resolusinya jadi terlihat, bukan tersembunyi.

Periode yang BELUM SELESAI ditandai `partial`. Tanpa itu "laba bulan ini vs
bulan lalu" diam-diam membandingkan 11 hari melawan 31 hari dan jawabannya
terlihat meyakinkan justru saat paling menyesatkan.

Seluruh batas dinyatakan dalam WIB dan bersifat setengah terbuka: awal inklusif,
akhir eksklusif. Itu menghindari kelas bug "transaksi pukul 14:20 hilang karena
batas akhir jatuh di 00:00" yang sudah pernah terjadi di proyek ini.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core.config import WIB
from app.core.format import BULAN

YEAR = re.compile(r"^(\d{4})$")
MONTH = re.compile(r"^(\d{4})-(\d{2})$")
QUARTER = re.compile(r"^(\d{4})-[Qq]([1-4])$")
RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")
# Bentuk yang berulang kali dicoba model dan dulu selalu ditolak.
MONTH_RANGE = re.compile(r"^(\d{4})-(\d{2})\.\.(\d{4})-(\d{2})$")
LAST_N = re.compile(r"^(\d{1,3})-(hari|bulan)-terakhir$")

# Batas atas jendela bergulir. Menahan "9999-bulan-terakhir" menjadi pemindaian
# tabel penuh yang tidak pernah diminta siapa pun.
MAX_LAST_DAYS = 365
MAX_LAST_MONTHS = 36

CONTOH = ('"2023", "2023-09", "2023-Q3", "2023-09-01..2023-09-15", '
          '"bulan-ini", "bulan-lalu", atau "6-bulan-terakhir"')


class PeriodError(ValueError):
    """Bentuk periode tidak dikenali. Sengaja tidak ditebak."""


@dataclass(frozen=True)
class Period:
    """
    Rentang setengah terbuka [start, end) dalam WIB.

    `label` adalah bentuk yang dibaca manusia; `raw` adalah masukan aslinya,
    disimpan supaya jawaban bisa menyebut periode persis seperti yang diminta.
    """
    start: datetime
    end: datetime
    label: str
    raw: str
    # Periode yang batas akhirnya belum lewat: bulan/kuartal/tahun berjalan.
    # Dibawa sampai ke jawaban supaya perbandingan terhadap periode penuh tidak
    # disajikan seolah setara.
    partial: bool = False

    @property
    def bounds(self) -> tuple[datetime, datetime]:
        return self.start, self.end

    def to_dict(self) -> dict:
        d = {
            "periode": self.raw,
            "label": self.label,
            "mulai": self.start.strftime("%Y-%m-%d"),
            "sampai_sebelum": self.end.strftime("%Y-%m-%d"),
        }
        if self.partial:
            d["sebagian_berjalan"] = True
            d["catatan_periode"] = (
                "Periode ini BELUM SELESAI. Angkanya belum lengkap, jadi jangan "
                "dibandingkan dengan periode penuh tanpa menyebut hal ini."
            )
        return d

    def months(self) -> int:
        return (self.end.year - self.start.year) * 12 + (self.end.month - self.start.month)


def _wib(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=WIB)


def _add_month(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _month_period(start: date, months: int, today: date, raw: str,
                  suffix: str = "") -> Period:
    """Periode sepanjang `months` bulan sejak `start`, ditandai kalau belum usai."""
    end = _add_month(start, months)
    label = (f"{BULAN[start.month]} {start.year}" if months == 1
             else f"{start.strftime('%m-%Y')} s/d {_add_month(end, -1).strftime('%m-%Y')}")
    return Period(_wib(start), _wib(end), label + suffix, raw, partial=today < end)


def _relative(token: str, today: date) -> Period | None:
    """
    Memetakan kata relatif ke rentang absolut. None kalau bukan bentuk relatif.

    Di sinilah aritmetika tanggal berada — Python, dapat diuji, sama hasilnya
    tiap kali. Sebelumnya pekerjaan ini jatuh ke model.
    """
    bulan_ini = date(today.year, today.month, 1)
    kuartal_ini = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    tahun_ini = date(today.year, 1, 1)

    if token == "bulan-ini":
        return _month_period(bulan_ini, 1, today, bulan_ini.strftime("%Y-%m"),
                             " (bulan berjalan)")
    if token == "bulan-lalu":
        lalu = _add_month(bulan_ini, -1)
        return _month_period(lalu, 1, today, lalu.strftime("%Y-%m"))
    if token == "kuartal-ini":
        q = (kuartal_ini.month - 1) // 3 + 1
        return _month_period(kuartal_ini, 3, today,
                             f"{kuartal_ini.year}-Q{q}", " (kuartal berjalan)")
    if token == "kuartal-lalu":
        lalu = _add_month(kuartal_ini, -3)
        q = (lalu.month - 1) // 3 + 1
        return _month_period(lalu, 3, today, f"{lalu.year}-Q{q}")
    if token == "tahun-ini":
        return Period(_wib(tahun_ini), _wib(date(today.year + 1, 1, 1)),
                      f"tahun {today.year} (tahun berjalan)",
                      str(today.year), partial=True)
    if token == "tahun-lalu":
        y = today.year - 1
        return Period(_wib(date(y, 1, 1)), _wib(date(y + 1, 1, 1)),
                      f"tahun {y}", str(y))

    if m := LAST_N.match(token):
        n, unit = int(m.group(1)), m.group(2)
        if n < 1:
            raise PeriodError(f"Jumlah pada '{token}' harus minimal 1.")
        if unit == "hari":
            if n > MAX_LAST_DAYS:
                raise PeriodError(
                    f"'{token}' melebihi batas {MAX_LAST_DAYS} hari.")
            # Akhir eksklusif di besok, supaya hari ini ikut terhitung penuh.
            end = today + timedelta(days=1)
            start = end - timedelta(days=n)
            return Period(_wib(start), _wib(end),
                          f"{n} hari terakhir (s/d {today.strftime('%d-%m-%Y')})",
                          f"{start.isoformat()}..{today.isoformat()}")
        if n > MAX_LAST_MONTHS:
            raise PeriodError(f"'{token}' melebihi batas {MAX_LAST_MONTHS} bulan.")
        # Bulan berjalan ikut dihitung sebagai salah satu dari n bulan itu.
        start = _add_month(bulan_ini, -(n - 1))
        return Period(_wib(start), _wib(today + timedelta(days=1)),
                      f"{n} bulan terakhir (s/d {today.strftime('%d-%m-%Y')})",
                      f"{start.isoformat()}..{today.isoformat()}", partial=True)

    return None


def parse(period: str, today: date | None = None) -> Period:
    """
    Mengubah teks periode menjadi rentang WIB.

    Melempar PeriodError untuk bentuk yang tidak dikenali — termasuk yang
    "hampir benar" seperti "September 2023" atau "tahun kemarin". Menebaknya
    berarti menjawab pertanyaan yang tidak diajukan.

    `today` dapat disuntik supaya bentuk relatif bisa diuji tanpa bergantung pada
    tanggal saat pengujian dijalankan.
    """
    if not period or not isinstance(period, str):
        raise PeriodError(f"Periode kosong. Gunakan {CONTOH}.")
    text = period.strip()

    if today is None:
        today = datetime.now(WIB).date()

    if relative := _relative(text.lower(), today):
        return relative

    # `partial` dihitung untuk bentuk absolut juga. Menulis "2026-08" itu sendiri
    # tidak membuat Agustus selesai — kalau hanya bentuk relatif yang ditandai,
    # peringatannya hilang persis ketika model memakai bentuk absolut.
    if m := YEAR.match(text):
        y = int(m.group(1))
        end = date(y + 1, 1, 1)
        return Period(_wib(date(y, 1, 1)), _wib(end), f"tahun {y}", text,
                      partial=today < end)

    if m := MONTH.match(text):
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise PeriodError(f"Bulan {mo} tidak sah pada '{text}'.")
        start = date(y, mo, 1)
        end = _add_month(start, 1)
        return Period(_wib(start), _wib(end), f"{BULAN[mo]} {y}", text,
                      partial=today < end)

    if m := QUARTER.match(text):
        y, q = int(m.group(1)), int(m.group(2))
        start = date(y, (q - 1) * 3 + 1, 1)
        end = _add_month(start, 3)
        return Period(_wib(start), _wib(end), f"kuartal {q} tahun {y}", text,
                      partial=today < end)

    if m := MONTH_RANGE.match(text):
        ya, ma, yb, mb = (int(g) for g in m.groups())
        if not (1 <= ma <= 12 and 1 <= mb <= 12):
            raise PeriodError(f"Bulan tidak sah pada '{text}'.")
        a, b = date(ya, ma, 1), date(yb, mb, 1)
        if b < a:
            raise PeriodError(f"Bulan akhir mendahului awal pada '{text}'.")
        # Akhir dibuat eksklusif di awal bulan berikutnya, supaya bulan terakhir
        # ikut termuat seluruhnya.
        return Period(_wib(a), _wib(_add_month(b, 1)),
                      f"{a.strftime('%m-%Y')} s/d {b.strftime('%m-%Y')}", text,
                      partial=today < _add_month(b, 1))

    if m := RANGE.match(text):
        try:
            a = date.fromisoformat(m.group(1))
            b = date.fromisoformat(m.group(2))
        except ValueError as exc:
            raise PeriodError(f"Tanggal tidak sah pada '{text}': {exc}") from exc
        if b < a:
            raise PeriodError(f"Tanggal akhir mendahului awal pada '{text}'.")
        # Akhir dibuat eksklusif dengan menambah satu hari, supaya rentang
        # "01..15" benar-benar memuat seluruh hari ke-15.
        return Period(_wib(a), _wib(b + timedelta(days=1)),
                      f"{a.strftime('%d-%m-%Y')} s/d {b.strftime('%d-%m-%Y')}", text)

    raise PeriodError(
        f"Periode '{text}' tidak dikenali. Gunakan {CONTOH}."
    )
