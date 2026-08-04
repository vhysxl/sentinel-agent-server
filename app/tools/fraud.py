from typing import Dict, Any, List

# TODO: Integrate with actual SQLAlchemy session later

def find_duplicate_expenses(amount: float, vendor_id: int, date: str) -> List[Dict[str, Any]]:
    """
    Mencari transaksi dengan nominal yang persis sama pada waktu yang berdekatan (< 24 jam).
    Digunakan untuk mendeteksi indikasi 'Split Payment' atau tagihan ganda.
    """
    return [
        {"transaction_id": 101, "amount": amount, "date": "2026-08-03T10:00:00"},
        {"transaction_id": 102, "amount": amount, "date": "2026-08-03T10:15:00", "notes": "Possible duplicate"}
    ]

def get_vendor_history(vendor_id: int) -> Dict[str, Any]:
    """
    Mendapatkan profil dan rekam jejak vendor dari database.
    Berguna untuk mendeteksi vendor baru yang belum memiliki riwayat transaksi jelas (resiko fiktif).
    """
    return {
        "vendor_id": vendor_id,
        "vendor_name": "PT Fiktif Jaya",
        "join_date": "2026-08-01",
        "total_transactions": 2,
        "status": "new_vendor",
        "risk_flag": True
    }

def get_approval_history(transaction_id: int) -> Dict[str, Any]:
    """
    Mengecek riwayat persetujuan (approval) dari sebuah dokumen atau transaksi.
    Digunakan oleh Agent 3 untuk memverifikasi apakah transaksi anomali sudah di-acc secara sah.
    """
    return {
        "transaction_id": transaction_id,
        "approved": False,
        "approval_notes": "Bypass system via admin panel",
        "approved_by": None
    }
