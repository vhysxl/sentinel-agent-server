"""
Smoke test untuk kesepuluh tool.

Tujuannya membuktikan satu hal: setiap tool benar-benar cocok dengan skema
database. Sebelum perbaikan Fase 0, lima tool gagal dengan
`UndefinedColumn: column u.department does not exist` — dan karena
`get_transaction_details` dipanggil ketiga agen, seluruh pipeline mati.

Sepenuhnya READ-ONLY: tidak menulis apa pun ke database.
Menjalankan tool dengan ID nyata hasil seed, bukan ID karangan, supaya query-nya
benar-benar dieksekusi Postgres — memanggil dengan ID yang tidak ada hanya
menghasilkan {"error": "not found"} dan tidak membuktikan SQL-nya benar.

Prasyarat: `python scripts/seed_transactions.py` sudah dijalankan.
"""
import os
import sys
import json
import traceback

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.tools.financial import (
    get_transaction_details,
    calculate_z_score,
    get_vendor_transaction_history,
    check_transaction_timing,
    get_sales_trend,
    compare_category_baseline,
    get_user_spending_pattern,
    get_monthly_expense_trend,
)
from app.tools.fraud import find_duplicate_expenses, get_vendor_history

EXPECTATIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "seed_expectations.json")


def load_context():
    """Ambil ID nyata dari database untuk dipakai sebagai argumen tool."""
    if not os.path.exists(EXPECTATIONS):
        raise SystemExit("seed_expectations.json tidak ada. "
                         "Jalankan scripts/seed_transactions.py lebih dulu.")
    exp = json.load(open(EXPECTATIONS, encoding="utf-8"))
    cases = {c["id"]: c for c in exp["cases"]}

    db = SessionLocal()
    try:
        dup_id = cases["B"]["transaction_ids"][0]
        row = db.execute(text("""
            SELECT amount, vendor_id,
                   to_char(transaction_date AT TIME ZONE 'Asia/Jakarta',
                           'YYYY-MM-DD HH24:MI:SS'),
                   to_char(transaction_date AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM')
            FROM transactions WHERE id = :i
        """), {"i": dup_id}).fetchone()
        user_id = db.execute(text(
            "SELECT input_by_user_id FROM transactions WHERE id = :i"
        ), {"i": cases["A"]["transaction_ids"][0]}).scalar()
        return {
            "spike_id": cases["A"]["transaction_ids"][0],
            "dup_id": dup_id,
            "dup_amount": float(row[0]),
            "dup_vendor": row[1],
            "dup_date": row[2],
            "dup_month": row[3],
            "clean_vendor": db.execute(text(
                "SELECT id FROM vendors WHERE vendor_name = 'CV. ATK Sejahtera'"
            )).scalar(),
            "user_id": user_id,
        }
    finally:
        db.close()


def main():
    ctx = load_context()
    print("Konteks:", json.dumps(ctx, ensure_ascii=False))
    print()

    checks = [
        ("get_transaction_details", lambda: get_transaction_details(ctx["spike_id"])),
        ("calculate_z_score", lambda: calculate_z_score(ctx["spike_id"])),
        ("get_vendor_transaction_history", lambda: get_vendor_transaction_history(ctx["clean_vendor"])),
        ("check_transaction_timing", lambda: check_transaction_timing(ctx["dup_id"])),
        ("get_sales_trend", lambda: get_sales_trend(ctx["dup_month"])),
        ("compare_category_baseline", lambda: compare_category_baseline(ctx["spike_id"])),
        ("get_user_spending_pattern", lambda: get_user_spending_pattern(ctx["user_id"])),
        ("get_monthly_expense_trend", lambda: get_monthly_expense_trend(6)),
        ("find_duplicate_expenses", lambda: find_duplicate_expenses(
            ctx["dup_amount"], ctx["dup_vendor"], ctx["dup_date"])),
        ("get_vendor_history", lambda: get_vendor_history(ctx["clean_vendor"])),
    ]

    failures = []
    for name, fn in checks:
        try:
            result = fn()
        except Exception as exc:
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"  GAGAL  {name}")
            traceback.print_exc()
            continue

        # Tool boleh mengembalikan dict berisi {"error": ...} untuk data yang
        # memang tidak ada. Yang TIDAK boleh adalah error skema.
        blob = json.dumps(result, default=str, ensure_ascii=False)
        if "does not exist" in blob or "UndefinedColumn" in blob:
            failures.append((name, "error skema di dalam hasil"))
            print(f"  GAGAL  {name}  -> {blob[:160]}")
            continue

        preview = blob if len(blob) <= 150 else blob[:150] + "..."
        print(f"  ok     {name:32} {preview}")

    print()
    if failures:
        print(f"{len(failures)} dari {len(checks)} tool GAGAL:")
        for name, why in failures:
            print(f"  - {name}: {why}")
        sys.exit(1)

    print(f"Semua {len(checks)} tool berjalan tanpa error skema.")


if __name__ == "__main__":
    main()
