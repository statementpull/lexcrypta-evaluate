"""Signal 31: Recurring Revenue Quality & MRR Estimation.

Recurring revenue is the most valuable type of revenue in an acquisition —
it commands premium multiples precisely because it is predictable and doesn't
require constant re-selling. But not all recurring revenue is equal.

What we detect:
  MRR/ARR estimation: Identify recurring inflow patterns (same source,
    monthly cadence) and estimate Monthly Recurring Revenue.
  Payment processor settlements: Stripe, Square, PayPal — indicate
    e-commerce or subscription revenue (typically high quality).
  Subscription churn signals: Declining settlement amounts from a steady
    payment processor indicate customer churn.
  Revenue mix: What proportion of total revenue is truly recurring vs one-time?
  Concentration in MRR: If recurring revenue is from one customer, it's
    not as valuable as distributed recurring revenue.

Revenue quality tiers (acquisition multiple premium):
  Tier 1: Contracted recurring (SaaS, subscription) → 6–15x ARR
  Tier 2: Habitual recurring (repeat customers, standing orders) → 3–6x EBITDA
  Tier 3: Project/transactional → 2–4x EBITDA
  Tier 4: One-time/lumpy → 1.5–3x EBITDA

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict
from datetime import datetime
import re


PAYMENT_PROCESSOR_KEYWORDS = [
    "STRIPE", "SQUARE ", "PAYPAL ", "BRAINTREE", "ADYEN",
    "AUTHORIZE.NET", "SHOPIFY PAYMENT", "CLOVER ", "TOAST POS",
    "LIGHTSPEED", "HEARTLAND PAYMENT", "WORLDPAY", "ELAVON",
    "FIRST DATA", "GLOBAL PAYMENTS", "PAYA ", "NMI ",
]

SUBSCRIPTION_PLATFORM_KEYWORDS = [
    "RECURLY", "CHARGEBEE", "ZUORA", "CHARGIFY", "PADDLE ",
    "FASTSPRING", "MAXIO", "MEMBERFUL", "SUBSTACK PAYMENT",
]

DIRECT_DEBIT_KEYWORDS = [
    "DIRECT DEBIT", "STANDING ORDER", "RECURRING PAYMENT",
    "AUTO PAY RECEIVED", "AUTOPAY DEPOSIT",
]


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _detect_recurring_payers(transactions: list[dict]) -> list[dict]:
    """Find inflow sources that repeat monthly with consistent amounts."""
    monthly_by_source: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for t in transactions:
        if t["amount"] <= 0:
            continue
        d = _parse_date(t.get("transaction_date", ""))
        if not d:
            continue
        key = t["merchant"].upper()[:40].strip()
        month = f"{d.year}-{d.month:02d}"
        monthly_by_source[key][month] += t["amount"]

    recurring = []
    for source, months in monthly_by_source.items():
        if len(months) >= 3:
            vals = list(months.values())
            avg = sum(vals) / len(vals)
            cv = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5 / avg if avg > 0 else 1
            if cv < 0.25 and avg > 500:  # Low variance = recurring
                recurring.append({
                    "source": source,
                    "monthly_avg": avg,
                    "months_seen": len(months),
                    "cv": cv,
                })

    return sorted(recurring, key=lambda x: -x["monthly_avg"])


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    inflows = [t for t in transactions if t["amount"] > 0]
    if not inflows:
        return []

    total_inflow = sum(t["amount"] for t in inflows)

    # ── Payment processor settlements (subscription/e-commerce signal) ────────
    processor_txns = []
    sub_platform_txns = []
    for t in inflows:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in PAYMENT_PROCESSOR_KEYWORDS):
            processor_txns.append(t)
        if any(kw in merchant for kw in SUBSCRIPTION_PLATFORM_KEYWORDS):
            sub_platform_txns.append(t)

    # ── MRR estimation from recurring payer patterns ──────────────────────────
    recurring_payers = _detect_recurring_payers(transactions)
    total_mrr = sum(r["monthly_avg"] for r in recurring_payers)
    annual_arr = total_mrr * 12

    # Revenue mix
    if total_inflow > 0:
        recurring_pct = min(annual_arr / (total_inflow), 1.0)
    else:
        recurring_pct = 0

    # ── Processor settlement trend (churn detection) ──────────────────────────
    trend_note = ""
    if len(processor_txns) >= 4:
        monthly_proc: dict[str, float] = defaultdict(float)
        for t in processor_txns:
            d = _parse_date(t.get("transaction_date", ""))
            if d:
                monthly_proc[f"{d.year}-{d.month:02d}"] += t["amount"]
        if len(monthly_proc) >= 3:
            sorted_months = sorted(monthly_proc.keys())
            vals = [monthly_proc[m] for m in sorted_months]
            recent = sum(vals[-2:]) / 2
            prior = sum(vals[:-2]) / max(len(vals) - 2, 1)
            if prior > 0:
                churn_pct = (recent - prior) / prior
                if churn_pct < -0.15:
                    trend_note = (
                        f" CHURN SIGNAL: Payment processor settlements declining — "
                        f"recent avg ${recent:,.0f}/month vs prior ${prior:,.0f}/month "
                        f"({churn_pct:+.0%}). Declining settlement volume indicates "
                        "customer attrition or basket size reduction."
                    )

    if not (processor_txns or sub_platform_txns or (total_mrr > 0)):
        return []

    # ── Recurring revenue quality flag ───────────────────────────────────────
    sev = "amber"
    if trend_note:
        sev = "red"

    top_recurring = recurring_payers[:5]
    recurring_text = " | ".join(
        f"{r['source'][:25]}: ${r['monthly_avg']:,.0f}/mo ({r['months_seen']} months)"
        for r in top_recurring
    ) if top_recurring else "Pattern detection in progress"

    proc_total = sum(t["amount"] for t in processor_txns)
    sub_total = sum(t["amount"] for t in sub_platform_txns)

    description = (
        f"Recurring revenue analysis: estimated MRR ${total_mrr:,.0f} (ARR ${annual_arr:,.0f}). "
        f"Recurring revenue as % of total inflows: {recurring_pct:.0%}. "
        f"{'Payment processor settlements: $' + str(f'{proc_total:,.0f}') + ' (Stripe/Square/PayPal) — indicates subscription or e-commerce revenue. ' if processor_txns else ''}"
        f"{'Subscription platform payments: $' + str(f'{sub_total:,.0f}') + '. ' if sub_platform_txns else ''}"
        f"Identified recurring payers: {recurring_text}. "
        f"{trend_note} "
        "Revenue quality note: contracted recurring revenue (SaaS, subscription) commands "
        "6–15x ARR multiples vs 2–4x for transactional revenue. "
        "Verify: (1) what proportion of revenue is under contract vs at-will, "
        "(2) average contract length and renewal rates, "
        "(3) customer churn rate (ideally <5%/month for SaaS), "
        "(4) whether recurring revenue requires the current owner's relationships to renew."
    )

    results.append({
        "signal_type": "recurring_revenue",
        "severity": sev,
        "merchant": f"RECURRING REVENUE: Est. MRR ${total_mrr:,.0f} | ARR ${annual_arr:,.0f} | {recurring_pct:.0%} recurring",
        "amount": annual_arr,
        "transaction_date": "",
        "description": description[:1500],
        "library_match": "RECURRING_REVENUE_QUALITY",
        "confidence_weight": 0.60,
    })

    return results
