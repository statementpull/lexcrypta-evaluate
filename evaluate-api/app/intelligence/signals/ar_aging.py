"""Signal 34: Accounts Receivable Aging & Collection Risk.

The gap between when revenue is declared and when cash is actually collected
is one of the most revealing metrics in acquisition due diligence. High AR
aging indicates: bad debt buildup, customer financial stress, billing disputes,
or fictitious revenue that was never collected because it never existed.

What we detect:
  DSO (Days Sales Outstanding): Revenue/365 vs cash collected — if DSO
    exceeds 60 days for a non-construction business, investigate.
  Collection deterioration: AR growing while revenue is flat = collections failing.
  Large undisclosed receivables: Declared revenue >> bank inflows = receivables
    that may be uncollectable.
  Customer payment disputes: Patterns of partial payments or payment offsets.
  Concentration in slow-paying customers: One large slow customer skews the whole
    AR picture.

Industry DSO benchmarks (Dun & Bradstreet / NACM):
  Professional services: 30–45 days
  Manufacturing: 35–50 days
  Construction: 45–75 days (progress billing)
  Healthcare: 45–90 days (insurance cycle)
  Retail/restaurant: near zero (POS)
  Government contractors: 30–60 days (Net 30–60 terms standard)

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime


def _row_amount(r: dict) -> float:
    for key in ("ytd", "amount", "value", "this_month", "balance"):
        v = r.get(key)
        if v is not None:
            try:
                val = float(re.sub(r"[,$\s%]", "", str(v)))
                if val != 0:
                    return val
            except (ValueError, TypeError):
                pass
    return 0.0


def _sum_rows(pl_rows, *keyword_lists) -> float:
    total = 0.0
    for r in pl_rows:
        account = str(r.get("account", "")).upper()
        for kws in keyword_lists:
            if any(kw in account for kw in kws):
                total += _row_amount(r)
                break
    return total


REVENUE_KW = ["REVENUE", "SALES", "NET SALES", "GROSS REVENUE", "SERVICE REVENUE"]
AR_KW = ["ACCOUNTS RECEIVABLE", "A/R", "TRADE RECEIVABLE", "DEBTORS", "RECEIVABLE"]
BAD_DEBT_KW = ["BAD DEBT", "DOUBTFUL ACCOUNT", "ALLOWANCE FOR", "WRITE-OFF", "WRITE OFF"]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not pl_rows:
        return []

    revenue = _sum_rows(pl_rows, REVENUE_KW)
    ar_balance = _sum_rows(pl_rows, AR_KW)
    bad_debt = abs(_sum_rows(pl_rows, BAD_DEBT_KW))

    if revenue <= 0:
        return []

    cash_inflows = sum(t["amount"] for t in transactions if t["amount"] > 0)

    results = []

    # ── DSO calculation ───────────────────────────────────────────────────────
    if ar_balance > 0:
        dso = (ar_balance / revenue) * 365
        collection_rate = cash_inflows / revenue if revenue > 0 else 0

        if dso > 90:
            severity = "red"
            dso_verdict = f"DSO {dso:.0f} days — CRITICAL. Receivables collection is severely delayed."
        elif dso > 60:
            severity = "amber"
            dso_verdict = f"DSO {dso:.0f} days — elevated. Investigate slow-paying customers."
        elif dso > 45:
            severity = "amber"
            dso_verdict = f"DSO {dso:.0f} days — above average for most industries."
        else:
            return []

        uncollected_est = ar_balance * (1 - min(collection_rate, 1.0)) if collection_rate < 0.85 else 0
        bad_debt_note = f" Bad debt reserve: ${bad_debt:,.0f}." if bad_debt > 0 else " No bad debt reserve identified in P&L — verify adequacy."

        results.append({
            "signal_type": "ar_aging",
            "severity": severity,
            "merchant": f"AR AGING RISK: {dso_verdict[:60]}",
            "amount": ar_balance,
            "transaction_date": "",
            "description": (
                f"{dso_verdict} "
                f"AR balance: ${ar_balance:,.0f} on revenue of ${revenue:,.0f}. "
                f"Cash collection rate: {collection_rate:.0%} of declared revenue.{bad_debt_note} "
                f"{'Estimated uncollectable AR: $' + str(f'{uncollected_est:,.0f}') + ' — adjust purchase price.' if uncollected_est > 10000 else ''} "
                "AR due diligence: (1) request aged AR schedule (current, 30, 60, 90, 90+ days), "
                "(2) identify customers over 90 days — these are likely uncollectable, "
                "(3) verify bad debt reserve is adequate (typically 2–5% of AR for healthy businesses), "
                "(4) confirm AR aging has not deteriorated in recent months vs historical, "
                "(5) check for related-party AR (owner-controlled customers who may not pay after exit). "
                "Exclude uncollectable AR from closing WC calculation."
            ),
            "library_match": "AR_AGING_RISK",
            "confidence_weight": 0.75 if dso > 90 else 0.60,
        })

    # ── Revenue vs cash gap (undisclosed AR / fictitious revenue) ─────────────
    elif revenue > 0 and cash_inflows < revenue * 0.60:
        gap = revenue - cash_inflows
        implied_dso = (gap / revenue) * 365
        results.append({
            "signal_type": "ar_aging",
            "severity": "red",
            "merchant": f"REVENUE-CASH GAP: ${gap:,.0f} of declared revenue uncollected",
            "amount": gap,
            "transaction_date": "",
            "description": (
                f"Cash inflows (${cash_inflows:,.0f}) represent only {cash_inflows/revenue:.0%} "
                f"of declared revenue (${revenue:,.0f}). "
                f"Gap: ${gap:,.0f} (implied DSO: {implied_dso:.0f} days). "
                "This gap either represents: (1) legitimate receivables outstanding at period end, "
                "or (2) revenue booked but never expected to be collected — the hallmark of "
                "channel stuffing, fictitious sales, or bill-and-hold schemes. "
                "DSRI (Days Sales Receivables Index) is the strongest single predictor in the "
                "Beneish M-Score model. Reconcile every dollar of declared revenue to either "
                "cash collected or a specific, collectable receivable with an identified debtor."
            ),
            "library_match": "AR_REVENUE_CASH_GAP",
            "confidence_weight": 0.85,
        })

    return results
