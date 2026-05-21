"""Signal 07: Real Estate Intelligence — mortgage servicers, rental income, HOA, Section 8, escrow anomalies."""
from collections import defaultdict

MORTGAGE_SERVICERS = [
    "SELENE", "SLENE", "SHELLPOINT", "MR COOPER", "NATIONSTAR", "PHH MORTGAGE",
    "CENLAR", "LOANCARE", "ROUNDPOINT", "FREEDOM MORTGAGE", "PENNYMAC",
    "CALIBER HOME", "FLAGSTAR", "ROCKET MORTGAGE", "QUICKEN LOAN",
    "WELLS FARGO HOME", "CHASE MORTGAGE", "OCWEN", "SELECT PORTFOLIO",
    "NEWREZ", "AMERIHOME", "PLANET HOME",
]

HOA_KEYWORDS = [
    "HOA", "HOMEOWNERS ASSN", "HOMEOWNERS ASSOCIATION", "CONDO ASSN",
    "CONDO ASSOCIATION", "COMMUNITY ASSN", "PROPERTY OWNERS ASSN",
    "MASTER ASSN",
]

PROPERTY_MGMT_KEYWORDS = [
    "PROPERTY MANAGEMENT", "PROP MGMT", "PROPERTY MGMT",
    "MANAGEMENT CO", "REAL PROPERTY", "RENTAL MANAGEMENT",
]

SECTION8_KEYWORDS = [
    "LMHA", "METRO HOUSING", "HOUSING AUTHORITY", "HAP PAYMENT",
    "SECTION 8", "HCV PAYMENT", "VOUCHER PAYMENT", "HOUSING COMMISSION",
]

ESCROW_KEYWORDS = [
    "ESCROW", "TITLE COMPANY", "CLOSING COST", "SETTLEMENT AGENT",
    "QUALIA", "STAVVY", "SIMPLIFILE",
]


def run(transactions: list[dict], loader=None) -> list[dict]:
    results = []

    mortgage_groups: dict[str, list] = defaultdict(list)
    rental_income: list[dict] = []
    irregular_income_months: dict[int, float] = defaultdict(float)

    for t in transactions:
        merchant = t["merchant"].upper()
        amt = t["amount"]

        # ── Mortgage servicer detection ───────────────────────────────────────
        for servicer in MORTGAGE_SERVICERS:
            if servicer in merchant and amt < 0:
                mortgage_groups[merchant].append(t)
                break

        # ── HOA detection ─────────────────────────────────────────────────────
        if amt < 0 and any(kw in merchant for kw in HOA_KEYWORDS):
            results.append({
                "signal_type": "real_estate",
                "severity": "amber",
                "merchant": merchant,
                "amount": amt,
                "transaction_date": t.get("transaction_date", ""),
                "description": (
                    f"HOA/strata payment to '{merchant}': ${abs(amt):,.0f}. "
                    "Verify property is disclosed and HOA obligations are current. "
                    "Delinquent HOA can result in super-priority lien ahead of mortgage."
                ),
                "library_match": None,
                "confidence_weight": 0.60,
            })

        # ── Section 8 / HAP income ────────────────────────────────────────────
        if amt > 0 and any(kw in merchant for kw in SECTION8_KEYWORDS):
            results.append({
                "signal_type": "real_estate",
                "severity": "amber",
                "merchant": merchant,
                "amount": amt,
                "transaction_date": t.get("transaction_date", ""),
                "description": (
                    f"Section 8 / HAP payment received from '{merchant}': ${abs(amt):,.0f}. "
                    "Income is government-subsidised — verify HAP contract and current voucher status. "
                    "Tenant-portion split must reconcile with lease agreement."
                ),
                "library_match": None,
                "confidence_weight": 0.70,
            })

        # ── Property management fees (possible self-management flag) ──────────
        if amt < 0 and any(kw in merchant for kw in PROPERTY_MGMT_KEYWORDS):
            results.append({
                "signal_type": "real_estate",
                "severity": "amber",
                "merchant": merchant,
                "amount": amt,
                "transaction_date": t.get("transaction_date", ""),
                "description": (
                    f"Property management payment to '{merchant}': ${abs(amt):,.0f}. "
                    "Cross-check against Schedule E line 6 (management fees). "
                    "Verify entity is arm's-length — self-managed properties using related entities "
                    "trigger IRS self-rental rules (Form 8825 property type code 7)."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

        # ── Escrow / title / closing transactions ─────────────────────────────
        if any(kw in merchant for kw in ESCROW_KEYWORDS):
            severity = "red" if abs(amt) > 50000 else "amber"
            direction = "disbursement" if amt < 0 else "receipt"
            results.append({
                "signal_type": "real_estate",
                "severity": severity,
                "merchant": merchant,
                "amount": amt,
                "transaction_date": t.get("transaction_date", ""),
                "description": (
                    f"Escrow/title {direction}: '{merchant}' — ${abs(amt):,.0f}. "
                    "Obtain ALTA settlement statement to verify all disbursements. "
                    "Cross-check payoff amounts against declared mortgage balances."
                ),
                "library_match": None,
                "confidence_weight": 0.75,
            })

        # ── Collect rental income for pattern analysis ────────────────────────
        if amt > 500 and amt < 15000:
            date_str = str(t.get("transaction_date", ""))
            try:
                month = int(date_str[5:7]) if len(date_str) >= 7 else 0
                if month:
                    irregular_income_months[month] += amt
                    rental_income.append(t)
            except (ValueError, TypeError):
                pass

    # ── Mortgage servicer recurring pattern analysis ──────────────────────────
    for servicer_merchant, txns in mortgage_groups.items():
        if len(txns) < 2:
            continue
        amounts = [abs(t["amount"]) for t in txns]
        avg = sum(amounts) / len(amounts)
        variance = max(amounts) - min(amounts)

        if variance > avg * 0.15:
            results.append({
                "signal_type": "real_estate",
                "severity": "amber",
                "merchant": servicer_merchant,
                "amount": -sum(amounts),
                "transaction_date": txns[0].get("transaction_date", ""),
                "description": (
                    f"Mortgage payments to '{servicer_merchant}': {len(txns)} payments, "
                    f"avg ${avg:,.0f}, variance ${variance:,.0f} ({variance/avg:.0%}). "
                    "Payment variation >15% may indicate escrow adjustments, late fees, "
                    "or ARM rate change. Obtain current mortgage statement."
                ),
                "library_match": None,
                "confidence_weight": 0.60,
            })
        else:
            results.append({
                "signal_type": "real_estate",
                "severity": "amber",
                "merchant": servicer_merchant,
                "amount": -sum(amounts),
                "transaction_date": txns[0].get("transaction_date", ""),
                "description": (
                    f"Recurring mortgage: '{servicer_merchant}' — {len(txns)} payments "
                    f"averaging ${avg:,.0f}/month (total ${sum(amounts):,.0f}). "
                    "Verify loan balance and payoff figure against ALTA or mortgage statement."
                ),
                "library_match": None,
                "confidence_weight": 0.55,
            })

    # ── Rental income concentration check ────────────────────────────────────
    if irregular_income_months:
        total_rental = sum(irregular_income_months.values())
        if total_rental > 10000:
            peak_month = max(irregular_income_months, key=lambda k: irregular_income_months[k])
            peak_pct = irregular_income_months[peak_month] / total_rental
            if peak_pct > 0.40:
                results.append({
                    "signal_type": "real_estate",
                    "severity": "amber",
                    "merchant": "RENTAL INCOME PATTERN",
                    "amount": total_rental,
                    "transaction_date": "",
                    "description": (
                        f"Rental income concentration: {peak_pct:.0%} received in month {peak_month}. "
                        "Inconsistent with steady tenancy — may indicate vacancies, lease-up delays, "
                        "or back-rent collection. Reconcile against lease agreements and rent roll."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.60,
                })

    return results
