"""Signal 06: Behavioural Forensics — velocity, structuring, unusual outflows."""
from collections import defaultdict


def run(transactions: list[dict]) -> list[dict]:
    results = []

    # High-velocity low-value payments (smurfing pattern)
    vendor_counts: dict[str, list] = defaultdict(list)
    for t in transactions:
        if t["amount"] < 0:
            vendor_counts[t["merchant"]].append(t)

    for merchant, txns in vendor_counts.items():
        if len(txns) < 5:
            continue
        amounts = [abs(t["amount"]) for t in txns]
        avg = sum(amounts) / len(amounts)
        total = sum(amounts)
        if avg < 10000 and total > 50000:
            results.append({
                "signal_type": "behavioural",
                "severity": "amber",
                "merchant": merchant,
                "amount": -total,
                "transaction_date": txns[0].get("transaction_date", ""),
                "description": (
                    f"High-velocity payments to '{merchant}': {len(txns)} transactions "
                    f"averaging ${avg:,.0f} (total ${total:,.0f}). "
                    "Pattern consistent with structuring to avoid detection thresholds."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # Single very large unexplained outflow
    for t in transactions:
        if t["amount"] < -100000:
            results.append({
                "signal_type": "behavioural",
                "severity": "red",
                "merchant": t["merchant"],
                "amount": t["amount"],
                "transaction_date": t.get("transaction_date", ""),
                "description": (
                    f"Single outflow of ${abs(t['amount']):,.0f} to '{t['merchant']}'. "
                    "Material single transaction — obtain supporting documentation and verify recipient."
                ),
                "library_match": None,
                "confidence_weight": 0.75,
            })

    return results
