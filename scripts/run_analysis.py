"""
Klien SSE untuk POST /api/analyze.

Menggantikan benchmark_analyze.py yang memanggil response.json() padahal
endpoint mengembalikan text/event-stream, sehingga selalu gagal mengurai.

Pakai:  python scripts/run_analysis.py [start_date] [end_date]
"""
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

URL = "http://127.0.0.1:8000/api/analyze"

# Skrip ini memanggil lewat HTTP, jadi ia tunduk pada handshake yang sama seperti
# Express. Tanpa kunci, jawabannya 401 dan bukan kegagalan analisis.
HEADERS = {"X-Internal-Key": os.getenv("INTERNAL_API_KEY", "")}


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2020-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2030-12-31"

    print(f"POST {URL}  {start} .. {end}\n")
    t0 = time.time()
    findings = []

    with requests.post(URL, json={"start_date": start, "end_date": end},
                       headers=HEADERS, stream=True, timeout=900) as resp:
        print(f"HTTP {resp.status_code}\n")
        if resp.status_code == 401:
            print("Ditolak. Pasang INTERNAL_API_KEY di .env dengan nilai yang sama "
                  "seperti yang dipakai agent server.")
            sys.exit(1)
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            event = json.loads(raw[6:])
            status = event.get("status")

            if status in ("progress", "info"):
                print(f"  [{event.get('node', 'info'):14}] {event.get('message', '')}")
            elif status == "error":
                print(f"\nERROR: {event.get('message')}")
                sys.exit(1)
            elif status == "complete":
                findings = event.get("findings", [])

    print(f"\nSelesai dalam {time.time() - t0:.1f} detik. {len(findings)} temuan.\n")

    for f in findings:
        if "error" in f:
            print(f"  tx {f['transaction_id']}  GAGAL: {f['error']} — {f.get('raw_response', '')[:120]}")
            continue
        s = f.get("scoring", {})
        print(f"  tx {f['transaction_id']:<5} {s.get('risk_level', '?'):<14} "
              f"base={s.get('base_risk_score')} adj={s.get('llm_semantic_adjustment')} "
              f"final={s.get('final_risk_score')}")
        for t in s.get("objective_triggers", []):
            tag = " [amplifier]" if t.get("amplifier_only") else ""
            print(f"        +{t['points']:<3} {t['code']:<22}{tag}")
            print(f"             {t['narrative'][:120]}")
        if s.get("adjustment_reason"):
            print(f"        A3 ({s.get('llm_semantic_adjustment'):+d}): "
                  f"{s['adjustment_reason'][:130]}")

    out = "scripts/last_run.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(findings, fh, indent=2, ensure_ascii=False)
    print(f"\nHasil lengkap: {out}")


if __name__ == "__main__":
    main()
