def calculate_las(signals: list[dict], total_transaction_volume: float = 0) -> float:
    if not signals:
        return 0.0

    # Component 1: Signal severity (0–40)
    severity_score = 0.0
    for s in signals:
        base = {"red": 8.0, "amber": 4.0, "green": 1.0}.get(s.get("severity", ""), 0)
        weight = s.get("confidence_weight", 0.5)
        severity_score += base * weight
    severity_score = min(severity_score, 40.0)

    # Component 2: Timing & Urgency (0–25) — cash flow signals indicate pre-sale manipulation
    cash_flow_count = sum(1 for s in signals if s.get("signal_type") == "cash_flow")
    timing_score = min(cash_flow_count * 5.0, 25.0)

    # Component 3: Financial Gap (0–20) — flagged amount vs total volume
    flagged_total = sum(abs(s.get("amount", 0)) for s in signals)
    financial_score = 0.0
    if total_transaction_volume > 0:
        ratio = flagged_total / total_transaction_volume
        financial_score = min(ratio * 40, 20.0)

    # Component 4: Recovery Potential (0–15) — crypto signals indicate hard-to-recover assets
    crypto_count = sum(1 for s in signals if s.get("signal_type") == "digital_asset")
    recovery_score = min(crypto_count * 5.0, 15.0)

    return round(severity_score + timing_score + financial_score + recovery_score, 1)


def severity_band(score: float) -> str:
    if score < 20:
        return "clear"
    if score < 45:
        return "elevated"
    return "refer"
