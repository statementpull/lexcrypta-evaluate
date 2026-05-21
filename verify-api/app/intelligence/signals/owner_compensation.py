"""Signal 41: Owner Compensation Deep Analysis.

Owner compensation normalisation is the single most important adjustment
in SME valuation. The entire SDE (Seller's Discretionary Earnings) model
exists because owner-operators pay themselves in multiple ways — many
invisible unless you look at the bank data directly.

Total owner economic benefit includes:
  1. W-2 salary or 1099 draws
  2. S-Corp/LLC distributions above market-rate salary
  3. Personal vehicle (lease payments, fuel, insurance run through business)
  4. Health, dental, vision, life insurance premiums
  5. Phone and personal subscriptions billed to business
  6. Family member salaries (spouse/children on payroll)
  7. Personal travel and entertainment
  8. Home office expenses
  9. Charitable contributions made through business
  10. Personal legal and accounting fees
  11. Owner's personal loans from the business (contra-revenue)

Why it matters: Each dollar of owner benefit that can be legitimately
added back to EBITDA increases the business's SDE — and therefore its
valuation. But these addbacks must be documented and defensible.
Inflated or undocumented addbacks are a form of seller misrepresentation.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict

OWNER_DRAW_KW = [
    "OWNER DRAW", "OWNER SALARY", "OFFICER SALARY", "OFFICER COMPENSATION",
    "MANAGING MEMBER", "MEMBER DRAW", "MEMBER DISTRIBUTION",
    "SHAREHOLDER DRAW", "PARTNER DRAW", "PRINCIPAL SALARY",
    "OWNER COMPENSATION", "OWNER PAYMENT",
]

FAMILY_PAYROLL_KW = [
    "FAMILY PAYROLL", "SPOUSE SALARY", "WIFE SALARY", "HUSBAND SALARY",
    "SON SALARY", "DAUGHTER SALARY", "FAMILY MEMBER",
]

PERSONAL_VEHICLE_KW = [
    "AUTO LEASE", "CAR LEASE", "VEHICLE LEASE", "PERSONAL VEHICLE",
    "CAR PAYMENT", "TRUCK PAYMENT", "AUTO PAYMENT",
    "EXXON", "SHELL GAS", "BP GAS", "SUNOCO", "CHEVRON GAS",
    "AUTO INSURANCE", "VEHICLE INSURANCE",
]

PERSONAL_SUBSCRIPTION_KW = [
    "NETFLIX", "SPOTIFY", "APPLE ONE", "AMAZON PRIME",
    "HULU ", "DISNEY PLUS", "HBO MAX",
    "PERSONAL CELL", "PERSONAL PHONE",
]

CHARITABLE_KW = [
    "CHARITABLE", "DONATION", "CHARITY", "NONPROFIT DONATION",
    "CHURCH DONATION", "TITHE", "GOFUNDME",
]

PERSONAL_TRAVEL_KW = [
    "PERSONAL TRAVEL", "VACATION", "AIRBNB PERSONAL",
    "CRUISE LINE", "RESORT PAYMENT", "GOLF CLUB",
    "COUNTRY CLUB", "YACHT CLUB", "SPORTS CLUB MEMBER",
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


def _sum_pl(pl_rows, *kw_lists) -> float:
    total = 0.0
    if not pl_rows:
        return 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        for kws in kw_lists:
            if any(kw in acc for kw in kws):
                total += abs(_row_amount(r))
                break
    return total


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    draw_txns, family_txns, vehicle_txns = [], [], []
    subscription_txns, charitable_txns, travel_txns = [], [], []

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in OWNER_DRAW_KW):
            draw_txns.append(t)
        if any(kw in merchant for kw in FAMILY_PAYROLL_KW):
            family_txns.append(t)
        if any(kw in merchant for kw in PERSONAL_VEHICLE_KW):
            vehicle_txns.append(t)
        if any(kw in merchant for kw in PERSONAL_SUBSCRIPTION_KW):
            subscription_txns.append(t)
        if any(kw in merchant for kw in CHARITABLE_KW):
            charitable_txns.append(t)
        if any(kw in merchant for kw in PERSONAL_TRAVEL_KW):
            travel_txns.append(t)

    all_owner = draw_txns + family_txns + vehicle_txns + subscription_txns + charitable_txns + travel_txns
    if not all_owner:
        return []

    # Build addback summary
    addback_items = []
    total_addbacks = 0.0

    if draw_txns:
        amt = sum(abs(t["amount"]) for t in draw_txns)
        total_addbacks += amt
        addback_items.append(f"Owner draws/salary: ${amt:,.0f}")

    if family_txns:
        amt = sum(abs(t["amount"]) for t in family_txns)
        total_addbacks += amt
        addback_items.append(f"Family member compensation: ${amt:,.0f}")

    if vehicle_txns:
        amt = sum(abs(t["amount"]) for t in vehicle_txns)
        total_addbacks += amt * 0.7  # assume 70% personal use
        addback_items.append(f"Vehicle/auto expenses (70% personal use): ${amt * 0.7:,.0f}")

    if subscription_txns:
        amt = sum(abs(t["amount"]) for t in subscription_txns)
        total_addbacks += amt
        addback_items.append(f"Personal subscriptions: ${amt:,.0f}")

    if charitable_txns:
        amt = sum(abs(t["amount"]) for t in charitable_txns)
        total_addbacks += amt
        addback_items.append(f"Charitable contributions: ${amt:,.0f}")

    if travel_txns:
        amt = sum(abs(t["amount"]) for t in travel_txns)
        total_addbacks += amt * 0.5  # assume 50% personal
        addback_items.append(f"Personal travel/entertainment (est. 50%): ${amt * 0.5:,.0f}")

    addback_text = " | ".join(addback_items)

    severity = "amber"
    if total_addbacks > 200000:
        severity = "red"

    results.append({
        "signal_type": "owner_compensation",
        "severity": severity,
        "merchant": f"OWNER BENEFIT ADDBACKS: ${total_addbacks:,.0f} identified",
        "amount": -total_addbacks,
        "transaction_date": all_owner[0].get("transaction_date", ""),
        "description": (
            f"Owner compensation and personal benefit analysis: "
            f"${total_addbacks:,.0f} in identified addback categories. "
            f"Addback detail: {addback_text}. "
            "SDE NORMALISATION: Each dollar of legitimate owner benefit added back to EBITDA "
            "increases the business's SDE — the appropriate valuation basis for owner-operator buyers. "
            "CAUTION: Addbacks must be fully documented with source invoices. "
            "Inflated or undocumented addbacks constitute seller misrepresentation. "
            "Obtain the seller's addback schedule and verify each item: "
            "(1) owner salary — should reflect market-rate salary for the owner's role, "
            "not the full draw (a replacement manager still costs money), "
            "(2) family compensation — if family members work in the business, "
            "their replacement cost should remain in EBITDA, "
            "(3) vehicle — verify the percentage of business use with mileage logs, "
            "(4) charitable contributions — fully addable, "
            "(5) personal expenses run through the business — require documentation. "
            "Adjusted EBITDA with documented addbacks should be presented to lenders."
        ),
        "library_match": "OWNER_COMPENSATION_ADDBACKS",
        "confidence_weight": 0.70,
    })

    return results
