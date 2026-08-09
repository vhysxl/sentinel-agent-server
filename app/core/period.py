"""
Pengurai periode.

Kosakata sengaja dibuat sempit, dan Python yang menghitung tanggalnya — bukan
LLM. Model hanya boleh memilih salah satu bentuk di bawah; ia tidak pernah
menyusun rentang tanggal sendiri.

    "2023"                    setahun penuh
    "2023-09"                 satu bulan
    "2023-Q3"                 satu kuartal
    "2023-09-01..2023-09-15"  rentang eksplisit

Alasannya sama dengan alasan angka tidak boleh dihitung LLM: begitu model
menyusun tanggal sendiri, tidak ada yang bisa memastikan "September" yang ia
maksud sama dengan September yang di-query. Bentuk yang tidak dikenali DITOLAK,
tidak ditebak — jawaban atas periode yang salah lebih berbahaya daripada
jawaban "saya tidak paham periodenya".

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

CONTOH = '"2023", "2023-09", "2023-Q3", atau "2023-09-01..2023-09-15"'


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

    @property
    def bounds(self) -> tuple[datetime, datetime]:
        return self.start, self.end

    def to_dict(self) -> dict:
        return {
            "periode": self.raw,
            "label": self.label,
            "mulai": self.start.strftime("%Y-%m-%d"),
            "sampai_sebelum": self.end.strftime("%Y-%m-%d"),
        }

    def months(self) -> int:
        return (self.end.year - self.start.year) * 12 + (self.end.month - self.start.month)


def _wib(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=WIB)


def _add_month(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def parse(period: str) -> Period:
    """
    Mengubah teks periode menjadi rentang WIB.

    Melempar PeriodError untuk bentuk yang tidak dikenali — termasuk yang
    "hampir benar" seperti "September 2023" atau "tahun lalu". Menebaknya berarti
    menjawab pertanyaan yang tidak diajukan.
    """
    if not period or not isinstance(period, str):
        raise PeriodError(f"Periode kosong. Gunakan {CONTOH}.")
    text = period.strip()

    if m := YEAR.match(text):
        y = int(m.group(1))
        return Period(_wib(date(y, 1, 1)), _wib(date(y + 1, 1, 1)),
                      f"tahun {y}", text)

    if m := MONTH.match(text):
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise PeriodError(f"Bulan {mo} tidak sah pada '{text}'.")
        start = date(y, mo, 1)
        return Period(_wib(start), _wib(_add_month(start, 1)),
                      f"{BULAN[mo]} {y}", text)

    if m := QUARTER.match(text):
        y, q = int(m.group(1)), int(m.group(2))
        start = date(y, (q - 1) * 3 + 1, 1)
        return Period(_wib(start), _wib(_add_month(start, 3)),
                      f"kuartal {q} tahun {y}", text)

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
