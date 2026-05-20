"""Signal 54: Aged Debtors Analysis — AR Quality, Concentration & Collection Risk.

Accounts receivable is cash the business hasn't collected yet. In an acquisition,
uncollected AR is often overvalued on the balance sheet. Buyers inherit:
  - Old invoices that will never be paid (bad debt)
  - Concentration risk (one customer = one risk)
  - Working capital shortfall if AR quality is worse than reported

Key ratios:
  Overdue % = (30+ days) / Total AR
    >30%: Significant collection issues — historical bad debt write-off rate needed
    >50%: Material risk — AR may be substantially overstated

  90+ day bucket % = (90+ days) / Total AR
    >20%: Severe — invoices >90 days have ~20–30% recovery probability in most industries

  Customer concentration = largest single debtor / total AR
    >40%: Dangerous concentration — one debtor default wipes substantial receivable balance

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""


def _sum_cat(rows: list[dict], *fields) -> float:
    return sum(r.get(f, 0) for r in rows for f in fields if f in r)


def run(transactions: list[dict], pl_rows: list[dict] | None = None,
        loader=None, supplementary: dict | None = None) -> list[dict]:
    ar_rows = (supplementary or {}).get("aged_debtors", [])
    if not ar_rows:
        return []

    results = []

    total_ar = sum(r.get("total", 0) for r in ar_rows)
    if total_ar <= 0:
        return []

    overdue = sum(
        r.get("days_30", 0) + r.get("days_60", 0) +
        r.get("days_90", 0) + r.get("days_90_plus", 0)
        for r in ar_rows
    )
    over_90 = sum(r.get("days_90_plus", 0) for r in ar_rows)

    overdue_pct = overdue / total_ar if total_ar else 0
    over_90_pct = over_90 / total_ar if total_ar else 0

    # ── Overdue concentration ──────────────────────────────────────────────
    if overdue_pct > 0.50:
        results.append({
            "signal_type": "aged_debtors_analysis",
            "severity": "red",
            "merchant": f"AR QUALITY RISK: {overdue_pct:.0%} of receivables are overdue",
            "amount": overdue,
            "transaction_date": "",
            "description": (
                f"Overdue AR (30+ days): ${overdue:,.0f} ({overdue_pct:.0%} of total AR ${total_ar:,.0f}). "
                f"90+ days: ${over_90:,.0f} ({over_90_pct:.0%} of total). "
                "More than half of accounts receivable is overdue — the stated AR balance is "
                "likely materially overstated in the acquisition price. "
                "Buyer risk: (1) demand a full AR aging review with historical write-off rates — "
                "the overdue portion may need to be excluded from net working capital, "
                "(2) require an AR escrow or holdback to cover bad debt, "
                "(3) verify whether overdue invoices relate to disputed work or customer disputes, "
                "(4) aged AR >90 days typically recovers at 20–30 cents on the dollar — "
                f"${over_90:,.0f} at this age represents significant write-down exposure."
            ),
            "library_match": "AR_QUALITY_RISK_SEVERE",
            "confidence_weight": 0.85,
        })
    elif overdue_pct > 0.30:
        results.append({
            "signal_type": "aged_debtors_analysis",
            "severity": "amber",
            "merchant": f"OVERDUE AR: {overdue_pct:.0%} of receivables past due",
            "amount": overdue,
            "transaction_date": "",
            "description": (
                f"Overdue AR (30+ days): ${overdue:,.0f} ({overdue_pct:.0%} of total AR ${total_ar:,.0f}). "
                f"90+ day bucket: ${over_90:,.0f} ({over_90_pct:.0%}). "
                "Elevated overdue percentage — request the seller's historical bad debt write-off rate "
                "for the past three years. "
                "Negotiate a working capital peg that reflects a haircut on overdue AR "
                "rather than accepting face value."
            ),
            "library_match": "AR_QUALITY_RISK_MODERATE",
            "confidence_weight": 0.70,
        })

    # ── 90+ day bucket ─────────────────────────────────────────────────────
    if over_90_pct > 0.20 and overdue_pct <= 0.50:
        results.append({
            "signal_type": "aged_debtors_analysis",
            "severity": "amber",
            "merchant": f"STALE RECEIVABLES: {over_90_pct:.0%} aged over 90 days",
            "amount": over_90,
            "transaction_date": "",
            "description": (
                f"AR aged >90 days: ${over_90:,.0f} ({over_90_pct:.0%} of total AR). "
                "Invoices outstanding >90 days have significantly reduced recovery probability. "
                "Verify: (1) whether these relate to specific disputes or customer financial difficulty, "
                "(2) whether the seller has made any provision for doubtful debts on the balance sheet, "
                "(3) whether any of these debtors are related parties — owner-owed balances "
                "may not be collectible by a new owner."
            ),
            "library_match": "AR_STALE_BUCKET",
            "confidence_weight": 0.70,
        })

    # ── Customer concentration ──────────────────────────────────────────────
    if ar_rows:
        largest = max(ar_rows, key=lambda r: r.get("total", 0))
        largest_amt = largest.get("total", 0)
        largest_pct = largest_amt / total_ar if total_ar else 0

        if largest_pct > 0.40:
            results.append({
                "signal_type": "aged_debtors_analysis",
                "severity": "red",
                "merchant": f"AR CONCENTRATION: {largest['customer']} = {largest_pct:.0%} of receivables",
                "amount": largest_amt,
                "transaction_date": "",
                "description": (
                    f"Single largest debtor: {largest['customer']} — "
                    f"${largest_amt:,.0f} ({largest_pct:.0%} of total AR ${total_ar:,.0f}). "
                    "Extreme AR concentration in one customer. If this customer disputes, delays, "
                    "or fails to pay, the working capital position deteriorates materially. "
                    "Verify: (1) is this customer's balance overdue? "
                    f"(overdue: ${largest.get('days_30',0)+largest.get('days_60',0)+largest.get('days_90',0)+largest.get('days_90_plus',0):,.0f}), "
                    "(2) is there a contractual dispute or ongoing negotiation with this customer? "
                    "(3) is this customer related to the seller — if so, the receivable may not survive a change of ownership, "
                    "(4) review the customer contract for assignment clauses or change-of-control provisions."
                ),
                "library_match": "AR_CUSTOMER_CONCENTRATION",
                "confidence_weight": 0.85,
            })
        elif largest_pct > 0.25:
            results.append({
                "signal_type": "aged_debtors_analysis",
                "severity": "amber",
                "merchant": f"AR CONCENTRATION: {largest['customer']} = {largest_pct:.0%} of receivables",
                "amount": largest_amt,
                "transaction_date": "",
                "description": (
                    f"Largest debtor: {largest['customer']} — "
                    f"${largest_amt:,.0f} ({largest_pct:.0%} of total AR ${total_ar:,.0f}). "
                    "Moderate AR concentration. Verify the payment history of this customer "
                    "and whether the balance is current or overdue."
                ),
                "library_match": "AR_CONCENTRATION_MODERATE",
                "confidence_weight": 0.65,
            })

    # ── Total AR size (informational context) ──────────────────────────────
    top5 = sorted(ar_rows, key=lambda r: r.get("total", 0), reverse=True)[:5]
    top5_total = sum(r.get("total", 0) for r in top5)
    top5_pct = top5_total / total_ar if total_ar else 0
    if len(ar_rows) > 5 and top5_pct > 0.80:
        results.append({
            "signal_type": "aged_debtors_analysis",
            "severity": "amber",
            "merchant": f"REVENUE CONCENTRATION (AR): Top 5 customers = {top5_pct:.0%} of AR",
            "amount": top5_total,
            "transaction_date": "",
            "description": (
                f"Top 5 debtors account for {top5_pct:.0%} of total AR (${top5_total:,.0f} / ${total_ar:,.0f}). "
                "High customer concentration in accounts receivable mirrors revenue concentration risk. "
                "If any of these customers change payment behaviour post-acquisition, "
                "working capital will be directly impacted. "
                "Verify: (1) are any of these customers also large revenue contributors "
                "where a relationship change could affect both revenue and collections simultaneously?"
            ),
            "library_match": "AR_TOP5_CONCENTRATION",
            "confidence_weight": 0.65,
        })

    return results
