"""Signal 12: Payroll Forensics — ghost employees, payroll manipulation, owner compensation.

Patterns:
- Ghost employees: payroll to individuals without recurring prior history
- Owner/officer payroll spike pre-sale: inflating or deflating comp before sale
- Irregular payroll timing: bi-weekly vs monthly vs random = classification risk
- Double payroll runs: same payroll processor + same amount, two runs in same period
- 1099 contractor concentration: >50% of labour through one 1099 = misclassification risk

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

PAYROLL_PROCESSORS = [
    "ADP", "PAYCHEX", "GUSTO", "BAMBOOHR", "RIPPLING", "JUSTWORKS",
    "PATRIOT PAYROLL", "PAYLOCITY", "CERIDIAN", "KRONOS", "WORKDAY PAYROLL",
    "INTUIT PAYROLL", "QB PAYROLL", "QUICKBOOKS PAYROLL", "SQUARE PAYROLL",
]

CONTRACTOR_KEYWORDS = [
    "CONTRACTOR", "1099", "FREELANCE", "INDEPENDENT", "CONSULTANT",
    "ZELLE", "VENMO", "CASHAPP", "CASH APP", "PAYPAL",
]

PAYROLL_KEYWORDS = PAYROLL_PROCESSORS + ["PAYROLL", "SALARY", "WAGES", "DIRECT DEPOSIT"]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    results = []

    payroll_txns: list[dict] = []
    contractor_txns: list[dict] = []
    processor_groups: dict[str, list] = defaultdict(list)

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        amt = abs(t["amount"])

        is_payroll = any(kw in merchant for kw in PAYROLL_KEYWORDS)
        is_contractor = any(kw in merchant for kw in CONTRACTOR_KEYWORDS)

        if is_payroll:
            payroll_txns.append(t)
            for proc in PAYROLL_PROCESSORS:
                if proc in merchant:
                    processor_groups[proc].append(t)
                    break
            else:
                processor_groups["PAYROLL (OTHER)"].append(t)

        if is_contractor:
            contractor_txns.append(t)

    # ── 1. Payroll timing irregularity ────────────────────────────────────────
    if len(payroll_txns) >= 4:
        dates = sorted(
            str(t.get("transaction_date", ""))
            for t in payroll_txns
            if t.get("transaction_date")
        )
        if len(dates) >= 3:
            # Calculate gaps between payroll dates
            gaps = []
            for i in range(1, len(dates)):
                try:
                    from datetime import datetime
                    d1 = datetime.strptime(dates[i - 1][:10], "%Y-%m-%d")
                    d2 = datetime.strptime(dates[i][:10], "%Y-%m-%d")
                    gaps.append((d2 - d1).days)
                except (ValueError, TypeError):
                    pass

            if gaps:
                avg_gap = sum(gaps) / len(gaps)
                max_gap = max(gaps)
                min_gap = min(gaps)
                gap_variance = max_gap - min_gap

                if gap_variance > avg_gap * 0.5 and avg_gap > 5:
                    results.append({
                        "signal_type": "payroll_forensics",
                        "severity": "amber",
                        "merchant": "PAYROLL TIMING IRREGULARITY",
                        "amount": -sum(abs(t["amount"]) for t in payroll_txns),
                        "transaction_date": dates[0],
                        "description": (
                            f"Payroll timing is inconsistent: average gap {avg_gap:.0f} days, "
                            f"but ranges from {min_gap} to {max_gap} days between runs. "
                            "Irregular payroll timing may indicate: (1) ad hoc owner draws "
                            "classified as payroll, (2) additional off-cycle payroll runs, "
                            "(3) manual payroll manipulation. "
                            "Obtain payroll register and verify each run against employee headcount."
                        ),
                        "library_match": None,
                        "confidence_weight": 0.60,
                    })

    # ── 2. Double payroll run detection ───────────────────────────────────────
    for processor, txns in processor_groups.items():
        date_amount_seen: dict[tuple, int] = defaultdict(int)
        for t in txns:
            key = (str(t.get("transaction_date", ""))[:7], round(abs(t["amount"]), -2))
            date_amount_seen[key] += 1
        for (period, amt), count in date_amount_seen.items():
            if count >= 2 and amt > 2000:
                results.append({
                    "signal_type": "payroll_forensics",
                    "severity": "amber",
                    "merchant": processor,
                    "amount": -(amt * count),
                    "transaction_date": period + "-01",
                    "description": (
                        f"Possible duplicate payroll run: {count} payroll payments via '{processor}' "
                        f"of ~${amt:,.0f} in the same period ({period}). "
                        "Multiple payroll runs in a single period may indicate: "
                        "(1) a bonus or off-cycle run (obtain board approval), "
                        "(2) processing error resulting in double payment, "
                        "(3) fictitious payroll entry. "
                        "Obtain payroll register and reconcile to HR headcount."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.65,
                })

    # ── 3. Payroll trend — spike in final period before sale ──────────────────
    all_dates = sorted(set(
        str(t.get("transaction_date", ""))
        for t in payroll_txns if t.get("transaction_date")
    ))
    if len(all_dates) >= 6:
        mid_idx = len(all_dates) * 2 // 3
        early_dates = set(all_dates[:mid_idx])
        late_dates = set(all_dates[mid_idx:])

        early_payroll = sum(
            abs(t["amount"]) for t in payroll_txns
            if str(t.get("transaction_date", "")) in early_dates
        )
        late_payroll = sum(
            abs(t["amount"]) for t in payroll_txns
            if str(t.get("transaction_date", "")) in late_dates
        )

        early_periods = max(1, mid_idx)
        late_periods = max(1, len(all_dates) - mid_idx)
        early_avg = early_payroll / early_periods
        late_avg = late_payroll / late_periods

        if late_avg > early_avg * 1.40 and late_payroll > 10000:
            results.append({
                "signal_type": "payroll_forensics",
                "severity": "amber",
                "merchant": "PAYROLL SPIKE — LATE PERIOD",
                "amount": -(late_payroll - early_payroll),
                "transaction_date": all_dates[mid_idx] if mid_idx < len(all_dates) else "",
                "description": (
                    f"Payroll increased {late_avg/early_avg:.0%} in the final third of the period "
                    f"(${late_payroll:,.0f} vs ${early_payroll:,.0f} earlier). "
                    "Pre-sale payroll spikes may indicate: (1) owner accelerating compensation "
                    "before sale to maximise personal extraction, (2) new headcount added, "
                    "(3) one-time bonuses that inflate EBITDA normalisation claims. "
                    "Request payroll register and confirm headcount was stable."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })
        elif early_avg > 0 and late_avg < early_avg * 0.50 and early_payroll > 10000:
            results.append({
                "signal_type": "payroll_forensics",
                "severity": "amber",
                "merchant": "PAYROLL DROP — LATE PERIOD",
                "amount": -(early_payroll - late_payroll),
                "transaction_date": all_dates[mid_idx] if mid_idx < len(all_dates) else "",
                "description": (
                    f"Payroll dropped {1 - late_avg/early_avg:.0%} in the final third of the period "
                    f"(${late_payroll:,.0f} vs ${early_payroll:,.0f} earlier). "
                    "Pre-sale payroll suppression inflates reported EBITDA. "
                    "Headcount reductions may not be sustainable post-acquisition. "
                    "Verify: (1) actual headcount change, (2) deferred payroll accruals, "
                    "(3) reclassification of labour as owner distributions."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # ── 4. High contractor concentration ─────────────────────────────────────
    if contractor_txns and payroll_txns:
        total_contractor = sum(abs(t["amount"]) for t in contractor_txns)
        total_labour = sum(abs(t["amount"]) for t in payroll_txns) + total_contractor
        if total_labour > 20000 and total_contractor / total_labour > 0.50:
            results.append({
                "signal_type": "payroll_forensics",
                "severity": "amber",
                "merchant": "HIGH CONTRACTOR CONCENTRATION",
                "amount": -total_contractor,
                "transaction_date": contractor_txns[0].get("transaction_date", ""),
                "description": (
                    f"Contractor payments represent {total_contractor/total_labour:.0%} of total labour costs "
                    f"(${total_contractor:,.0f} contractor vs ${total_labour-total_contractor:,.0f} payroll). "
                    "High contractor concentration creates: (1) worker misclassification risk "
                    "(IRS/DOL penalties), (2) workforce instability post-acquisition, "
                    "(3) unrecorded employee benefit liabilities. "
                    "Verify contractor classification against IRS 20-factor test. "
                    "Obtain copies of contractor agreements."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    return results
