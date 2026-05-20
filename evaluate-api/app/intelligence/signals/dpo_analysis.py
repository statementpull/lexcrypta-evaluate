"""Signal 44: Days Payable Outstanding (DPO) & Vendor Payment Stress.

DPO = (Accounts Payable / COGS) * 365

DPO measures how long a business takes to pay its suppliers. Stretching
payables is a classic sign of cash stress — and a hidden liability for
the buyer, who must honour those obligations at close.

High DPO signals:
  - Business is using supplier credit as a form of short-term financing
  - Vendors may have placed the business on tighter terms or COD
  - Accrued but unpaid payables inflate the liability at closing
  - Supplier relationships may be strained — pricing/priority at risk

Low DPO signals (paying faster than terms):
  - May indicate early payment discounts (positive, if intentional)
  - May indicate vendor has demanded prepayment (cash stress signal)
  - May indicate poor cash management

Industry DPO benchmarks (REL, Hackett Group):
  Manufacturing: 45–60 days
  Retail: 30–45 days
  Professional services: 20–35 days
  Healthcare: 35–55 days
  Construction: 45–75 days (retainage complicates)

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime

AP_KW_PL = ["ACCOUNTS PAYABLE", "A/P", "TRADE PAYABLE", "CREDITORS", "ACCRUED PAYABLE"]
COGS_KW_PL = ["COST OF GOODS", "COST OF SALES", "COGS", "DIRECT COST", "COST OF REVENUE"]
REVENUE_KW_PL = ["REVENUE", "SALES", "NET SALES", "GROSS REVENUE"]

PREPAYMENT_KW = ["PREPAID SUPPLIER", "COD PAYMENT", "PREPAY ORDER", "ADVANCE TO SUPPLIER"]
EARLY_PAY_KW = ["EARLY PAYMENT DISCOUNT", "PROMPT PAY DISCOUNT", "2/10 NET"]


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


def _sum_rows(pl_rows, *kw_lists) -> float:
    total = 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        for kws in kw_lists:
            if any(kw in acc for kw in kws):
                total += _row_amount(r)
                break
    return total


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not pl_rows:
        return []

    ap = abs(_sum_rows(pl_rows, AP_KW_PL))
    cogs = abs(_sum_rows(pl_rows, COGS_KW_PL))
    revenue = _sum_rows(pl_rows, REVENUE_KW_PL)

    if cogs <= 0 and revenue <= 0:
        return []

    basis = cogs if cogs > 0 else revenue * 0.5
    if ap <= 0 or basis <= 0:
        return []

    dpo = (ap / basis) * 365
    results = []

    if dpo > 75:
        severity = "red"
        verdict = f"DPO {dpo:.0f} days — CRITICAL. Vendors are being severely stretched."
    elif dpo > 55:
        severity = "amber"
        verdict = f"DPO {dpo:.0f} days — elevated. Supplier payment terms are being stretched."
    elif dpo > 45:
        severity = "amber"
        verdict = f"DPO {dpo:.0f} days — above average for most industries."
    else:
        return []

    # Prepayment signals (opposite problem — vendors demanding upfront)
    prepay_txns = [t for t in transactions if t["amount"] < 0 and
                   any(kw in t["merchant"].upper() for kw in PREPAYMENT_KW)]
    prepay_note = ""
    if prepay_txns:
        prepay_total = sum(abs(t["amount"]) for t in prepay_txns)
        prepay_note = (
            f" Prepayment/COD payments detected (${prepay_total:,.0f}) — "
            "some suppliers may have already tightened terms, requiring upfront payment."
        )

    results.append({
        "signal_type": "dpo_analysis",
        "severity": severity,
        "merchant": f"VENDOR PAYMENT STRETCH: {verdict[:60]}",
        "amount": -ap,
        "transaction_date": "",
        "description": (
            f"{verdict} "
            f"AP balance: ${ap:,.0f} on COGS of ${cogs:,.0f} (DPO = {dpo:.0f} days). "
            f"{prepay_note} "
            "Extended payables create several risks for the buyer: "
            "(1) AP balance is a real liability at closing — stretched payables must be "
            "paid by the buyer post-close, reducing effective working capital, "
            "(2) Vendors who have been stretched may demand shorter payment terms with the "
            "new owner — requiring more working capital to operate, "
            "(3) Vendor relationships may be damaged — risk of lost supply priority or "
            "pricing concessions, "
            "(4) Some vendors may have placed the business on credit hold — "
            "verify current standing with top 5 suppliers. "
            "Request aged AP schedule and confirm current payment terms with key vendors."
        ),
        "library_match": "DPO_VENDOR_STRETCH",
        "confidence_weight": 0.70,
    })

    return results
