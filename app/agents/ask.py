"""
Ask Sentinel — tanya jawab keuangan, dua langkah.

    1. RENCANA   model memilih tool + periode. Tidak menyentuh angka.
    2. Python    menjalankan tool. Angkanya milik Python.
    3. NARASI    model menulis jawaban dari angka yang sudah jadi. Tanpa tool.

Versi pertama memakai automatic function calling dan membiarkan model melaporkan
angka beserta sumbernya sendiri. Hasilnya langsung salah: untuk Juni 2026 ia
menjawab pendapatan Rp1.450.000.000 dan biaya Rp980.000.000, padahal data
sebenarnya Rp1.170.080.000 dan Rp256.204.917 — lalu mencantumkan keduanya di
`figures` dengan `source: get_period_summary`, sumber yang tidak pernah
mengembalikan angka itu.

Itu pola yang sudah dua kali terjadi di proyek ini: Agent 1 dan Agent 3 juga
pernah melaporkan "revenue turun 12%" untuk bulan yang naik 43,39% sambil
mencantumkan tool yang benar. Pemeriksaan pasca-jawaban tidak bisa menangkapnya,
karena angka karangan itu terdaftar rapi bersama sumber karangannya.

Maka `figures` sekarang dirakit PYTHON dari hasil tool, bukan oleh model.
Fabrikasi jadi mustahil secara struktur, bukan sekadar dilarang lewat prompt.
"""
import json
import re

from app.agents import persona
from app.agents.llm import run_agent
from app.core.format import rupiah
from app.tools import analytics

# Whitelist. Nama di luar daftar ini ditolak, tidak dicoba.
TOOLS = {
    "get_period_summary": (analytics.get_period_summary, ("period",)),
    "get_monthly_breakdown": (analytics.get_monthly_breakdown, ("period",)),
    "get_category_breakdown": (analytics.get_category_breakdown, ("period", "jenis")),
    "get_top_vendors": (analytics.get_top_vendors, ("period",)),
    "get_top_transactions": (analytics.get_top_transactions, ("period",)),
    "compare_periods": (analytics.compare_periods, ("period_a", "period_b")),
    "get_findings_summary": (analytics.get_findings_summary, ("period",)),
    "list_vendors": (analytics.list_vendors, ("status", "limit")),
    "get_vendor_detail": (analytics.get_vendor_detail, ("vendor", "period")),
    "search_transactions": (analytics.search_transactions, (
        "period", "vendor", "category", "description", "invoice_no",
        "min_amount", "max_amount", "jenis", "urutkan", "limit")),
    "search_findings": (analytics.search_findings, (
        "period", "risk_level", "status", "resolution", "vendor",
        "urutkan", "limit")),
    "get_finding_detail": (analytics.get_finding_detail,
                           ("finding_id", "transaction_id")),
}

MAX_STEPS = 3

PLAN_PROMPT = """
Kamu merencanakan cara menjawab pertanyaan keuangan. Kamu TIDAK menjawabnya.

PERTANYAAN: {question}
Hari ini: {today}. Data tersedia: {data_range}.

Pilih maksimal {max_steps} pemanggilan tool.

TOOL YANG ADA:
  get_period_summary(period)            total pendapatan, biaya, laba, margin
  get_monthly_breakdown(period)         rincian per bulan — untuk pertanyaan tren
  get_category_breakdown(period, jenis) per kategori; jenis: "expense"/"income"
  get_top_vendors(period)               vendor dengan belanja terbesar
  get_top_transactions(period)          contoh transaksi terbesar
  compare_periods(period_a, period_b)   membandingkan dua periode
  get_findings_summary(period)          BERAPA BANYAK temuan audit per tingkat
  search_findings(...)                  DAFTAR temuannya. Semua opsional: period,
                                        risk_level ("low"/"medium"/"high"/
                                        "critical"), status ("open"/"resolved"/
                                        "all", default "open"), resolution,
                                        vendor, urutkan ("terparah"/"teringan"/
                                        "terbaru"/"terlama"), limit
  get_finding_detail(finding_id |       RINCIAN satu temuan: pemicu beserta
                     transaction_id)    poinnya, susunan skor, kesimpulan agen,
                                        status penyelesaian
  list_vendors(status)                  DAFTAR vendor terdaftar: status, tanggal
                                        transaksi terakhir, lama tidak aktif, dan
                                        jumlah temuan. Untuk "vendor apa saja yang
                                        kita punya" DAN untuk "mana yang tidak
                                        aktif / mencurigakan" — BUKAN
                                        get_top_vendors, yang mengurutkan
                                        berdasarkan belanja dan membuang vendor
                                        yang belum pernah bertransaksi
  get_vendor_detail(vendor, period)     profil SATU vendor: status, total belanja,
                                        temuan, transaksi terakhirnya.
                                        `vendor` = potongan nama, `period` opsional
  search_transactions(...)              mencari transaksi tertentu. Semua opsional:
                                        period, vendor, category, description
                                        (kata dalam deskripsi), invoice_no (nomor
                                        faktur, cocok persis), min_amount,
                                        max_amount, jenis ("expense"/"income"),
                                        urutkan ("terbaru"/"terlama"/"terbesar"/
                                        "terkecil"), limit

MEMILIH ANTAR TOOL YANG MIRIP:
- Pertanyaan menyebut NAMA VENDOR dan minta gambaran menyeluruh -> get_vendor_detail
- Minta daftar transaksi tertentu (tanggal tertentu, vendor tertentu, "yang
  terbaru", "di atas 50 juta") -> search_transactions
- Minta TOTAL atau ringkasan -> get_period_summary / get_category_breakdown.
  Jangan memakai search_transactions lalu menjumlahkan sendiri.
- Tentang TEMUAN AUDIT: "berapa banyak" -> get_findings_summary;
  "temuan apa saja / mana yang belum ditangani" -> search_findings;
  "kenapa transaksi itu ditandai" -> get_finding_detail.
- "paling parah", "terbesar", "terbaru" itu URUTAN, bukan penyaring. Pakai
  `urutkan`. Isi `risk_level` HANYA kalau pertanyaannya menyebut tingkat
  tertentu ("temuan kritis"). Menyaring yang seharusnya diurutkan membuang
  jawaban yang benar — "temuan paling parah" dengan risk_level="critical"
  mengembalikan kosong padahal ada temuan tingkat Sedang.
- Isi argumen angka sebagai ANGKA, bukan teks: 1887, bukan "1887".

FORMAT PERIODE — hanya ini, jangan mengarang bentuk lain:
  absolut : "2023"  "2023-09"  "2023-Q3"  "2023-03..2023-08"
            "2023-09-01..2023-09-15"
  relatif : "bulan-ini" "bulan-lalu" "kuartal-ini" "kuartal-lalu"
            "tahun-ini" "tahun-lalu" "6-bulan-terakhir" "30-hari-terakhir"

Untuk "bulan ini", "bulan kemarin", "tahun lalu" dan sejenisnya, PAKAI BENTUK
RELATIF di atas. Jangan menghitung sendiri tanggalnya menjadi bentuk absolut —
Python yang memetakannya, dan hasilnya lebih bisa dipercaya daripada hitunganmu.

Balas HANYA JSON, tanpa kalimat pembuka:
{{"steps": [{{"tool": "get_period_summary", "args": {{"period": "2026-06"}}}}]}}

Contoh lain:
{{"steps": [{{"tool": "search_transactions",
             "args": {{"vendor": "Sinar Abadi", "urutkan": "terbaru"}}}}]}}

Pertanyaan boleh berbahasa Indonesia ATAU Inggris. Perlakukan sama; pemilihan
tool tidak bergantung pada bahasa.

TIGA KEMUNGKINAN, PILIH SATU:

1. BUTUH DATA — pertanyaannya tentang keuangan, transaksi, vendor, atau temuan
   audit perusahaan ini. Pilih tool.

   Termasuk pertanyaan yang meminta PENILAIAN atas data: "vendor mana yang
   mencurigakan", "mana yang sudah tidak aktif", "apa yang perlu saya periksa
   minggu ini". Ambil datanya lewat tool; Sentinel yang menilai setelahnya.
   Jangan menolak hanya karena tidak ada tool bernama "cari yang mencurigakan" —
   tool yang benar adalah yang membawa bahan penilaiannya.

2. TIDAK BUTUH DATA — tentang Sentinel sendiri ("siapa kamu", "apa yang bisa
   kamu lakukan"), sapaan, atau pertanyaan umum seputar keuangan, audit, dan
   statistik yang tidak menyangkut angka perusahaan ini ("apa itu z-score",
   "kenapa pembayaran dipecah itu masalah").
   -> steps kosong, alasan "tidak butuh data".

3. DI LUAR CAKUPAN — pemrograman, biologi, resep, puisi, berita, dan apa pun di
   luar keuangan/audit/statistik.
   -> steps kosong, alasan "di luar cakupan".

Untuk pilihan 2 dan 3 balas:
{{"steps": [], "alasan": "tidak butuh data | di luar cakupan — penjelasan singkat"}}

Jangan memanggil tool hanya untuk terlihat berusaha. Tool yang dipanggil pada
pertanyaan yang tidak menyangkut data perusahaan akan mengembalikan angka yang
tidak ada hubungannya dengan pertanyaan, dan itu lebih menyesatkan daripada
menjawab tanpa data.
"""

# Persona disisipkan sebagai ARGUMEN .format(), bukan dirangkai lebih dulu
# dengan f-string. Bedanya penting: teks yang dirangkai duluan ikut terbaca
# .format() sebagai template, sehingga satu kurung kurawal yang kelak ditambahkan
# di persona akan menjatuhkan seluruh prompt dengan KeyError. Sebagai argumen, ia
# disubstitusi setelah template diurai dan tidak pernah ditafsirkan.
NARRATE_PROMPT = """
{persona}

PERTANYAAN: {question}

DATA — dihitung Postgres, bukan olehmu. Ini SATU-SATUNYA angka yang boleh kamu
sebut:
{data}

{boundaries}

CARA MEMBACA DATA DI ATAS:
- Kalau statusnya "tidak_ada_data" atau "tidak_ada_temuan", katakan apa adanya.
  Jangan menjawab dari ingatan.
- `get_top_transactions` hanya CONTOH. Jangan dijumlahkan.
- Kalau ada "sebagian_berjalan", SEBUTKAN periodenya belum selesai — terutama
  saat dibandingkan dengan periode penuh. Tanpa itu jawabanmu menyesatkan.
- Kalau "terpotong" bernilai true, sebutkan bahwa yang ditampilkan hanya
  sebagian dari "total_cocok". Jangan menyajikannya seolah itu seluruhnya.
- Kalau ada "temuan_di_status_lain", sebutkan — "tidak ada temuan yang belum
  ditangani" sangat berbeda dari "tidak ada temuan sama sekali".
- Status "vendor_ambigu" atau "vendor_tidak_ditemukan" bukan kegagalan: sebutkan
  kandidat atau daftar vendor yang ada, lalu minta penanya mempertegas.
- Kalau ada "penyaring_tidak_didukung", KATAKAN TERUS TERANG bahwa kamu tidak
  bisa menyaring berdasarkan hal itu. Jangan menyajikan hasilnya seolah menjawab
  pertanyaan. Kalau "seluruh_penyaring_hilang" bernilai true, hasil itu hanya
  daftar apa adanya — jawab bahwa pertanyaannya belum bisa dijawab, jangan
  membacakan barisnya seakan-akan itu jawabannya.

BAHASA JAWABAN — periksa ini sebelum menulis:
Prompt ini dan sebagian isi data berbahasa Indonesia. Itu TIDAK menentukan
bahasa jawabanmu. Yang menentukan hanya kalimat ini:

    "{question}"

Isi "language" lebih dulu — "en" kalau kalimat itu bahasa Inggris, "id" kalau
bahasa Indonesia — lalu tulis "answer" dalam bahasa tersebut. Nama vendor,
kategori, dan status tetap disalin apa adanya, tidak diterjemahkan.

Balas HANYA JSON:
{{"language": "id atau en", "answer": "jawabanmu dalam bahasa tersebut"}}
"""

# Jalur untuk pertanyaan yang memang tidak butuh data: identitas, kemampuan,
# sapaan, atau hal di luar jangkauan.
#
# Sebelumnya cabang ini pulang membawa kalimat mati tanpa memanggil model sama
# sekali, sehingga persona tidak pernah sampai — "siapa kamu?" dijawab daftar
# kemampuan yang ditulis di dalam kode, dalam bahasa Indonesia apa pun bahasa
# penanyanya. Identitas yang tidak bisa memperkenalkan dirinya sendiri bukan
# identitas.
META_PROMPT = """
{persona}

PERTANYAAN: {question}

Pertanyaan ini tidak membutuhkan data transaksi.

Catatan perencana (catatan INTERNAL, selalu ditulis bahasa Indonesia; jangan
dikutip dan jangan dijadikan acuan bahasa jawabanmu): {alasan}

{capabilities}

ATURAN:
- TIDAK ADA data yang diambil, jadi jangan menyebut satu pun angka, nominal,
  nama vendor, atau temuan milik perusahaan ini.
- Kalau pertanyaannya tentang dirimu atau kemampuanmu, jawab dengan ramah dan
  ringkas, lalu sebutkan hal yang bisa ditanyakan.
- Kalau pertanyaannya soal KONSEP keuangan, audit, atau statistik — "apa itu
  z-score", "kenapa split payment berbahaya" — jawab saja, singkat dan praktis.
  Itu masih bidangmu; kamu hanya tidak sedang melihat angka perusahaan ini.
  Tawarkan menerapkannya ke data mereka kalau relevan.
- Kalau pertanyaannya DI LUAR keuangan, audit, dan statistik — pemrograman,
  biologi, resep, puisi — tolak dalam satu kalimat tanpa minta maaf berlebihan,
  lalu sebutkan bidang yang kamu tangani. Jangan mencoba menjawabnya sedikit pun.
- Jangan mengarang kemampuan yang tidak ada di daftar di atas.

BAHASA JAWABAN — periksa ini sebelum menulis:
Seluruh prompt ini berbahasa Indonesia, termasuk catatan perencana di atas.
Itu TIDAK menentukan bahasa jawabanmu. Yang menentukan hanya kalimat ini:

    "{question}"

Isi "language" lebih dulu — "en" kalau kalimat itu bahasa Inggris, "id" kalau
bahasa Indonesia — lalu tulis "answer" dalam bahasa tersebut.

Balas HANYA JSON:
{{"language": "id atau en", "answer": "jawabanmu dalam bahasa tersebut"}}
"""


def _parse(content: str) -> dict:
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0].strip()
    return json.loads(content)


def plan(question: str, today: str, data_range: str) -> dict:
    """Langkah 1: model memilih tool dan periode. Tidak melihat satu angka pun."""
    res = run_agent(
        label="Ask/rencana", prompt=PLAN_PROMPT.format(
            question=question, today=today, data_range=data_range,
            max_steps=MAX_STEPS),
        tools=[], temperature=0.0, max_output_tokens=512)
    return _parse(res.choices[0].message.content)


def execute(steps: list) -> tuple[list, list]:
    """
    Langkah 2: PYTHON menjalankan tool.

    Nama tool divalidasi terhadap whitelist dan argumennya disaring — model tidak
    bisa memanggil apa pun di luar daftar, dan tidak bisa menyelundupkan argumen
    tak dikenal.

    Argumen yang dibuang DILAPORKAN, tidak dibuang diam-diam.

    Penyaringannya sendiri benar dan tetap. Yang salah adalah diamnya. Setiap
    penyaring `search_transactions` bersifat opsional, sehingga panggilan yang
    seluruh penyaringnya terbuang berubah menjadi "tampilkan semua" dan tetap
    melapor `status: ok`. "Faktur INV-2024-001 dibayar berapa kali?" dijawab 20
    baris yang tidak berhubungan, terlihat seperti jawaban.

    Sebuah penyaring yang diminta model adalah pernyataan tentang APA YANG
    DITANYAKAN. Membuangnya berarti membuang pertanyaannya, dan itu harus
    terbaca di hasil — sebab jawaban yang salah dengan percaya diri lebih
    merugikan daripada penolakan yang jujur.
    """
    results, used = [], []
    for step in (steps or [])[:MAX_STEPS]:
        name = (step or {}).get("tool")
        entry = TOOLS.get(name)
        if not entry:
            results.append({"tool": name, "error": f"Tool '{name}' tidak dikenal."})
            continue

        fn, allowed = entry
        diminta = (step or {}).get("args") or {}
        args = {k: v for k, v in diminta.items() if k in allowed}
        dibuang = {k: v for k, v in diminta.items() if k not in allowed}

        item: dict = {"tool": name, "args": args}
        if dibuang:
            item["penyaring_tidak_didukung"] = dibuang
            item["peringatan_penyaring"] = (
                f"Penyaring {list(dibuang)} diminta tetapi TIDAK didukung "
                f"{name}, jadi diabaikan. Hasil di bawah TIDAK tersaring menurut "
                f"itu dan mungkin tidak menjawab pertanyaannya."
            )
            # Seluruh penyaring hilang berarti yang tersisa bukan pencarian lagi,
            # melainkan daftar apa adanya.
            item["seluruh_penyaring_hilang"] = not args

        try:
            item["result"] = fn(**args)
            used.append(name)
        except Exception as e:
            item["error"] = str(e)[:200]
        results.append(item)

    return results, used


# Field yang layak diangkat sebagai "angka jawaban", beserta labelnya.
FIGURE_FIELDS = {
    "pendapatan": "pendapatan", "biaya": "biaya", "laba": "laba",
    "margin_persen": "margin (%)", "total": "total",
    "total_temuan": "jumlah temuan", "belum_ditangani": "belum ditangani",
    "total_nominal": "total nominal", "rata_rata": "rata-rata",
    "jumlah_transaksi": "jumlah transaksi", "total_cocok": "jumlah yang cocok",
    "risk_score": "skor risiko",
}

# Kunci yang isinya daftar baris, beserta cara memberi nama tiap baris dan field
# nominal yang diambil. Ditulis sebagai data supaya menambah tool baru berarti
# menambah satu baris di sini, bukan satu blok `for` lagi di bawah.
FIGURE_ROWS = {
    "bulan": ("bulan", ("pendapatan", "biaya", "laba")),
    "kategori": ("kategori", ("total",)),
    "vendor": ("vendor", ("total",)),
    "transaksi": ("id", ("nominal",)),
    "transaksi_terakhir_rinci": ("id", ("nominal",)),
    "temuan": ("finding_id", ("nominal", "risk_score")),
    "per_tingkat": ("tingkat", ("jumlah", "belum_ditangani", "nilai_transaksi")),
}


def _rows(value) -> list:
    """
    Baris yang benar-benar bisa diperlakukan sebagai baris.

    Penjagaan ini ada karena `transaksi` dipakai dua bentuk: daftar transaksi
    pada hasil pencarian, dan SATU dict pada rincian temuan. Mengulangi sebuah
    dict menghasilkan nama-nama kuncinya sebagai string, dan indeks string ke
    string melempar TypeError yang menjatuhkan seluruh endpoint — bukan sekadar
    kehilangan satu angka.
    """
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def collect_figures(results: list) -> list:
    """
    Langkah 2b: PYTHON merakit daftar angka dari hasil tool.

    Dulu daftar ini dibuat model, dan itulah lubangnya — ia mengisi angka
    karangan lengkap dengan sumber karangan. Sekarang tiap entri berasal dari
    nilai kembalian fungsi, jadi tidak ada jalan bagi angka yang tidak pernah
    dihitung untuk muncul di sini.

    Cakupannya harus mengikuti SETIAP tool yang mengembalikan angka. Tool yang
    terlewat di sini tidak diam-diam kehilangan satu baris — angkanya justru
    ditandai `audit_figures` sebagai tak bersumber, sehingga jawaban yang benar
    terbit dengan peringatan palsu.
    """
    figures = []

    def add(label, value, tool, period):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            figures.append({"label": label.strip(), "value": value,
                            "source": tool, "period": period})

    for item in results:
        res, tool = item.get("result"), item.get("tool")
        if not isinstance(res, dict):
            continue
        period = res.get("periode") or item.get("args", {}).get("period")

        for key, label in FIGURE_FIELDS.items():
            add(f"{label} {period or ''}", res.get(key), tool, period)

        for key, (name_field, value_fields) in FIGURE_ROWS.items():
            for row in _rows(res.get(key)):
                nama = row.get(name_field, "?")
                for vf in value_fields:
                    add(f"{key} {nama} {vf}", row.get(vf), tool,
                        row.get("bulan", period))

        # Rincian satu temuan: nominal transaksinya dan susunan skornya.
        for blok in ("transaksi", "susunan_skor"):
            isi = res.get(blok)
            if isinstance(isi, dict):
                for k, v in isi.items():
                    add(f"{blok} {k}", v, tool, period)

        for key in ("pendapatan", "biaya", "laba"):
            blk = res.get(key)
            if isinstance(blk, dict) and "selisih" in blk:
                for sub in ("sebelum", "sesudah", "selisih"):
                    add(f"{key} {sub}", blk.get(sub), tool, period)

    return figures


def narrate(question: str, results: list):
    """
    Langkah 3: model menarasikan angka yang sudah jadi. Tanpa akses tool.

    Hanya langkah ini yang memakai persona. Perencana tidak: keluarannya JSON
    pemilihan tool yang tidak pernah dibaca manusia, jadi identitas di sana
    hanya menambah token tanpa mengubah apa pun.

    Suhu tetap 0.0. Persona mengatur bentuk kalimat, bukan keleluasaan menebak —
    menaikkan suhu demi "terdengar lebih manusiawi" berarti membayarnya dengan
    kepatuhan pada angka.
    """
    payload = json.dumps(results, ensure_ascii=False, indent=2, default=str)
    return run_agent(
        label="Ask/narasi",
        prompt=NARRATE_PROMPT.format(persona=persona.preamble(),
                                     boundaries=persona.BOUNDARIES,
                                     question=question, data=payload),
        tools=[], temperature=0.0, max_output_tokens=1024)


SKALA = {"ribu": 1e3, "juta": 1e6, "miliar": 1e9, "triliun": 1e12}

# Hanya angka BERKONTEKS UANG yang diaudit: didahului "Rp", atau diikuti kata
# skala. Versi sebelumnya memungut setiap deretan angka, sehingga nomor
# transaksi ("1887"), jumlah baris ("1.234 transaksi"), dan nomor faktur ikut
# ditandai tak bersumber. Peringatan yang menyala pada jawaban benar akan
# diabaikan orang, dan begitu diabaikan ia berhenti melindungi apa pun —
# padahal justru inilah satu-satunya penjaga terhadap angka karangan.
UANG = re.compile(
    r"Rp\s?([\d.,]+)\s*(ribu|juta|miliar|triliun)?"
    r"|([\d.,]+)\s*(ribu|juta|miliar|triliun)\b",
    re.IGNORECASE,
)


def _nominal(angka: str, skala: str | None) -> tuple[float, float] | None:
    """
    Mengurai "1.234,6" + "juta" menjadi (nilai, presisi_tulisan).

    Presisi ikut dikembalikan karena itulah dasar toleransi yang benar: yang
    menulis "Rp1,2 miliar" hanya menjanjikan ketelitian sampai 0,1 miliar, dan
    menuntutnya lebih tepat dari itu berarti menghukum bentuk ringkas yang justru
    diminta persona.
    """
    bersih = angka.strip(".,")
    if not bersih or not bersih[0].isdigit():
        return None
    try:
        nilai = float(bersih.replace(".", "").replace(",", "."))
    except ValueError:
        return None

    faktor = SKALA.get((skala or "").lower(), 1.0)
    desimal = len(bersih.split(",")[1]) if "," in bersih else 0
    return nilai * faktor, faktor / (10 ** desimal)


# Angka polos di dalam payload JSON hasil tool: "median": 50000000.0.
ANGKA_JSON = re.compile(r"\d+(?:\.\d+)?")


def _angka_dalam_payload(results: list) -> set:
    """
    Setiap angka yang benar-benar muncul di hasil tool.

    `figures` hanya memanen FIELD numerik, sementara sebagian angka yang sah
    hidup di dalam kalimat: penjelasan tiap pemicu dirakit Python
    ("biasanya sekitar Rp50 juta"), dan ringkasan temuan tersimpan sebagai teks.
    Tanpa sumber ini, jawaban yang mengutip angka dari kalimat tersebut ditandai
    palsu — dan peringatan yang salah lebih merusak daripada tidak ada
    peringatan, karena ia mengajari orang mengabaikannya.

    Dua bentuk dipungut sekaligus: angka gaya JSON (50000000.0) dan gaya
    Indonesia di dalam teks (Rp50.000.000, "Rp1,2 miliar").
    """
    if not results:
        return set()
    payload = json.dumps(results, ensure_ascii=False, default=str)

    angka = {float(m.group()) for m in ANGKA_JSON.finditer(payload)}
    for m in UANG.finditer(payload):
        a, s = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if hasil := _nominal(a, s):
            angka.add(hasil[0])
    return angka


def answer_without_data(question: str, alasan: str | None) -> str:
    """
    Menjawab pertanyaan yang tidak butuh tool, tetap dengan suara Sentinel.

    Aman dilakukan justru karena tidak ada data yang diambil: tidak ada angka
    yang bisa salah dikutip, sehingga satu-satunya yang dipertaruhkan adalah
    kalimatnya. Itu sebabnya persona boleh bekerja penuh di sini sementara di
    jalur bernomor ia dikurung ketat.

    Kalau panggilan ini gagal, kalimat cadangan tetap terbit — pertanyaan
    sesederhana "siapa kamu?" tidak boleh berakhir sebagai error.
    """
    try:
        res = run_agent(
            label="Ask/meta",
            prompt=META_PROMPT.format(persona=persona.preamble(),
                                      capabilities=persona.CAPABILITIES,
                                      question=question,
                                      alasan=(alasan or "tidak ada tool yang cocok")),
            tools=[], temperature=0.0, max_output_tokens=512)
        jawaban = (_parse(res.choices[0].message.content).get("answer") or "").strip()
        if jawaban:
            return jawaban
    except Exception:
        pass
    return persona.OUT_OF_SCOPE


def audit_figures(answer: str, figures: list, results: list | None = None) -> list:
    """
    Nominal di jawaban yang tidak pernah muncul di hasil tool.

    Yang dibuktikan pemeriksaan ini: angka itu tidak dikarang. Yang TIDAK
    dibuktikannya: bahwa angka itu dipakai pada tempat yang benar — mengutip
    biaya lalu menyebutnya laba tetap lolos. Menutup celah kedua butuh menilai
    makna kalimat, dan itu tidak bisa dikerjakan dengan mencocokkan bilangan.
    """
    declared = _angka_dalam_payload(results)
    for f in figures or []:
        try:
            declared.add(float(f["value"]))
        except (TypeError, ValueError, KeyError):
            continue

    found: dict[float, float] = {}
    for m in UANG.finditer(answer or ""):
        angka, skala = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        hasil = _nominal(angka, skala)
        if hasil:
            nilai, presisi = hasil
            # Presisi terlonggar menang: nominal yang sama boleh disebut dua kali
            # dengan ketelitian berbeda dalam satu jawaban.
            found[nilai] = max(found.get(nilai, 0), presisi)

    def near(n: float, presisi: float) -> bool:
        toleransi = max(1.0, presisi / 2)
        return any(abs(n - d) <= max(toleransi, abs(d) * 0.005) for d in declared)

    return sorted(round(n) for n, presisi in found.items()
                  if n >= 1000 and not near(n, presisi))


def ask_sentinel(question: str, today: str, data_range: str) -> dict:
    """Alur lengkap: rencana -> Python menjalankan -> narasi."""
    try:
        planned = plan(question, today, data_range)
    except Exception as e:
        return {"error": "Tidak dapat merencanakan jawaban.", "detail": str(e)[:200]}

    steps = planned.get("steps") or []
    if not steps:
        return {"answer": answer_without_data(question, planned.get("alasan")),
                "figures": [], "tools_used": [], "steps": []}

    results, used = execute(steps)
    figures = collect_figures(results)

    try:
        answer = _parse(narrate(question, results).choices[0].message.content).get("answer", "")
    except Exception as e:
        return {"error": "Tidak dapat menyusun jawaban.", "detail": str(e)[:200],
                "figures": figures, "tools_used": used}

    return {
        "answer": answer,
        "figures": figures,
        "tools_used": used,
        "steps": [{"tool": r.get("tool"), "args": r.get("args")} for r in results],
        "unsourced_figures": audit_figures(answer, figures, results),
    }
