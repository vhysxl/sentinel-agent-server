from typing import Dict, Any

# TODO: Integrate with actual SQLAlchemy session later

def calculate_z_score(transaction_id: int) -> Dict[str, Any]:
    """
    Menghitung Z-score dari sebuah transaksi berdasarkan histori pengeluaran vendor/kategori.
    Berguna untuk mendeteksi anomali atau lonjakan ekstrem.
    """
    # Mock return value for MVP design
    return {
        "transaction_id": transaction_id,
        "z_score": 4.5,
        "status": "anomaly",
        "message": "Transaksi ini 4.5 standar deviasi di atas rata-rata historis."
    }

def get_sales_trend(department: str, month: str) -> Dict[str, Any]:
    """
    Mendapatkan tren penjualan atau KPI departemen pada bulan tertentu.
    Digunakan untuk memverifikasi klaim bahwa pengeluaran besar dibutuhkan karena 'demand tinggi'.
    """
    return {
        "department": department,
        "month": month,
        "trend_percentage": 0.5,
        "description": "Stagnant (Kenaikan hanya 0.5%)"
    }

def get_budget_variance(department: str) -> Dict[str, Any]:
    """
    Membandingkan pengeluaran riil dengan batas budget (pagu anggaran) departemen saat ini.
    """
    return {
        "department": department,
        "budget_limit": 100000.0,
        "current_spent": 115000.0,
        "variance": -15000.0,
        "status": "overbudget"
    }
