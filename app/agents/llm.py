"""
Klien LLM bersama untuk ketiga agen.

Menggantikan tangga fallback 4 model yang sebelumnya disalin di agent1/2/3.
Tangga itu punya dua masalah:

1. Ia MEMBUAT ULANG sesi chat pada setiap percobaan
   (`client.chats.create(...)` di dalam loop), sehingga seluruh state
   tool-calling yang sudah terkumpul hilang begitu terjadi fallback. Agen
   memulai dari nol tanpa ada yang tahu.

2. Ia menukar model diam-diam. Dua temuan pada satu run bisa dihasilkan model
   berbeda tanpa tercatat di mana pun — padahal proyek ini menjanjikan setiap
   angka dapat ditelusuri.

Diganti: satu model yang dipin lewat environment, dengan exponential backoff
pada error yang memang sementara. Nama model dikembalikan bersama hasilnya agar
dapat disimpan ke provenance.
"""
import os
import random
import sys
import time

from dotenv import dotenv_values, find_dotenv, load_dotenv
from google import genai
from google.genai import types

# Windows: cegah UnicodeEncodeError saat mencetak keluaran agen.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

DEFAULT_MODEL = "models/gemini-3.5-flash"
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 2.0

# Hanya error sementara yang layak diulang. Sisanya (400, kunci salah, prompt
# ditolak) tidak akan membaik dengan menunggu, jadi langsung dilempar.
TRANSIENT_MARKERS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                     "DEADLINE_EXCEEDED", "INTERNAL")


def pinned_model(agent: str | None = None) -> str:
    """
    Model untuk sebuah agen.

    `GEMINI_MODEL` berlaku untuk semua; `GEMINI_MODEL_AGENT3` menimpanya khusus
    untuk verifikator.

    Dipisah karena bebannya memang berbeda, dan itu terukur. Dengan
    gemini-3.1-flash-lite sebagai verifikator, transaksi yang PERSIS SAMA
    menghasilkan tiga putusan berbeda dalam tiga kali jalan:
        -15 (Low Risk), +15 (High Risk), -10 (Medium Risk)
    Skor objektifnya stabil di 50 pada ketiganya — yang bergoyang hanya
    penilaian LLM. Agent 1 dan 2 hanya menarasikan dan menyelidiki, jadi model
    ringan memadai; Agent 3 memutuskan angka, dan itu butuh model yang lebih kuat.
    """
    base = _model_setting("GEMINI_MODEL") or DEFAULT_MODEL
    if agent == "agent3":
        return _model_setting("GEMINI_MODEL_AGENT3") or base
    return base


def _model_setting(key: str) -> str | None:
    """
    Membaca setelan model LANGSUNG dari berkas .env, bukan dari os.environ.

    `load_dotenv(override=True)` hanya MENIMPA kunci yang ada di berkas; ia tidak
    pernah MENGHAPUS kunci yang sudah telanjur masuk os.environ. Akibatnya
    mengomentari atau menghapus sebuah baris di .env tidak berpengaruh sama
    sekali pada proses yang sedang berjalan — ia terus memakai nilai lama.

    Itu bukan teori: setelah GEMINI_MODEL_AGENT3 dikomentari, server yang sedang
    hidup tetap memanggil model lama dan Agent 3 terus gagal 429, sementara
    proses baru membaca nilai yang benar. Membingungkan justru karena keduanya
    "benar" menurut sumbernya masing-masing.

    Dengan membaca berkasnya langsung, mengomentari sebuah baris berperilaku
    seperti yang diharapkan. os.environ hanya dipakai kalau .env tidak ada
    (mis. saat dijalankan di container dengan env var sungguhan).
    """
    path = find_dotenv(usecwd=True)
    if path:
        return dotenv_values(path).get(key)
    return os.environ.get(key)


def _is_transient(error: Exception) -> bool:
    text = str(error)
    return any(marker in text for marker in TRANSIENT_MARKERS)


class AgentResponse:
    """
    Pembungkus berbentuk OpenAI (`.choices[0].message.content`).

    Bentuk ini warisan dari saat proyek memakai klien OpenAI. Dipertahankan agar
    main.py tidak perlu diubah, tetapi sekarang membawa `model` dan `attempts`
    supaya provenance bisa mencatat model mana yang benar-benar menjawab.
    """

    class _Message:
        def __init__(self, content: str):
            self.content = content

    class _Choice:
        def __init__(self, content: str):
            self.message = AgentResponse._Message(content)

    def __init__(self, content: str, model: str, attempts: int):
        self.choices = [AgentResponse._Choice(content)]
        self.model = model
        self.attempts = attempts


def run_agent(*, label: str, prompt: str, tools: list, agent: str | None = None,
              temperature: float = 0.2, max_output_tokens: int = 4096) -> AgentResponse:
    """
    Menjalankan satu agen sampai selesai.

    `tools` boleh kosong — artinya agen hanya menarasikan fakta yang sudah
    diberikan di dalam prompt, tanpa bisa menjelajah.

    `agent` menentukan model mana yang dipakai (lihat pinned_model).
    """
    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    model = pinned_model(agent)

    config = types.GenerateContentConfig(
        tools=tools or None,
        temperature=temperature,
        top_p=0.95,
        max_output_tokens=max_output_tokens,
    )

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"[{label}] {model} (percobaan {attempt}/{MAX_ATTEMPTS})"
                  f"{' tanpa tool' if not tools else f', {len(tools)} tool konteks'}...")
            # Sesi dibuat baru hanya saat benar-benar mengulang dari awal, dan
            # modelnya selalu sama — tidak ada state yang hilang diam-diam.
            chat = client.chats.create(model=model, config=config)
            response = chat.send_message(prompt)
            return AgentResponse(response.text, model, attempt)

        except Exception as error:
            last_error = error
            if not _is_transient(error) or attempt == MAX_ATTEMPTS:
                raise

            # Exponential backoff + jitter, supaya beberapa agen yang berjalan
            # paralel tidak mencoba ulang pada detik yang persis sama.
            delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.5 * delay)
            print(f"[{label}] Error sementara: {error}. Mengulang dalam {delay:.1f}s...")
            time.sleep(delay)

    raise last_error  # pragma: no cover - tidak tercapai
