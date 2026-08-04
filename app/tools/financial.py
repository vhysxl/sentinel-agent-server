from typing import Dict, Any
from sqlalchemy import func, text
from app.db.session import SessionLocal
from app.db.models import Transaction, User

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

def get_transaction_details(transaction_id: int) -> dict:
    """
    Mengambil detail sebuah transaksi (termasuk informasi departemen pembuatnya).
    Gunakan ini pertama kali untuk mengetahui konteks transaksi!
    """
    db = SessionLocal()
    try:
        sql = text("""
            SELECT t.id, t.transaction_date, t.amount, t.category, t.description, 
                   v.vendor_name, u.department, u.username
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
            "vendor_name": result[5],
            "department": result[6],
            "input_by_user": result[7]
        }
    finally:
        db.close()
