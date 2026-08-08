"""
Klien SSE untuk POST /api/analyze.

Menggantikan benchmark_analyze.py yang memanggil response.json() padahal
endpoint mengembalikan text/event-stream, sehingga selalu gagal mengurai.

Pakai:  python scripts/run_analysis.py [start_date] [end_date]
"""
import json
import sys
import time

import requests

URL = "http://127.0.0.1:8000/api/analyze"


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2020-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2030-12-31"

    print(f"POST {URL}  {start} .. {end}\n")
    t0 = time.time()
    findings = []

    with requests.post(URL, json={"start_date": start, "end_date": end},
                       stream=True, timeout=900) as resp:
        print(f"HTTP {resp.status_code}\n")
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
        for trigger in s.get("objective_triggers", []):
            print(f"        - {trigger}")
        if s.get("adjustment_reason"):
            print(f"        alasan A3: {s['adjustment_reason'][:150]}")

    out = "scripts/last_run.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(findings, fh, indent=2, ensure_ascii=False)
    print(f"\nHasil lengkap: {out}")


if __name__ == "__main__":
    main()
