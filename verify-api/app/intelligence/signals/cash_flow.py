"""Signal 01: Cash Flow Integrity — Q4 concentration, round-number structuring."""
import re
from collections import defaultdict


def run(transactions: list[dict], pl_rows: list[dict]) -> list[dict]:
    results = []

    # Q4 revenue concentration check
    monthly_credits = defaultdict(float)
    for t in transactions:
        if t["amount"] > 0:
            month = _extract_month(t.get("transaction_date", ""))
            if month:
                monthly_credits[month] += t["amount"]

    if monthly_credits:
        total = sum(monthly_credits.values())
        q4_months = {10, 11, 12}
        q4_total = sum(v for k, v in monthly_credits.items() if k in q4_months)
        if total > 0:
            q4_pct = q4_total / total
            if q4_pct > 0.50:
                results.append({
                    "signal_type": "cash_flow",
                    "severity": "amber",
                    "merchant": "REVENUE PATTERN",
                    "amount": q4_total,
                    "transaction_date": "",
                    "description": (
                        f"Q4 revenue concentration: {q4_pct:.0%} of annual credits received Oct–Dec. "
                        "Inconsistent with even revenue distribution — investigate customer contract terms."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.70,
                })

    # Round-number debits >= $9,000 (structuring indicator)
    for t in transactions:
        if t["amount"] < 0:
            amt = abs(t["amount"])
            if amt >= 9000 and amt % 1000 == 0:
                results.append({
                    "signal_type": "cash_flow",
                    "severity": "amber",
                    "merchant": t["merchant"],
                    "amount": t["amount"],
                    "transaction_date": t.get("transaction_date", ""),
                    "description": (
                        f"Round-number debit of ${amt:,.0f} — potential structuring pattern. "
                        "Verify against disclosed expense schedule."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.60,
                })

    return results


def _extract_month(date_str: str):
    m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", date_str)
    if not m:
        return None
    _day, month, _year = m.groups()
    try:
        return int(month)
    except ValueError:
        return None
