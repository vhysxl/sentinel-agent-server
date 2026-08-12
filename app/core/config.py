"""
Konstanta domain untuk seluruh agent server.

Semua angka yang menentukan perilaku deteksi dikumpulkan di sini supaya dapat
diubah tanpa menyentuh logika, dan supaya sebuah temuan bisa menyebutkan ambang
mana yang dipakainya. Lihat PLAN.md untuk alasan tiap angka.
"""
from zoneinfo import ZoneInfo

# --- Zona waktu -------------------------------------------------------------
# created_at bertipe timestamptz dan merupakan SATU-SATUNYA waktu di sistem ini
# (transaksi masuk dari API bank). Server database ber-timezone GMT, jadi setiap
# pembacaan jam WAJIB dikonversi ke WIB dulu; jangan pernah mengandalkan
# timezone sesi. Lihat PLAN.md §6.4.
WIB = ZoneInfo("Asia/Jakarta")

# --- Jam kerja --------------------------------------------------------------
# Jam kerja = Senin-Jumat, WORK_START_HOUR <= jam < WORK_END_HOUR (WIB).
# Di luar itu dianggap outside_hours. Aturan biner, bukan bertingkat.
WORK_START_HOUR = 8
WORK_END_HOUR = 18

# --- Ambang structuring (split payment) --------------------------------------
# APPROVAL_THRESHOLD BUKAN mekanisme approval yang benar-benar berjalan di
# app ini -- aplikasi ini sengaja tidak punya persetujuan berjenjang (lihat
# transactions_vendor_overview.md). Angka ini murni acuan "garis bulat" yang
# lazim dipakai pelaku structuring untuk membuat satu pengeluaran besar
# terlihat sebagai beberapa pengeluaran kecil.
# Dikenali dari nominal yang menempel tepat di bawah ambang ini: hanya
# transaksi di pita [SPLIT_BAND_FACTOR * T, T) yang dihitung. Tanpa pita
# tersebut, belanja rutin bernilai kecil yang totalnya besar ikut tertangkap.
APPROVAL_THRESHOLD = 25_000_000
SPLIT_WINDOW_DAYS = 7
SPLIT_BAND_FACTOR = 0.8
SPLIT_MIN_COUNT = 2

# --- Pola smurfing (nominal identik berulang) --------------------------------
# Beda dengan split_payment: di sini nominalnya TIDAK harus dekat garis bulat
# manapun. Sinyalnya murni FREKUENSI nominal identik yang tidak lazim ke
# vendor yang sama. SMURF_MIN_AMOUNT adalah floor, bukan pita -- pecahan di
# bawah ini (uang parkir, konsumsi, dll) terlalu umum berulang secara wajar
# untuk jadi sinyal. Pelaku yang benar-benar memecah ke pecahan sekecil itu
# sudah gampang dicurigai lewat cara lain di luar algoritma ini (frekuensi
# transaksi harian yang mencolok), jadi detector ini fokus ke pecahan yang
# masih "masuk akal" dipakai untuk menyamarkan satu pengeluaran besar.
SMURF_MIN_AMOUNT = 2_000_000
SMURF_WINDOW_DAYS = 7
SMURF_MIN_COUNT = 3

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
# Status vendor di app HANYA 'active' / 'inactive' (lihat
# sentinel-backend/src/validations/vendor.validation.js dan
# transactions_vendor_overview.md — "tidak ada status lain"). Vendor yang
# dinonaktifkan tapi masih dibayar adalah red flag nyata: entah lupa
# dinonaktifkan, entah sengaja tetap dipakai walau semestinya sudah berhenti.
RISKY_VENDOR_STATUSES = ("inactive",)
