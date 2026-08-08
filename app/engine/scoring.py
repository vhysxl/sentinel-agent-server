from typing import Dict, Any, List

MAX_BASE_SCORE = 80


def _append_trigger(triggers: List[str], domain: str, message: str) -> None:
    prefix = f"{domain}: " if domain else ""
    triggers.append(f"{prefix}{message}")


def calculate_agent_score(agent_finding: Dict[str, Any], domain: str = "") -> Dict[str, Any]:
    """
    Hitung skor deterministik untuk hasil satu agent/domain.
    Agent mengumpulkan evidence, angka skor objektif dihitung dari kontrak
    metric yang stabil agar hasilnya bisa diaudit.
    """
    base_score = 0
    objective_triggers: List[str] = []

    evidence = agent_finding.get("evidence", {})
    objective_metrics = evidence.get("objective", [])

    for item in objective_metrics:
        metric = str(item.get("metric", "")).lower()
        value = item.get("value")
        status = str(item.get("status", "")).lower()
        value_text = str(value).lower()

        if metric == "z_score":
            try:
                z_val = float(value)
                if 3.0 <= z_val <= 4.0:
                    base_score += 20
                    _append_trigger(objective_triggers, domain, f"Z-Score {z_val:.2f} (+20)")
                elif 4.0 < z_val <= 5.0:
                    base_score += 30
                    _append_trigger(objective_triggers, domain, f"Z-Score {z_val:.2f} (+30)")
                elif z_val > 5.0:
                    base_score += 40
                    _append_trigger(objective_triggers, domain, f"Z-Score {z_val:.2f} (+40)")
            except (ValueError, TypeError):
                pass

        elif metric == "timing":
            if any(flag in status for flag in ["unusual", "weekend", "outside", "midnight"]):
                base_score += 20
                _append_trigger(objective_triggers, domain, f"Transaksi di luar kewajaran waktu ({value}) (+20)")

        elif metric in ["vendor_history", "vendor_status"]:
            if any(flag in value_text or flag in status for flag in ["new", "baru", "flagged", "suspended", "high_risk", "mencurigakan"]):
                base_score += 30
                _append_trigger(objective_triggers, domain, "Vendor baru/berisiko (+30)")

        elif metric == "budget_variance":
            if "over" in status or "over" in value_text:
                base_score += 10
                _append_trigger(objective_triggers, domain, "Overbudget (+10)")

        elif metric in ["duplicate_transaction", "duplicate_transactions"]:
            negative_flags = ["aman", "safe", "false", "no", "normal", "tidak"]
            positive_flags = ["yes", "true", "duplicate", "split", "confirmed", "detected", "indikasi"]
            try:
                numeric_value = int(str(value).split()[0])
            except (ValueError, TypeError, IndexError):
                numeric_value = None

            is_negative = any(flag in status for flag in negative_flags)
            is_duplicate_count = numeric_value is not None and numeric_value > 1
            is_positive_status = any(flag in status for flag in positive_flags)

            if not is_negative and (is_duplicate_count or is_positive_status):
                base_score += 40
                _append_trigger(objective_triggers, domain, "Transaksi ganda/split payment (+40)")

    base_score = min(base_score, MAX_BASE_SCORE)

    return {
        "domain": domain,
        "base_score": base_score,
        "objective_triggers": objective_triggers,
    }


def aggregate_agent_scores(agent_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Gabungkan skor objektif per-agent menjadi base risk score global.
    Untuk MVP, skor dijumlahkan dan dibatasi sesuai proposal.
    """
    base_score = min(sum(int(score.get("base_score", 0) or 0) for score in agent_scores), MAX_BASE_SCORE)
    objective_triggers: List[str] = []

    for score in agent_scores:
        objective_triggers.extend(score.get("objective_triggers", []))

    return {
        "base_risk_score": base_score,
        "objective_triggers": objective_triggers,
        "agent_scores": agent_scores,
    }


def calculate_base_score(agent_finding: Dict[str, Any]) -> Dict[str, Any]:
    """
    Backward-compatible scoring untuk payload gabungan lama.
    """
    agent_score = calculate_agent_score(agent_finding)
    base_score = agent_score["base_score"]
    objective_triggers = agent_score["objective_triggers"]
    final_risk_score = base_score

    if final_risk_score >= 80:
        risk_level = "Critical"
    elif final_risk_score >= 60:
        risk_level = "High"
    elif final_risk_score >= 40:
        risk_level = "Medium"
    else:
        risk_level = "Low"

    return {
        "base_risk_score": base_score,
        "final_risk_score": final_risk_score,
        "risk_level": risk_level,
        "objective_triggers": objective_triggers,
    }
