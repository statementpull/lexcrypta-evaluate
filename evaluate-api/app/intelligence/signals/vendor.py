"""Signal 03: Vendor Intelligence — payments to unregistered or shell vendors."""

SHELL_INDICATORS = [
    "PTY LTD", "LLC", "HOLDINGS", "TRUST", "SERVICES", "CONSULTING",
    "SOLUTIONS", "MANAGEMENT", "ADVISORY", "GROUP",
]


def run(transactions: list[dict], disclosed_vendors: list[str] | None = None) -> list[dict]:
    results = []
    disclosed = {v.upper() for v in (disclosed_vendors or [])}

    for t in transactions:
        if t["amount"] >= 0:
            continue
        amt = abs(t["amount"])
        if amt < 5000:
            continue
        merchant = t["merchant"].upper()
        is_disclosed = merchant in disclosed
        has_shell_indicator = any(ind in merchant for ind in SHELL_INDICATORS)

        if not is_disclosed and has_shell_indicator and amt >= 10000:
            results.append({
                "signal_type": "vendor",
                "severity": "red" if amt >= 50000 else "amber",
                "merchant": merchant,
                "amount": t["amount"],
                "transaction_date": t.get("transaction_date", ""),
                "description": (
                    f"Payment of ${amt:,.0f} to '{merchant}' — entity not in disclosed vendor register. "
                    "Corporate suffix suggests registered entity. "
                    "Verify registration and relationship to directors."
                ),
                "library_match": None,
                "confidence_weight": 0.75,
            })

    return results
