"""Signal 50: Subscription Revenue Churn & Refund Rate Analysis.

For businesses with recurring revenue, bank data reveals the true churn
and refund picture that CRM systems and reported MRR often mask.

Payment processors pass settlement funds with chargebacks and refunds
netted out — but close analysis of the transaction stream reveals gross
revenue, refund rates, and subscription decay patterns.

Key metrics:
  Gross refund rate: Refunds / Gross revenue from processor settlements.
    >15% of gross revenue returned is a product quality or customer
    satisfaction problem. >30% is catastrophic — not a viable business.
  Subscription decay: If recurring processor settlements are declining
    month-over-month, the business is losing subscribers faster than
    it is acquiring them.
  Churn signal: Recurring payees (by merchant) that appear consistently
    then stop — each represents a lost subscription customer.
  Processor concentration: If >80% of revenue flows through a single
    payment processor, a processor dispute, fee increase, or account
    termination is an existential risk.
  Pre-sale refund acceleration: If refund rates spike in the 3 months
    before sale, it may indicate a product quality issue or aggressive
    churn by customers aware of or reacting to a transition.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime

PROCESSOR_SETTLEMENT_KW = [
    "STRIPE", "SQUARE", "PAYPAL", "SHOPIFY PAYMENTS", "BRAINTREE",
    "AUTHORIZE.NET", "WORLDPAY", "HEARTLAND PAYMENT", "FIRST DATA",
    "CLOVER", "TOAST POS", "ZETTLE", "SUMUP",
    "PROCESSOR SETTLEMENT", "MERCHANT SETTLEMENT", "CARD SETTLEMENT",
    "PAYMENT SETTLEMENT", "SALES DEPOSIT",
]

REFUND_KW = [
    "REFUND", "CHARGEBACK", "REVERSAL", "DISPUTE",
    "RETURN PAYMENT", "CREDIT BACK", "CUSTOMER REFUND",
    "REFUND DISBURSEMENT", "CHARGEBACK ADJUSTMENT",
]

SUBSCRIPTION_KW = [
    "SUBSCRIPTION", "RECURRING", "MEMBERSHIP", "MONTHLY FEE",
    "ANNUAL MEMBERSHIP", "LICENSE FEE", "SAAS",
]


def _parse_date(d):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt)
        except:
            pass
    return None


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    refund_txns = []
    settlement_txns = []

    for t in transactions:
        m = t["merchant"].upper()
        amt = t["amount"]
        if amt < 0 and any(kw in m for kw in REFUND_KW):
            refund_txns.append(t)
        if amt > 0 and any(kw in m for kw in PROCESSOR_SETTLEMENT_KW):
            settlement_txns.append(t)

    if not settlement_txns and not refund_txns:
        return []

    gross_settlements = sum(t["amount"] for t in settlement_txns)
    gross_refunds = sum(abs(t["amount"]) for t in refund_txns)

    # ── Gross refund rate ────────────────────────────────────────────────
    if gross_settlements > 0 and gross_refunds > 0:
        refund_rate = gross_refunds / (gross_settlements + gross_refunds)
        if refund_rate > 0.15:
            severity = "red" if refund_rate > 0.25 else "amber"
            results.append({
                "signal_type": "subscription_churn",
                "severity": severity,
                "merchant": f"REFUND RATE: {refund_rate:.1%} of gross revenue returned",
                "amount": -gross_refunds,
                "transaction_date": refund_txns[0].get("transaction_date", "") if refund_txns else "",
                "description": (
                    f"Gross refunds and chargebacks: ${gross_refunds:,.0f} = {refund_rate:.1%} "
                    f"of gross processor revenue (${gross_settlements:,.0f}). "
                    f"{'CRITICAL: ' if refund_rate > 0.25 else ''}"
                    "A refund rate above 15% indicates a systemic issue — product quality, "
                    "customer satisfaction, or misleading marketing. Payment processors "
                    "(Stripe, Square, PayPal) will suspend accounts if chargebacks exceed "
                    "1% of transaction volume — verify current standing with each processor. "
                    "For subscription businesses, high refund rates signal a churn problem "
                    "that will accelerate post-acquisition if not addressed. "
                    "Request full chargeback history and reason codes from each processor."
                ),
                "library_match": "CHURN_REFUND_RATE",
                "confidence_weight": 0.80,
            })

    # ── Pre-sale refund acceleration ────────────────────────────────────
    if refund_txns:
        monthly_refunds: dict[str, float] = defaultdict(float)
        monthly_settle: dict[str, float] = defaultdict(float)
        for t in refund_txns:
            d = _parse_date(t.get("transaction_date", ""))
            if d:
                monthly_refunds[f"{d.year}-{d.month:02d}"] += abs(t["amount"])
        for t in settlement_txns:
            d = _parse_date(t.get("transaction_date", ""))
            if d:
                monthly_settle[f"{d.year}-{d.month:02d}"] += t["amount"]

        if len(monthly_refunds) >= 4:
            r_months = sorted(monthly_refunds.keys())
            r_vals = [monthly_refunds[m] for m in r_months]
            prior_avg = sum(r_vals[:-3]) / max(len(r_vals) - 3, 1)
            recent_avg = sum(r_vals[-3:]) / 3
            if prior_avg > 0 and recent_avg > prior_avg * 2.0:
                results.append({
                    "signal_type": "subscription_churn",
                    "severity": "red",
                    "merchant": f"REFUND ACCELERATION: {recent_avg/prior_avg:.1f}x spike pre-sale",
                    "amount": -(recent_avg - prior_avg) * 3,
                    "transaction_date": r_months[-1],
                    "description": (
                        f"Refund/chargeback volumes in the last 3 months "
                        f"(${recent_avg:,.0f}/month avg) are {recent_avg/prior_avg:.1f}x "
                        f"the prior period average (${prior_avg:,.0f}/month). "
                        "Accelerating refunds immediately before a sale indicate either: "
                        "(1) a deteriorating product or service experience, "
                        "(2) customers leaving en masse in anticipation of ownership change, or "
                        "(3) a billing dispute or policy change triggering mass refund requests. "
                        "Investigate root cause before pricing the deal — these refunds "
                        "are a forward indicator of revenue decline, not a one-time event."
                    ),
                    "library_match": "CHURN_REFUND_ACCELERATION",
                    "confidence_weight": 0.80,
                })

    # ── Processor settlement trend (churn/growth) ────────────────────────
    if settlement_txns:
        monthly_s: dict[str, float] = defaultdict(float)
        for t in settlement_txns:
            d = _parse_date(t.get("transaction_date", ""))
            if d:
                monthly_s[f"{d.year}-{d.month:02d}"] += t["amount"]

        if len(monthly_s) >= 6:
            s_months = sorted(monthly_s.keys())
            s_vals = [monthly_s[m] for m in s_months]
            # Linear slope
            n = len(s_vals)
            x_mean = (n - 1) / 2
            y_mean = sum(s_vals) / n
            slope = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(s_vals)) / \
                    max(sum((i - x_mean) ** 2 for i in range(n)), 1)

            if slope < 0 and abs(slope) > y_mean * 0.05:
                decay_pct = abs(slope * 12 / max(y_mean, 1))
                results.append({
                    "signal_type": "subscription_churn",
                    "severity": "amber",
                    "merchant": f"REVENUE DECAY: ~{decay_pct:.0%} annualised decline in processor settlements",
                    "amount": slope * 12,
                    "transaction_date": s_months[-1],
                    "description": (
                        f"Payment processor settlements are on a declining trend — "
                        f"approximately {decay_pct:.0%} annualised decline rate. "
                        f"Recent monthly average: ${s_vals[-1]:,.0f} vs peak of ${max(s_vals):,.0f}. "
                        "For subscription businesses, declining settlements indicate net negative "
                        "subscriber growth — more customers are leaving than joining. "
                        "This trend will likely continue post-acquisition unless a specific "
                        "growth initiative is implemented. The acquisition multiple should "
                        "reflect the declining trajectory, not peak revenue levels. "
                        "Request cohort retention data and customer lifetime value analysis."
                    ),
                    "library_match": "CHURN_SETTLEMENT_DECAY",
                    "confidence_weight": 0.70,
                })

    # ── Single processor concentration ──────────────────────────────────
    if settlement_txns:
        by_processor: dict[str, float] = defaultdict(float)
        for t in settlement_txns:
            by_processor[t["merchant"]] += t["amount"]
        top = max(by_processor.values()) if by_processor else 0
        if top / max(gross_settlements, 1) > 0.85 and len(by_processor) == 1:
            proc = max(by_processor, key=by_processor.get)
            results.append({
                "signal_type": "subscription_churn",
                "severity": "amber",
                "merchant": f"SINGLE PROCESSOR: 100% revenue via {proc[:40]}",
                "amount": 0,
                "transaction_date": "",
                "description": (
                    f"All payment processing (${gross_settlements:,.0f}) flows through "
                    f"a single processor: {proc}. "
                    "Single-processor dependency creates concentration risk: "
                    "(1) processor account suspension or termination halts all revenue, "
                    "(2) processor fee increases are non-negotiable without migration cost, "
                    "(3) chargeback thresholds are applied per processor — "
                    "verify current chargeback ratio and dispute status. "
                    "Recommend establishing a backup processor relationship post-close."
                ),
                "library_match": "CHURN_PROCESSOR_CONCENTRATION",
                "confidence_weight": 0.55,
            })

    return results
