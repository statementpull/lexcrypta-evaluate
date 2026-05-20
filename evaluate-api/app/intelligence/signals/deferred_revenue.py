"""Signal 37: Deferred Revenue & Customer Deposit Risk.

Deferred revenue and customer deposits are liabilities, not assets — yet
they are frequently omitted from SME balance sheets or understated. An
acquirer who pays for a business without accounting for these obligations
effectively pays twice: once in the purchase price, once in fulfilling
the obligation.

What is deferred revenue:
  - Customer pre-payments for services not yet delivered
  - Annual subscriptions billed upfront but not yet earned
  - Gift cards and vouchers outstanding
  - Project deposits and retainers
  - Season passes, memberships, prepaid maintenance contracts

Why it matters in acquisitions:
  - Deferred revenue is NOT free cash — it must be earned by delivering
    services. The buyer delivers the service, the seller keeps the cash.
  - ASC 606 (Revenue Recognition Standard): deferred revenue must be
    recognised when performance obligations are met, not when cash received.
  - Gift card breakage: unredeemed gift cards may create state unclaimed
    property obligations (escheatment) — a liability the buyer inherits.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict

DEFERRED_REV_KW_PL = [
    "DEFERRED REVENUE", "UNEARNED REVENUE", "CUSTOMER DEPOSIT",
    "ADVANCE PAYMENT", "PREPAID REVENUE", "CONTRACT LIABILITY",
    "GIFT CARD LIABILITY", "GIFT CERTIFICATE", "BREAKAGE",
    "SUBSCRIPTION DEFERRED", "DEFERRED INCOME",
]

DEPOSIT_INFLOW_KW = [
    "DEPOSIT RECEIVED", "CUSTOMER DEPOSIT", "PROJECT DEPOSIT",
    "RETAINER RECEIVED", "ADVANCE RECEIVED", "PRE-PAYMENT",
    "PREPAYMENT RECEIVED", "DOWN PAYMENT",
]

SUBSCRIPTION_BILLING_KW = [
    "ANNUAL SUBSCRIPTION", "ANNUAL MEMBERSHIP", "ANNUAL RENEWAL",
    "ANNUAL SERVICE", "12 MONTH", "ANNUAL FEE",
]

GIFT_CARD_KW = [
    "GIFT CARD", "GIFT CERTIFICATE", "VOUCHER SALE", "GIFT VOUCHER",
    "STORE CREDIT", "GIFT CARD SALE",
]

ESCROW_KW = [
    "ESCROW RECEIPT", "ESCROW PAYMENT", "ESCROW RELEASE",
    "TRUST ACCOUNT", "CLIENT TRUST",
]


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


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    results = []

    # ── Deferred revenue from P&L / balance sheet ─────────────────────────────
    if pl_rows:
        deferred_balance = 0.0
        for r in pl_rows:
            account = str(r.get("account", "")).upper()
            if any(kw in account for kw in DEFERRED_REV_KW_PL):
                deferred_balance += abs(_row_amount(r))

        if deferred_balance > 5000:
            results.append({
                "signal_type": "deferred_revenue",
                "severity": "amber",
                "merchant": f"DEFERRED REVENUE LIABILITY: ${deferred_balance:,.0f}",
                "amount": -deferred_balance,
                "transaction_date": "",
                "description": (
                    f"Deferred revenue / customer deposits on balance sheet: ${deferred_balance:,.0f}. "
                    "This is a LIABILITY — the business has received cash but has not yet delivered "
                    "the corresponding services or products. The buyer must fulfil these obligations. "
                    "Purchase price adjustment: (1) deferred revenue should be excluded from "
                    "the enterprise value calculation or the purchase price reduced by the "
                    "present value of the unfulfilled obligation, "
                    "(2) verify each deferred revenue category: subscriptions (service must be delivered), "
                    "deposits (may be refundable), gift cards (state escheatment rules apply), "
                    "(3) confirm deferred revenue schedule is accurate — understated deferred "
                    "revenue inflates reported income. "
                    "Request aging of deferred revenue and performance obligation schedule."
                ),
                "library_match": "DEFERRED_REVENUE_LIABILITY",
                "confidence_weight": 0.75,
            })

    if not transactions:
        return results

    # ── Large deposit inflows (customer pre-payments) ─────────────────────────
    deposit_txns = []
    gift_card_txns = []
    subscription_txns = []

    for t in transactions:
        merchant = t["merchant"].upper()
        if t["amount"] > 0:
            if any(kw in merchant for kw in DEPOSIT_INFLOW_KW):
                deposit_txns.append(t)
            if any(kw in merchant for kw in GIFT_CARD_KW):
                gift_card_txns.append(t)
            if any(kw in merchant for kw in SUBSCRIPTION_BILLING_KW):
                subscription_txns.append(t)

    if deposit_txns:
        total = sum(t["amount"] for t in deposit_txns)
        results.append({
            "signal_type": "deferred_revenue",
            "severity": "amber",
            "merchant": f"CUSTOMER DEPOSITS RECEIVED: ${total:,.0f}",
            "amount": total,
            "transaction_date": deposit_txns[0].get("transaction_date", ""),
            "description": (
                f"Customer deposit / advance payment inflows: {len(deposit_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "Customer deposits are liabilities until the service is performed or product delivered. "
                "Verify: (1) what deposits are outstanding at closing date, "
                "(2) whether deposit terms allow refund (buyer must honour refund obligations), "
                "(3) whether deposits are held in a separate trust/escrow account or commingled "
                "(commingling is a red flag and may violate state consumer protection laws). "
                "Adjust WC peg to exclude customer deposits from current assets."
            ),
            "library_match": "CUSTOMER_DEPOSITS",
            "confidence_weight": 0.60,
        })

    if gift_card_txns:
        total = sum(t["amount"] for t in gift_card_txns)
        results.append({
            "signal_type": "deferred_revenue",
            "severity": "amber",
            "merchant": f"GIFT CARD / VOUCHER SALES: ${total:,.0f} — escheatment risk",
            "amount": total,
            "transaction_date": gift_card_txns[0].get("transaction_date", ""),
            "description": (
                f"Gift card and voucher sales: {len(gift_card_txns)} transactions totalling ${total:,.0f}. "
                "Gift cards create two obligations: (1) redemption liability — the buyer must honour "
                "all outstanding gift cards regardless of when they were sold, "
                "(2) escheatment / unclaimed property — unredeemed gift cards become state property "
                "after a dormancy period (typically 3–5 years). Most states require annual reporting "
                "and remittance of unclaimed property — verify compliance history. "
                "Estimate outstanding gift card liability and treat as a purchase price deduction."
            ),
            "library_match": "GIFT_CARD_ESCHEATMENT",
            "confidence_weight": 0.65,
        })

    return results
