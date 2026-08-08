"""
Konstanta domain untuk seluruh agent server.

Semua angka yang menentukan perilaku deteksi dikumpulkan di sini supaya dapat
diubah tanpa menyentuh logika, dan supaya sebuah temuan bisa menyebutkan ambang
mana yang dipakainya. Lihat PLAN.md untuk alasan tiap angka.
"""
from zoneinfo import ZoneInfo

# --- Zona waktu -------------------------------------------------------------
# transaction_date bertipe timestamptz. Server database ber-timezone GMT, jadi
# setiap pembacaan jam WAJIB dikonversi ke WIB dulu; jangan pernah mengandalkan
# timezone sesi. Lihat PLAN.md §6.4.
WIB = ZoneInfo("Asia/Jakarta")

# --- Jam kerja --------------------------------------------------------------
# Jam kerja = Senin-Jumat, WORK_START_HOUR <= jam < WORK_END_HOUR (WIB).
# Di luar itu dianggap outside_hours. Aturan biner, bukan bertingkat.
WORK_START_HOUR = 8
WORK_END_HOUR = 18

# --- Ambang persetujuan (split payment) -------------------------------------
# APPROVAL_THRESHOLD adalah nominal yang wajib disetujui finance lead.
# Structuring dikenali dari nominal yang menempel tepat di bawah ambang ini:
# hanya transaksi di pita [SPLIT_BAND_FACTOR * T, T) yang dihitung. Tanpa pita
# tersebut, belanja rutin bernilai kecil yang totalnya besar ikut tertangkap.
APPROVAL_THRESHOLD = 25_000_000
SPLIT_WINDOW_DAYS = 7
SPLIT_BAND_FACTOR = 0.8
SPLIT_MIN_COUNT = 2

# --- Baseline statistik -----------------------------------------------------
# Baseline vendor dipakai lebih dulu; kalau sampelnya kurang, turun ke baseline
# kategori; kalau itu pun kurang, terbitkan insufficient_baseline (jangan diam).
MIN_VENDOR_BASELINE = 10
MIN_CATEGORY_BASELINE = 20

# Ambang modified z-score (Iglewicz & Hoaglin).
MODIFIED_Z_THRESHOLD = 3.5

# Konstanta konsistensi terhadap simpangan baku pada data normal:
#   MAD    -> sigma  dibagi 0.6745
#   MeanAD -> sigma  dikali  1.2533  (= sqrt(pi/2)), dipakai saat MAD = 0
MAD_SCALE = 0.6745
MEANAD_SCALE = 1.2533

# --- Vendor -----------------------------------------------------------------
NEW_VENDOR_MAX_TRANSACTIONS = 3
RISKY_VENDOR_STATUSES = ("flagged", "suspended", "high_risk")
