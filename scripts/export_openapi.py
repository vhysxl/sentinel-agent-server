"""
Mengekspor skema OpenAPI app ke openapi/snapshot.json.

Inilah sumber kontrak lintas-repo: sentinel-backend dan sentinel mem-pull
snapshot ini (via AGENT_CONTRACT_URL / raw GitHub) dan men-generate konstanta
serta tipe mereka dari situ. Jalankan setiap kali model Pydantic atau enum di
app.api_models / app.core.constants berubah:

    python scripts/export_openapi.py

Snapshot dikomit supaya konsumen bisa build tanpa server hidup. Dibuat
in-process lewat app.openapi(), bukan HTTP, sehingga tidak tersangkut
pemeriksaan kunci internal.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

out = Path(__file__).resolve().parent.parent / "openapi" / "snapshot.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False), encoding="utf-8")
print(f"openapi snapshot -> {out}")
