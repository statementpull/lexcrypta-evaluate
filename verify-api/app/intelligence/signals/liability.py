"""Signal 05: Liability Signal Detection — undisclosed recurring financing obligations."""
from collections import defaultdict

OBLIGATION_KEYWORDS = [
    "REPAYMENT", "LOAN", "FINANCE", "LEASE", "RENTAL", "INSTALMENT",
    "INTEREST", "MORTGAGE", "FACILITY", "DEBT", "AMORTIS",
]


def run(transactions: list[dict]) -> list[dict]:
    results = []
    recurring: dict[str, list] = defaultdict(list)

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in OBLIGATION_KEYWORDS):
            recurring[merchant].append(t)

    for merchant, txns in recurring.items():
        if len(txns) < 2:
            continue
        total = sum(abs(t["amount"]) for t in txns)
        results.append({
            "signal_type": "liability",
            "severity": "amber",
            "merchant": merchant,
            "amount": -total,
            "transaction_date": txns[0].get("transaction_date", ""),
            "description": (
                f"Recurring payment to '{merchant}': {len(txns)} transactions totalling ${total:,.0f}. "
                "Pattern consistent with undisclosed financing obligation. "
                "Cross-reference against disclosed debt schedule."
            ),
            "library_match": None,
            "confidence_weight": 0.70,
        })

    return results
