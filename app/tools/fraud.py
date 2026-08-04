from typing import Dict, Any, List
from sqlalchemy import text
from app.db.session import SessionLocal
from app.db.models import Transaction, Vendor

def find_duplicate_expenses(amount: float, vendor_id: int, date: str) -> List[Dict[str, Any]]:
    """
    Mencari transaksi dengan nominal yang persis sama pada waktu yang berdekatan (< 24 jam).
    Digunakan untuk mendeteksi indikasi 'Split Payment' atau tagihan ganda.
    """
    db = SessionLocal()
    try:
        sql = text("""
            SELECT id, amount, transaction_date, description
            FROM transactions
            WHERE vendor_id = :vendor_id
              AND amount = :amount
              AND transaction_date >= :date::timestamp - INTERVAL '24 hours'
              AND transaction_date <= :date::timestamp + INTERVAL '24 hours'
            ORDER BY transaction_date ASC
        """)
        rows = db.execute(sql, {"vendor_id": vendor_id, "amount": amount, "date": date}).fetchall()
        
        results = []
        for row in rows:
            results.append({
                "transaction_id": row[0],
                "amount": float(row[1]),
                "date": str(row[2]),
                "description": row[3]
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        db.close()

def get_vendor_history(vendor_id: int) -> Dict[str, Any]:
    """
    Mendapatkan profil dan rekam jejak vendor dari database.
    Berguna untuk mendeteksi vendor baru yang belum memiliki riwayat transaksi jelas (resiko fiktif).
    """
    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            return {"error": f"Vendor {vendor_id} not found."}
            
        stats_sql = text("""
            SELECT COUNT(*)
            FROM transactions
            WHERE vendor_id = :vendor_id
        """)
        total_tx = db.execute(stats_sql, {"vendor_id": vendor_id}).scalar() or 0
        
        status = "new_vendor" if total_tx < 3 else "established_vendor"
        risk_flag = True if total_tx < 3 else False
        
        return {
            "vendor_id": vendor_id,
            "vendor_name": vendor.vendor_name,
            "join_date": str(vendor.join_date),
            "total_transactions": total_tx,
            "status": status,
            "risk_flag": risk_flag
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


