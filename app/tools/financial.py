from datetime import time
from typing import Dict, Any, List
from sqlalchemy import func, text
from app.db.session import SessionLocal
from app.db.models import Transaction, User, Vendor

def calculate_z_score(transaction_id: int) -> Dict[str, Any]:
    """
    Menghitung Z-score dari sebuah transaksi berdasarkan histori pengeluaran vendor/kategori.
    Berguna untuk mendeteksi anomali atau lonjakan ekstrem.
    """
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            return {"error": f"Transaction {transaction_id} not found."}

        stats = db.query(
            func.avg(Transaction.amount).label('mean'),
            func.stddev(Transaction.amount).label('stddev')
        ).filter(
            Transaction.vendor_id == txn.vendor_id,
            Transaction.type == 'expense',
            Transaction.id != transaction_id
        ).first()

        if not stats.mean or not stats.stddev:
             return {
                 "transaction_id": transaction_id,
                 "z_score": 0.0,
                 "status": "insufficient_data",
                 "message": "Tidak cukup data historis untuk menghitung Z-score."
             }
        
        mean_val = float(stats.mean)
        stddev_val = float(stats.stddev)
        amount_val = float(txn.amount)

        z_score = 0.0 if stddev_val == 0 else (amount_val - mean_val) / stddev_val
        status = "anomaly" if z_score > 3.0 else "normal"
        
        return {
            "transaction_id": transaction_id,
            "z_score": round(z_score, 2),
            "status": status,
            "message": f"Transaksi ini {round(z_score, 2)} standar deviasi dari rata-rata historis (Rata-rata: {mean_val:,.2f}, Transaksi: {amount_val:,.2f})."
        }
    finally:
        db.close()

def get_sales_trend(department: str, month: str) -> Dict[str, Any]:
    """
    Mendapatkan tren penjualan atau KPI departemen pada bulan tertentu.
    Digunakan untuk memverifikasi klaim bahwa pengeluaran besar dibutuhkan karena 'demand tinggi'.
    """
    # Mock data for MVP, assuming it would query an external ERP/CRM system
    return {
        "department": department,
        "month": month,
        "trend_percentage": 0.5,
        "description": "Stagnant (Kenaikan hanya 0.5%)"
    }

def compare_category_baseline(transaction_id: int) -> Dict[str, Any]:
    """
    Membandingkan nilai transaksi terhadap baseline kategori dan departemen yang sama.
    Berguna untuk melihat apakah transaksi besar masih wajar di konteks kategorinya.
    """
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            return {"error": f"Transaction {transaction_id} not found."}

        user = db.query(User).filter(User.id == txn.input_by_user_id).first()
        if not user:
            return {"error": f"User for transaction {transaction_id} not found."}

        stats_sql = text("""
            SELECT
                COUNT(*) AS sample_size,
                AVG(t.amount) AS mean_amount,
                COALESCE(NULLIF(STDDEV(t.amount), 0), 0) AS stddev_amount,
                MIN(t.amount) AS min_amount,
                MAX(t.amount) AS max_amount
            FROM transactions t
            JOIN users u ON t.input_by_user_id = u.id
            WHERE t.type = :txn_type
              AND t.category = :category
              AND u.department = :department
              AND t.id != :transaction_id
        """)
        result = db.execute(stats_sql, {
            "txn_type": txn.type,
            "category": txn.category,
            "department": user.department,
            "transaction_id": transaction_id
        }).fetchone()

        sample_size = int(result[0] or 0)
        if sample_size == 0 or result[1] is None:
            return {
                "transaction_id": transaction_id,
                "category": txn.category,
                "department": user.department,
                "status": "insufficient_data",
                "message": "Tidak ada baseline kategori/departemen yang cukup untuk pembanding."
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
            "department": user.department,
            "sample_size": sample_size,
            "transaction_amount": amount,
            "category_department_mean": round(mean_amount, 2),
            "category_department_stddev": round(stddev_amount, 2),
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
            func.min(Transaction.transaction_date).label("first_transaction_date"),
            func.max(Transaction.transaction_date).label("last_transaction_date")
        ).filter(Transaction.vendor_id == vendor_id).first()

        total_transactions = int(stats.total_transactions or 0)
        status = "new_vendor" if total_transactions < 3 else "established_vendor"

        recent_sql = text("""
            SELECT id, transaction_date, amount, type, category, description
            FROM transactions
            WHERE vendor_id = :vendor_id
            ORDER BY transaction_date DESC
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
                    "transaction_date": str(row[1]),
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
    Mengecek apakah transaksi dibuat pada waktu yang tidak lazim seperti akhir pekan atau luar jam kerja.
    """
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            return {"error": f"Transaction {transaction_id} not found."}

        txn_time = txn.transaction_date.time()
        is_weekend = txn.transaction_date.weekday() >= 5
        is_after_hours = txn_time < time(8, 0) or txn_time > time(18, 0)
        is_midnight_window = txn_time < time(5, 0)

        flags = []
        if is_weekend:
            flags.append("weekend")
        if is_after_hours:
            flags.append("outside_business_hours")
        if is_midnight_window:
            flags.append("midnight_window")

        return {
            "transaction_id": transaction_id,
            "transaction_date": str(txn.transaction_date),
            "weekday": txn.transaction_date.strftime("%A"),
            "time": txn_time.strftime("%H:%M:%S"),
            "is_weekend": is_weekend,
            "is_after_hours": is_after_hours,
            "is_midnight_window": is_midnight_window,
            "flags": flags,
            "status": "unusual_timing" if flags else "normal_timing"
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
            "username": user.username,
            "role": user.role,
            "department": user.department,
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

def get_monthly_expense_trend(department: str, months: int = 6) -> Dict[str, Any]:
    """
    Mengambil tren expense bulanan departemen dari tabel transaksi.
    Berguna untuk membandingkan lonjakan transaksi dengan tren biaya aktual, bukan hanya sales trend mock.
    """
    db = SessionLocal()
    try:
        sql = text("""
            SELECT
                DATE_TRUNC('month', t.transaction_date) AS month,
                COUNT(*) AS transaction_count,
                SUM(t.amount) AS total_expense,
                AVG(t.amount) AS mean_expense
            FROM transactions t
            JOIN users u ON t.input_by_user_id = u.id
            WHERE t.type = 'expense'
              AND u.department = :department
              AND t.transaction_date >= (
                  SELECT MAX(transaction_date) FROM transactions
              ) - (:months || ' months')::interval
            GROUP BY DATE_TRUNC('month', t.transaction_date)
            ORDER BY month
        """)
        rows = db.execute(sql, {"department": department, "months": months}).fetchall()
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
            "department": department,
            "months_requested": months,
            "growth_percentage": round(growth_percentage, 2),
            "trend_status": trend_status,
            "trend": trend
        }
    finally:
        db.close()

def get_transaction_details(transaction_id: int) -> dict:
    """
    Mengambil detail sebuah transaksi (termasuk informasi departemen pembuatnya).
    Gunakan ini pertama kali untuk mengetahui konteks transaksi!
    """
    db = SessionLocal()
    try:
        sql = text("""
            SELECT t.id, t.transaction_date, t.amount, t.category, t.description,
                   t.vendor_id, t.input_by_user_id, v.vendor_name, u.department, u.username
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
            "transaction_date": str(result[1]),
            "amount": float(result[2]),
            "category": result[3],
            "description": result[4],
            "vendor_id": result[5],
            "input_by_user_id": result[6],
            "vendor_name": result[7],
            "department": result[8],
            "input_by_user": result[9]
        }
    finally:
        db.close()
