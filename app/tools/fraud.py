from typing import Dict, Any, List
from sqlalchemy import text
from app.db.session import SessionLocal
from app.db.models import Transaction, Vendor

def find_duplicate_expenses(amount: float, vendor_id: int, date: str) -> List[Dict[str, Any]]:
    """
    Mencari transaksi ke vendor yang sama dengan nominal persis sama dalam
    rentang +/- 24 jam. Parameter `date` diinterpretasikan sebagai waktu WIB.
    """
    db = SessionLocal()
    try:
        # Dua hal yang harus eksplisit soal zona waktu:
        # 1. `date` datang sebagai waktu WIB naif. Tanpa AT TIME ZONE, Postgres
        #    menafsirkannya memakai timezone sesi (GMT di server ini), sehingga
        #    batas jendela meleset 7 jam.
        # 2. created_at dikembalikan dalam WIB, bukan UTC mentah — kalau
        #    tidak, LLM akan menarasikan jam yang salah.
        sql = text("""
            WITH anchor AS (
                SELECT CAST(:date AS TIMESTAMP) AT TIME ZONE 'Asia/Jakarta' AS ts
            )
            SELECT t.id, t.amount,
                   to_char(t.created_at AT TIME ZONE 'Asia/Jakarta',
                           'YYYY-MM-DD HH24:MI:SS') AS date_wib,
                   t.description, t.invoice_no
            FROM transactions t, anchor a
            WHERE t.vendor_id = :vendor_id
              AND t.amount = :amount
              AND t.type = 'expense'
              AND t.created_at BETWEEN a.ts - INTERVAL '24 hours'
                                         AND a.ts + INTERVAL '24 hours'
            ORDER BY t.created_at ASC
        """)
        rows = db.execute(sql, {"vendor_id": vendor_id, "amount": amount, "date": date}).fetchall()

        return [
            {
                "transaction_id": row[0],
                "amount": float(row[1]),
                "date": row[2],
                "timezone": "Asia/Jakarta",
                "description": row[3],
                "invoice_no": row[4],
            }
            for row in rows
        ]
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


