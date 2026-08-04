from typing import Dict, Any, List

def calculate_base_score(agent_finding: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scoring Engine deterministik yang mem-parsing output objektif 
    dari Agent LLM dan menghitung Base Risk Score.
    """
    base_score = 0
    objective_triggers = []
    
    evidence = agent_finding.get("evidence", {})
    objective_metrics = evidence.get("objective", [])
    
    for item in objective_metrics:
        metric = item.get("metric", "")
        value = item.get("value")
        status = str(item.get("status", "")).lower()
        
        # 1. Z-Score Anomaly
        if metric == "z_score":
            try:
                z_val = float(value)
                if 3.0 <= z_val <= 4.0:
                    base_score += 20
                    objective_triggers.append(f"Z-Score {z_val:.2f} (+20)")
                elif 4.1 <= z_val <= 5.0:
                    base_score += 30
                    objective_triggers.append(f"Z-Score {z_val:.2f} (+30)")
                elif z_val > 5.0:
                    base_score += 40
                    objective_triggers.append(f"Z-Score {z_val:.2f} (+40)")
            except (ValueError, TypeError):
                pass
                
        # 2. Pola Transaksi (Waktu)
        elif metric == "timing":
            if "unusual" in status or "weekend" in status or "outside" in status or "midnight" in status:
                base_score += 20
                objective_triggers.append(f"Transaksi di luar kewajaran waktu ({value}) (+20)")
                
        # 3. Kredibilitas Vendor
        elif metric == "vendor_history":
            val_str = str(value).lower()
            if "new" in val_str or "baru" in val_str:
                base_score += 30
                objective_triggers.append("Vendor Baru (Belum ada histori) (+30)")
                
        # 4. (Future) Kepatuhan Anggaran (Overbudget)
        elif metric == "budget_variance":
            if "over" in status:
                base_score += 10
                objective_triggers.append("Overbudget (+10)")
                
        # 5. (Future) Transaksi Ganda (Agent 2)
        elif metric == "duplicate_transaction":
            if "yes" in status or "true" in status:
                base_score += 40
                objective_triggers.append("Transaksi Ganda terdeteksi (+40)")

    # Maksimal Base Risk Score dibatasi ke 80 sesuai proposal
    if base_score > 80:
        base_score = 80
        
    return {
        "base_risk_score": base_score,
        "objective_triggers": objective_triggers
    }
