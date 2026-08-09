"""
Kode status dan labelnya.

Dipisah karena keduanya berbeda sifat. Kode adalah DATA — ia masuk database,
difilter, dan diperiksa constraint. Label adalah TAMPILAN — ia berubah kalau
bahasanya berubah, dan tidak boleh ikut tersimpan.

Sebelumnya `findings.risk_level` menyimpan "Critical Risk": teks antarmuka
berbahasa Inggris, tanpa constraint apa pun. Dua akibatnya: satu salah ketik
membuat baris itu diam-diam tidak pernah terhitung di filter mana pun, dan
UI berbahasa Indonesia tetap harus memetakannya ulang sebelum tampil.

Berkas ini menjadi satu-satunya sumber, supaya SQL dan Python tidak bisa
melenceng satu sama lain.
"""

# --- Tingkat risiko ---------------------------------------------------------
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"

RISK_LEVELS = (RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL)

# Urutan keparahan, dipakai untuk mengurutkan dan membandingkan.
RISK_ORDER = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2, RISK_CRITICAL: 3}

RISK_LABEL = {
    RISK_LOW: "Rendah",
    RISK_MEDIUM: "Sedang",
    RISK_HIGH: "Tinggi",
    RISK_CRITICAL: "Kritis",
}

# Tindakan yang disarankan per tingkat. Ini copy, bukan logika — sebelumnya
# kalimat-kalimat ini tertanam di tengah RISK_BANDS pada scoring engine.
RISK_ACTION = {
    RISK_LOW: "Transaksi wajar. Tidak perlu tindakan.",
    RISK_MEDIUM: "Anomali ringan. Cukup dicatat di laporan audit bulanan.",
    RISK_HIGH: "Indikasi kecurangan. Perlu diverifikasi manual minggu ini.",
    RISK_CRITICAL: "Indikasi fraud serius. Eskalasi ke Manajer/CFO sekarang.",
}


# --- Penyelesaian temuan ----------------------------------------------------
# Bukan boolean: KENAPA sebuah temuan ditutup lebih penting daripada bahwa ia
# ditutup. Kalau lead menutup sepuluh temuan z-score berturut-turut sebagai
# `justified`, itu sinyal ambangnya kekencangan — boolean tidak memberi itu.
RESOLUTION_JUSTIFIED = "justified"
RESOLUTION_FALSE_POSITIVE = "false_positive"
RESOLUTION_CONFIRMED_FRAUD = "confirmed_fraud"
RESOLUTION_ESCALATED = "escalated"

RESOLUTIONS = (
    RESOLUTION_JUSTIFIED,
    RESOLUTION_FALSE_POSITIVE,
    RESOLUTION_CONFIRMED_FRAUD,
    RESOLUTION_ESCALATED,
)

RESOLUTION_LABEL = {
    RESOLUTION_JUSTIFIED: "Ada penjelasan yang sah",
    RESOLUTION_FALSE_POSITIVE: "Deteksi keliru",
    RESOLUTION_CONFIRMED_FRAUD: "Terbukti, sudah ditindak",
    RESOLUTION_ESCALATED: "Dilaporkan ke atasan",
}


# --- Status pemeriksaan per transaksi ---------------------------------------
# Inilah yang membedakan "sudah diperiksa dan bersih" dari "belum pernah
# diperiksa". Tanpa pembedaan itu, angka "64 aman" di halaman tidak punya arti.
STATUS_CLEAN = "clean"
STATUS_FLAGGED = "flagged"
STATUS_FAILED = "failed"

ANALYSIS_STATUSES = (STATUS_CLEAN, STATUS_FLAGGED, STATUS_FAILED)
