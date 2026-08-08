"""
Statistik anomali nominal.

Memakai modified z-score berbasis median + MAD, bukan mean + stddev. Dua alasan,
dan yang kedua lebih penting daripada yang pertama:

1. Tidak ada langit-langit.
   Z-score biasa yang menyertakan transaksi ke dalam baseline-nya sendiri punya
   batas matematis z_max = (n-1)/sqrt(n). Untuk n=31 batas itu 5.39, sehingga
   fraud 45 juta dan fraud 450 juta menghasilkan angka yang persis sama. Vendor
   dengan <= 10 transaksi bahkan tidak pernah bisa melewati ambang z > 3.

2. Tahan terhadap fraud jamak (masking effect).
   Kalau di dalam riwayat vendor sudah ada 2-3 transaksi fraud, mean dan stddev
   ikut membengkak sehingga fraud menyembunyikan dirinya sendiri. Median tidak
   bergeser sampai lebih dari separuh data tercemar.

Baseline SELALU mengecualikan transaksi yang sedang dinilai.
"""
from dataclasses import dataclass, asdict
from statistics import median, fmean
from typing import Optional, Sequence

from app.core.config import (
    MAD_SCALE,
    MEANAD_SCALE,
    MIN_CATEGORY_BASELINE,
    MIN_VENDOR_BASELINE,
    MODIFIED_Z_THRESHOLD,
)

# Metode yang mungkin dipakai, dari yang paling dipercaya ke paling lemah.
METHOD_VENDOR_MAD = "vendor_mad"
METHOD_VENDOR_MEANAD = "vendor_meanad"
METHOD_CATEGORY_MAD = "category_mad"
METHOD_CATEGORY_MEANAD = "category_meanad"
METHOD_CONSTANT_BASELINE = "constant_baseline"
METHOD_INSUFFICIENT = "insufficient_baseline"

CONFIDENCE = {
    METHOD_VENDOR_MAD: "high",
    METHOD_VENDOR_MEANAD: "high",
    METHOD_CATEGORY_MAD: "medium",
    METHOD_CATEGORY_MEANAD: "medium",
    METHOD_CONSTANT_BASELINE: "low",
    METHOD_INSUFFICIENT: "none",
}


@dataclass
class BaselineResult:
    """
    Hasil penilaian sebuah nominal terhadap baseline-nya.

    Seluruh field disimpan ke findings.payload supaya skor dapat direproduksi
    setelah data bertambah (D16). Tanpa ini, angka pada temuan lama tidak bisa
    lagi dijelaskan begitu baseline berubah.
    """
    scope: str                      # vendor | category | none
    method: str
    n_baseline: int
    median: Optional[float]
    mad: Optional[float]
    threshold: float
    computed_value: Optional[float]  # modified z-score
    confidence: str
    is_anomaly: bool
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def modified_z_score(amount: float, baseline: Sequence[float]):
    """
    Menghitung modified z-score terhadap sebuah baseline.

        modified_z = 0.6745 * (amount - median) / MAD

    Konstanta 0.6745 mengembalikan skala MAD agar setara simpangan baku pada data
    normal (MAD ~ 0.6745 * sigma), sehingga ambang 3.5 tetap bermakna seperti
    ambang z-score yang sudah dikenal.

    Mengembalikan (nilai, metode, median, mad).

    Penanganan bertingkat saat MAD = 0 — terjadi ketika lebih dari separuh
    baseline bernilai persis sama, misalnya langganan tetap tiap bulan:
      MAD > 0     -> rumus di atas
      MAD = 0     -> pakai rata-rata simpangan absolut, dikoreksi 1.2533
                     (= sqrt(pi/2), faktor konsistensi terhadap sigma)
      keduanya 0  -> seluruh baseline identik; tidak ada angka yang bermakna
    """
    values = [float(v) for v in baseline]
    if not values:
        return None, METHOD_INSUFFICIENT, None, None

    med = median(values)
    deviations = [abs(v - med) for v in values]
    mad = median(deviations)

    if mad > 0:
        return MAD_SCALE * (amount - med) / mad, "mad", med, mad

    mean_ad = fmean(deviations)
    if mean_ad > 0:
        return (amount - med) / (MEANAD_SCALE * mean_ad), "meanad", med, 0.0

    return None, METHOD_CONSTANT_BASELINE, med, 0.0


def evaluate(amount: float,
             vendor_baseline: Sequence[float],
             category_baseline: Sequence[float]) -> BaselineResult:
    """
    Menilai sebuah nominal dengan baseline bertingkat.

        1. Riwayat vendor      (n >= MIN_VENDOR_BASELINE)     -> keyakinan tinggi
        2. Riwayat kategori    (n >= MIN_CATEGORY_BASELINE)   -> keyakinan sedang
        3. insufficient_baseline

    Jenjang kedua ada karena vendor fiktif — pola fraud paling klasik — justru
    selalu punya sedikit transaksi. Kalau sistem hanya menjawab "data tidak
    cukup", ia buta persis pada kasus yang paling ingin ditangkap.

    Jenjang ketiga WAJIB diterbitkan, tidak boleh didiamkan: bagi sistem audit,
    "sudah diperiksa dan bersih" dan "tidak pernah bisa diperiksa" adalah dua hal
    yang berbeda dan tidak boleh terlihat sama.
    """
    for values, scope, minimum in (
        (vendor_baseline, "vendor", MIN_VENDOR_BASELINE),
        (category_baseline, "category", MIN_CATEGORY_BASELINE),
    ):
        if len(values) < minimum:
            continue

        z, kind, med, mad = modified_z_score(amount, values)

        if kind == METHOD_CONSTANT_BASELINE:
            differs = med is not None and amount != med
            return BaselineResult(
                scope=scope, method=METHOD_CONSTANT_BASELINE, n_baseline=len(values),
                median=_round(med), mad=0.0, threshold=MODIFIED_Z_THRESHOLD,
                computed_value=None, confidence=CONFIDENCE[METHOD_CONSTANT_BASELINE],
                is_anomaly=differs,
                message=(
                    f"Seluruh {len(values)} transaksi pembanding bernilai sama "
                    f"({med:,.0f}), sehingga sebaran tidak dapat diukur. "
                    f"Nominal ini {'berbeda dari' if differs else 'sama dengan'} nilai tersebut."
                ),
            )

        method = f"{scope}_{kind}"
        is_anomaly = z is not None and z >= MODIFIED_Z_THRESHOLD
        return BaselineResult(
            scope=scope, method=method, n_baseline=len(values),
            median=_round(med), mad=_round(mad), threshold=MODIFIED_Z_THRESHOLD,
            computed_value=_round(z), confidence=CONFIDENCE.get(method, "medium"),
            is_anomaly=is_anomaly,
            message=(
                f"Nominal {amount:,.0f} berada pada modified z-score {z:.1f} "
                f"terhadap {len(values)} transaksi pembanding "
                f"(median {med:,.0f}, MAD {mad:,.0f}, ambang {MODIFIED_Z_THRESHOLD}). "
                f"Baseline: {scope}."
            ),
        )

    n = max(len(vendor_baseline), len(category_baseline))
    return BaselineResult(
        scope="none", method=METHOD_INSUFFICIENT, n_baseline=n,
        median=None, mad=None, threshold=MODIFIED_Z_THRESHOLD,
        computed_value=None, confidence=CONFIDENCE[METHOD_INSUFFICIENT],
        is_anomaly=False,
        message=(
            f"Baseline tidak mencukupi: vendor punya {len(vendor_baseline)} "
            f"transaksi pembanding (minimum {MIN_VENDOR_BASELINE}), kategori "
            f"{len(category_baseline)} (minimum {MIN_CATEGORY_BASELINE}). "
            f"Nominal tidak dinilai secara statistik. Ini BUKAN pernyataan bahwa "
            f"transaksi ini wajar."
        ),
    )
