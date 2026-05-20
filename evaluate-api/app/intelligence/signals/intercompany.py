"""Signal 02: Intercompany Forensics — recurring transfers to related/undisclosed entities."""
from collections import defaultdict


def run(transactions: list[dict], disclosed_entities: list[str]) -> list[dict]:
    results = []
    disclosed_upper = {e.upper() for e in disclosed_entities}

    merchant_groups: dict[str, list] = defaultdict(list)
    for t in transactions:
        if t["amount"] < 0:
            merchant_groups[t["merchant"]].append(t)

    for merchant, txns in merchant_groups.items():
        if len(txns) < 2:
            continue
        amounts = [abs(t["amount"]) for t in txns]
        total = sum(amounts)
        is_undisclosed = merchant not in disclosed_upper
        is_identical = len(set(amounts)) == 1

        if is_identical and len(txns) >= 3:
            severity = "red" if is_undisclosed else "amber"
            results.append({
                "signal_type": "intercompany",
                "severity": severity,
                "merchant": merchant,
                "amount": -total,
                "transaction_date": txns[0].get("transaction_date", ""),
                "description": (
                    f"{len(txns)} identical transfers of ${amounts[0]:,.0f} to '{merchant}' "
                    f"(total ${total:,.0f}). "
                    + (
                        "Entity not in disclosed corporate structure — undisclosed related party."
                        if is_undisclosed
                        else "Recurring pattern — verify intercompany agreement."
                    )
                ),
                "library_match": None,
                "confidence_weight": 0.85 if is_undisclosed else 0.60,
            })

    return results
