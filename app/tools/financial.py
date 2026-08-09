from typing import Dict, Any, List
from sqlalchemy import func, text
from app.db.session import SessionLocal
from app.db.models import Transaction, User, Vendor
from app.core.config import WIB, WORK_START_HOUR, WORK_END_HOUR
from app.core.roles import role_of
from app.engine import statistics

def calculate_z_score(transaction_id: int) -> Dict[str, Any]:
    """
    Menghitung modified z-score (median + MAD) sebuah transaksi terhadap riwayat
    vendor, dengan fallback ke baseline kategori. Berguna untuk mendeteksi
    lonjakan nominal yang tidak wajar.
    """
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            return {"error": f"Transaction {transaction_id} not found."}

        # Memakai modul yang sama dengan scoring engine, supaya angka yang
        # dinarasikan agen identik dengan angka yang menghasilkan skor.
        vendor_baseline = [float(r[0]) for r in db.execute(text("""
            SELECT amount FROM transactions
            WHERE vendor_id = :v AND type = 'expense' AND id <> :i
        """), {"v": txn.vendor_id, "i": transaction_id})] if txn.vendor_id else []

        category_baseline = [float(r[0]) for r in db.execute(text("""
            SELECT amount FROM transactions
            WHERE category = :c AND type = 'expense' AND id <> :i
        """), {"c": txn.category, "i": transaction_id})]

        result = statistics.evaluate(float(txn.amount), vendor_baseline, category_baseline)

        return {
            "transaction_id": transaction_id,
            "amount": float(txn.amount),
            "modified_z_score": result.computed_value,
            "method": result.method,
            "baseline_scope": result.scope,
            "n_baseline": result.n_baseline,
            "median": result.median,
            "mad": result.mad,
            "threshold": result.threshold,
            "confidence": result.confidence,
            "status": "anomaly" if result.is_anomaly else (
                "insufficient_data"
                if result.method == statistics.METHOD_INSUFFICIENT else "normal"
            ),
            "message": result.message,
        }
    finally:
        db.close()

def get_sales_trend(month: str) -> Dict[str, Any]:
    """
    Pertumbuhan pendapatan sebuah bulan dibanding bulan sebelumnya.
    Digunakan untuk memverifikasi klaim bahwa pengeluaran besar dibutuhkan
    karena permintaan tinggi. Format month: 'YYYY-MM' atau 'YYYY-MM-DD'.
    """
    db = SessionLocal()
    try:
        month_key = month.strip()[:7] + "-01"

        # Pendapatan dibaca dari transaksi `type='income'` — sumber yang sama
        # dengan biaya. Sebelumnya angka ini datang dari tabel monthly_revenue
        # terpisah, sehingga satu pertanyaan bisa dijawab dua angka berbeda.
        rows = db.execute(text("""
            SELECT to_char(date_trunc('month', created_at AT TIME ZONE 'Asia/Jakarta'),
                           'YYYY-MM-DD') AS bulan,
                   SUM(amount) AS pendapatan,
                   LAG(SUM(amount)) OVER (ORDER BY date_trunc('month',
                       created_at AT TIME ZONE 'Asia/Jakarta')) AS sebelumnya
            FROM transactions
            WHERE type = 'income'
            GROUP BY date_trunc('month', created_at AT TIME ZONE 'Asia/Jakarta')
            ORDER BY 1
        """)).fetchall()

        if not rows:
            return {
                "month": month_key, "status": "no_revenue_data",
                "message": "Belum ada transaksi pendapatan; tren tidak dapat diverifikasi."
            }

        target = next((r for r in rows if r[0] == month_key), None)
        if target is None:
            return {
                "month": month_key, "status": "month_not_found",
                "available_months": [r[0] for r in rows],
                "message": f"Tidak ada pendapatan tercatat untuk {month_key}."
            }

        revenue = float(target[1])
        prev = float(target[2]) if target[2] is not None else None
        if prev is None or prev == 0:
            return {"month": month_key, "revenue": revenue,
                    "status": "insufficient_history",
                    "message": "Tidak ada bulan pembanding sebelumnya."}

        growth = ((revenue - prev) / prev) * 100
        status = "growing" if growth > 25 else "declining" if growth < -25 else "stagnant"

        return {
            "month": month_key,
            "revenue": revenue,
            "previous_month_revenue": prev,
            "trend_percentage": round(growth, 2),
            "status": status,
            "source": "transactions.type=income",
            "description": (f"Pendapatan {month_key}: {revenue:,.0f} vs bulan "
                            f"sebelumnya {prev:,.0f} ({growth:+.1f}%)."),
        }
    finally:
        db.close()


def compare_category_baseline(transaction_id: int) -> Dict[str, Any]:
    """
    Membandingkan nilai transaksi terhadap baseline kategori yang sama.
    Berguna untuk melihat apakah transaksi besar masih wajar di konteks kategorinya.
    """
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            return {"error": f"Transaction {transaction_id} not found."}

        # Tidak ada dimensi departemen: aplikasi ini dipakai satu tim keuangan,
        # jadi baseline yang bermakna adalah kategori, bukan struktur organisasi.
        stats_sql = text("""
            SELECT
                COUNT(*) AS sample_size,
                AVG(t.amount) AS mean_amount,
                COALESCE(NULLIF(STDDEV(t.amount), 0), 0) AS stddev_amount,
                MIN(t.amount) AS min_amount,
                MAX(t.amount) AS max_amount
            FROM transactions t
            WHERE t.type = :txn_type
              AND t.category = :category
              AND t.id != :transaction_id
        """)
        result = db.execute(stats_sql, {
            "txn_type": txn.type,
            "category": txn.category,
            "transaction_id": transaction_id
        }).fetchone()

        sample_size = int(result[0] or 0)
        if sample_size == 0 or result[1] is None:
            return {
                "transaction_id": transaction_id,
                "category": txn.category,
                "sample_size": sample_size,
                "status": "insufficient_data",
                "message": "Tidak ada baseline kategori yang cukup untuk pembanding."
            }

        mean_amount = float(result[1])
        stddev_amount = float(result[2] or 0)
        amount = float(txn.amount)
        z_score = 0.0 if stddev_amount == 0 else (amount - mean_amount) / stddev_amount
        ratio_to_mean = amount / mean_amount if mean_amount else 0.0

        if ratio_to_mean >= 3 or z_score > 3:
            status = "category_outlier"
        elif ratio_to_mean >= 1.5 or z_score > 2:
            status = "above_baseline"
        else:
            status = "within_baseline"

        return {
            "transaction_id": transaction_id,
            "category": txn.category,
            "sample_size": sample_size,
            "transaction_amount": amount,
            "category_mean": round(mean_amount, 2),
            "category_stddev": round(stddev_amount, 2),
            "ratio_to_mean": round(ratio_to_mean, 2),
            "z_score": round(z_score, 2),
            "status": status,
            "range": {
                "min": float(result[3]),
                "max": float(result[4])
            }
        }
    finally:
        db.close()

def get_vendor_transaction_history(vendor_id: int) -> Dict[str, Any]:
    """
    Mengambil ringkasan histori transaksi vendor dari database.
    Berguna sebagai evidence apakah vendor punya rekam jejak cukup atau masih baru/tidak biasa.
    """
    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            return {"error": f"Vendor {vendor_id} not found."}

        stats = db.query(
            func.count(Transaction.id).label("total_transactions"),
            func.avg(Transaction.amount).label("mean_amount"),
            func.sum(Transaction.amount).label("total_amount"),
            func.min(Transaction.created_at).label("first_transaction_date"),
            func.max(Transaction.created_at).label("last_transaction_date")
        ).filter(Transaction.vendor_id == vendor_id).first()

        total_transactions = int(stats.total_transactions or 0)
        status = "new_vendor" if total_transactions < 3 else "established_vendor"

        recent_sql = text("""
            SELECT id,
                   to_char(created_at AT TIME ZONE 'Asia/Jakarta',
                           'YYYY-MM-DD HH24:MI') AS recorded_wib,
                   amount, type, category, description
            FROM transactions
            WHERE vendor_id = :vendor_id
            ORDER BY created_at DESC
            LIMIT 5
        """)
        recent_rows = db.execute(recent_sql, {"vendor_id": vendor_id}).fetchall()

        return {
            "vendor_id": vendor_id,
            "vendor_name": vendor.vendor_name,
            "vendor_status": vendor.status,
            "join_date": str(vendor.join_date),
            "total_transactions": total_transactions,
            "mean_amount": round(float(stats.mean_amount or 0), 2),
            "total_amount": round(float(stats.total_amount or 0), 2),
            "first_transaction_date": str(stats.first_transaction_date) if stats.first_transaction_date else None,
            "last_transaction_date": str(stats.last_transaction_date) if stats.last_transaction_date else None,
            "history_status": status,
            "recent_transactions": [
                {
                    "transaction_id": row[0],
                    "recorded_at": str(row[1]),
                    "amount": float(row[2]),
                    "type": row[3],
                    "category": row[4],
                    "description": row[5]
                }
                for row in recent_rows
            ]
        }
    finally:
        db.close()

def check_transaction_timing(transaction_id: int) -> Dict[str, Any]:
    """
    Mengecek apakah transaksi terjadi di dalam atau di luar jam kerja (WIB).
    Jam kerja = Senin-Jumat 08:00-17:59.

    Sistem hanya punya satu waktu: `created_at`, yaitu saat transaksi tercatat
    dari API bank. Itulah kapan transaksi terjadi.
    """
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            return {"error": f"Transaction {transaction_id} not found."}

        # WAJIB dikonversi ke WIB dulu. Kolomnya timestamptz dan server database
        # ber-timezone GMT, jadi membaca jam langsung menghasilkan jam UTC —
        # 09:00 WIB akan terbaca 02:00 dan salah ditandai sebagai tengah malam.
        local = txn.created_at.astimezone(WIB)

        is_workday = local.weekday() < 5
        is_workhour = WORK_START_HOUR <= local.hour < WORK_END_HOUR
        outside = not (is_workday and is_workhour)

        return {
            "transaction_id": transaction_id,
            "evaluated_field": "created_at",
            "recorded_at_wib": local.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": local.strftime("%A"),
            "time_wib": local.strftime("%H:%M:%S"),
            "timezone": "Asia/Jakarta",
            "work_hours": f"Senin-Jumat {WORK_START_HOUR:02d}:00-{WORK_END_HOUR - 1:02d}:59",
            "is_workday": is_workday,
            "is_work_hour": is_workhour,
            "status": "outside_hours" if outside else "normal",
        }
    finally:
        db.close()

def get_user_spending_pattern(user_id: int) -> Dict[str, Any]:
    """
    Mengambil pola transaksi yang diinput oleh user.
    Berguna untuk melihat apakah user tersebut biasa membuat transaksi besar atau dengan vendor tertentu.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"error": f"User {user_id} not found."}

        stats = db.query(
            func.count(Transaction.id).label("total_transactions"),
            func.avg(Transaction.amount).label("mean_amount"),
            func.max(Transaction.amount).label("max_amount"),
            func.sum(Transaction.amount).label("total_amount")
        ).filter(Transaction.input_by_user_id == user_id).first()

        vendor_sql = text("""
            SELECT v.id, v.vendor_name, COUNT(t.id) AS transaction_count, SUM(t.amount) AS total_amount
            FROM transactions t
            LEFT JOIN vendors v ON t.vendor_id = v.id
            WHERE t.input_by_user_id = :user_id
            GROUP BY v.id, v.vendor_name
            ORDER BY transaction_count DESC, total_amount DESC
            LIMIT 5
        """)
        vendor_rows = db.execute(vendor_sql, {"user_id": user_id}).fetchall()

        category_sql = text("""
            SELECT category, COUNT(*) AS transaction_count, AVG(amount) AS mean_amount, SUM(amount) AS total_amount
            FROM transactions
            WHERE input_by_user_id = :user_id
            GROUP BY category
            ORDER BY total_amount DESC
            LIMIT 5
        """)
        category_rows = db.execute(category_sql, {"user_id": user_id}).fetchall()

        return {
            "user_id": user_id,
            "email": user.email,
            "fullname": user.fullname,
            "role": role_of(user.is_admin),
            "total_transactions": int(stats.total_transactions or 0),
            "mean_amount": round(float(stats.mean_amount or 0), 2),
            "max_amount": round(float(stats.max_amount or 0), 2),
            "total_amount": round(float(stats.total_amount or 0), 2),
            "top_vendors": [
                {
                    "vendor_id": row[0],
                    "vendor_name": row[1],
                    "transaction_count": int(row[2]),
                    "total_amount": float(row[3] or 0)
                }
                for row in vendor_rows
            ],
            "top_categories": [
                {
                    "category": row[0],
                    "transaction_count": int(row[1]),
                    "mean_amount": round(float(row[2] or 0), 2),
                    "total_amount": round(float(row[3] or 0), 2)
                }
                for row in category_rows
            ]
        }
    finally:
        db.close()

def get_monthly_expense_trend(months: int = 6) -> Dict[str, Any]:
    """
    Mengambil tren total expense bulanan dari tabel transaksi.
    Berguna untuk membandingkan lonjakan sebuah transaksi dengan tren biaya aktual.
    """
    db = SessionLocal()
    try:
        # Bulan dikelompokkan dalam WIB agar transaksi larut malam tidak jatuh ke
        # bulan sebelumnya seperti kalau dikelompokkan dalam UTC.
        sql = text("""
            SELECT
                DATE_TRUNC('month', t.created_at AT TIME ZONE 'Asia/Jakarta') AS month,
                COUNT(*) AS transaction_count,
                SUM(t.amount) AS total_expense,
                AVG(t.amount) AS mean_expense
            FROM transactions t
            WHERE t.type = 'expense'
              AND t.created_at >= (
                  SELECT MAX(created_at) FROM transactions
              ) - (:months || ' months')::interval
            GROUP BY DATE_TRUNC('month', t.created_at AT TIME ZONE 'Asia/Jakarta')
            ORDER BY month
        """)
        rows = db.execute(sql, {"months": months}).fetchall()
        trend: List[Dict[str, Any]] = [
            {
                "month": str(row[0].date()) if row[0] else None,
                "transaction_count": int(row[1]),
                "total_expense": round(float(row[2] or 0), 2),
                "mean_expense": round(float(row[3] or 0), 2)
            }
            for row in rows
        ]

        if len(trend) < 2:
            trend_status = "insufficient_data"
            growth_percentage = 0.0
        else:
            previous = trend[-2]["total_expense"]
            current = trend[-1]["total_expense"]
            growth_percentage = 0.0 if previous == 0 else ((current - previous) / previous) * 100
            if growth_percentage > 25:
                trend_status = "expense_increasing"
            elif growth_percentage < -25:
                trend_status = "expense_decreasing"
            else:
                trend_status = "expense_stable"

        return {
            "months_requested": months,
            "growth_percentage": round(growth_percentage, 2),
            "trend_status": trend_status,
            "trend": trend
        }
    finally:
        db.close()

def get_transaction_details(transaction_id: int) -> dict:
    """
    Mengambil detail sebuah transaksi beserta vendor dan siapa yang menginputnya.
    Gunakan ini pertama kali untuk mengetahui konteks transaksi!
    """
    db = SessionLocal()
    try:
        sql = text("""
            SELECT t.id,
                   t.created_at AT TIME ZONE 'Asia/Jakarta' AS recorded_wib,
                   t.amount, t.type, t.category, t.description, t.invoice_no,
                   t.vendor_id, v.vendor_name, v.status AS vendor_status,
                   t.input_by_user_id, u.fullname, u.is_admin
            FROM transactions t
            LEFT JOIN vendors v ON t.vendor_id = v.id
            LEFT JOIN users u ON t.input_by_user_id = u.id
            WHERE t.id = :tid
        """)
        result = db.execute(sql, {"tid": transaction_id}).fetchone()
        if not result:
            return {"error": "Transaction not found"}

        return {
            "transaction_id": result[0],
            # SATU-SATUNYA waktu di sistem ini: saat transaksi tercatat dari API
            # bank. Dipakai untuk segalanya -- jam kerja, jendela duplikat,
            # jendela split payment, dan pengelompokan bulan.
            "recorded_at": str(result[1]),
            "timezone": "Asia/Jakarta",
            "amount": float(result[2]),
            "type": result[3],
            "category": result[4],
            "description": result[5],
            "invoice_no": result[6],
            "vendor_id": result[7],
            "vendor_name": result[8],
            "vendor_status": result[9],
            "input_by_user_id": result[10],
            "input_by_user": result[11],
            "input_by_role": role_of(result[12]),
        }
    finally:
        db.close()
