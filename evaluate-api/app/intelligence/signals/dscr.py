"""Signal 19: Debt Service Coverage Ratio (DSCR) — Acquisition Financing Viability.

DSCR = Net Operating Income / Total Debt Service (Annual Principal + Interest)

The DSCR is the primary metric used by lenders (SBA, conventional, DSCR loans)
to determine whether a business can service acquisition debt from its own cash flows.

Lender thresholds (SBA SOP 50 10 7, conventional acquisition lending standards):
  DSCR >= 1.25x = Minimum SBA 7(a) requirement
  DSCR >= 1.35x = Preferred by most conventional lenders
  DSCR >= 1.50x = Strong — qualifies for best terms
  DSCR 1.10–1.25x = Marginal — may require additional collateral or guarantees
  DSCR < 1.10x = Deal likely not financeable with standard debt
  DSCR < 1.00x = Business cannot cover its own debt service — distress signal

We compute:
  1. Available DSCR: what DSCR would be at various acquisition price multiples
  2. Maximum supportable debt: the loan amount the EBITDA can service at 1.25x
  3. Implied maximum acquisition price: at typical 10–25yr term, 7% SBA rate

Sources:
- SBA SOP 50 10 7 (current edition) — DSCR ≥1.25 required for 7(a) eligibility
- Pepperdine Private Capital Markets Report 2024 — lender preference bands
- FDIC Supervisory Insights — DSCR calculation methodology for small business

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re


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
        desc = str(r.get("description", "")).upper()
        for kws in keyword_lists:
            if any(kw in account or kw in desc for kw in kws):
                total += _row_amount(r)
                break
    return total


REVENUE_KW = ["REVENUE", "SALES", "INCOME", "NET SALES", "GROSS REVENUE"]
COGS_KW = ["COST OF GOODS", "COST OF SALES", "COGS", "DIRECT COST", "COST OF REVENUE"]
SGA_KW = ["SELLING", "GENERAL", "ADMINISTRATIVE", "SG&A", "G&A", "OVERHEAD"]
DEPR_KW = ["DEPRECIATION", "AMORTIZATION", "AMORTISATION", "D&A"]
INTEREST_KW = ["INTEREST EXPENSE", "INTEREST PAID", "FINANCE CHARGE", "LOAN INTEREST"]
TAX_KW = ["INCOME TAX", "TAX PROVISION", "CORPORATE TAX"]
NET_INCOME_KW = ["NET INCOME", "NET PROFIT", "NET LOSS", "NET EARNINGS"]
OWNER_SALARY_KW = ["OWNER SALARY", "OFFICER SALARY", "OWNER COMPENSATION", "MANAGING MEMBER",
                   "OWNER DRAW", "MEMBER DISTRIBUTION", "SHAREHOLDER SALARY"]
EXISTING_DEBT_KW = ["LOAN PAYMENT", "DEBT SERVICE", "NOTE PAYABLE", "MORTGAGE PAYMENT",
                    "SBA PAYMENT", "EQUIPMENT LOAN", "LEASE PAYMENT"]


def _annual_debt_service(principal: float, rate: float, years: int) -> float:
    """Monthly payment × 12 using standard amortization."""
    if rate <= 0 or years <= 0 or principal <= 0:
        return 0.0
    r = rate / 12
    n = years * 12
    monthly = principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
    return monthly * 12


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not pl_rows:
        return []

    revenue = _sum_rows(pl_rows, REVENUE_KW)
    if revenue <= 0:
        return []

    cogs = abs(_sum_rows(pl_rows, COGS_KW))
    sga = abs(_sum_rows(pl_rows, SGA_KW))
    depr = abs(_sum_rows(pl_rows, DEPR_KW))
    interest = abs(_sum_rows(pl_rows, INTEREST_KW))
    taxes = abs(_sum_rows(pl_rows, TAX_KW))
    net_income = _sum_rows(pl_rows, NET_INCOME_KW)
    owner_salary = abs(_sum_rows(pl_rows, OWNER_SALARY_KW))
    existing_debt_service = abs(_sum_rows(pl_rows, EXISTING_DEBT_KW))

    # Existing debt payments from bank
    bank_loan_payments = sum(
        abs(t["amount"]) for t in transactions
        if t["amount"] < 0 and any(
            kw in t["merchant"].upper()
            for kw in ["LOAN PMT", "LOAN PAYMENT", "SBA", "EIDL", "MORTGAGE"]
        )
    )

    # EBITDA
    ebitda = net_income + interest + taxes + depr

    # SDE (owner-operator benefit) — used for small business lending
    sde = ebitda + owner_salary

    if ebitda <= 0 and sde <= 0:
        return []

    results = []
    noi = max(ebitda, sde * 0.7)  # Use EBITDA; fall back to conservative SDE proxy

    # ── SBA 7(a) acquisition financing model ─────────────────────────────────
    # SBA 7(a): up to 25 years, ~7.0–8.5% rate (Prime + 2.75%, capped)
    # We model at 7.5% / 10 year (conservative), 7.0% / 25 year (SBA max)

    scenarios = [
        ("SBA 7(a) 25-year", 0.075, 25),
        ("Conventional 10-year", 0.085, 10),
        ("Seller finance 5-year", 0.060, 5),
    ]

    financing_lines = []
    for label, rate, years in scenarios:
        # Max supportable debt at DSCR = 1.25x
        # Annual debt service = NOI / 1.25
        max_annual_ds = noi / 1.25
        # Reverse-engineer principal from annuity formula
        r = rate / 12
        n = years * 12
        if r > 0:
            max_debt = max_annual_ds / 12 * ((1 + r) ** n - 1) / (r * (1 + r) ** n)
        else:
            max_debt = max_annual_ds * years

        financing_lines.append(
            f"{label} @ {rate:.1%}: max supportable loan ${max_debt:,.0f} "
            f"(annual P&I ${max_annual_ds:,.0f})"
        )

    # ── DSCR at common acquisition multiples ─────────────────────────────────
    # For each implied price (3x, 4x, 5x EBITDA), compute DSCR assuming 80% financed
    multiple_lines = []
    for multiple in [2.5, 3.0, 4.0, 5.0]:
        implied_price = noi * multiple
        loan_amount = implied_price * 0.80  # 80% LTV is typical SBA
        annual_ds_10yr = _annual_debt_service(loan_amount, 0.085, 10)
        annual_ds_25yr = _annual_debt_service(loan_amount, 0.075, 25)
        dscr_10yr = noi / annual_ds_10yr if annual_ds_10yr > 0 else 99
        dscr_25yr = noi / annual_ds_25yr if annual_ds_25yr > 0 else 99
        flag = ""
        if dscr_25yr < 1.00:
            flag = " ❌ NOT SERVICEABLE"
        elif dscr_25yr < 1.25:
            flag = " ⚠ BELOW SBA MINIMUM"
        elif dscr_25yr >= 1.50:
            flag = " ✓ STRONG"
        multiple_lines.append(
            f"{multiple:.1f}x (${implied_price:,.0f}): "
            f"DSCR {dscr_25yr:.2f}x (25yr SBA) / {dscr_10yr:.2f}x (10yr conv){flag}"
        )

    # ── Overall DSCR assessment ───────────────────────────────────────────────
    # Use existing debt service to compute current DSCR
    current_ds = max(existing_debt_service, bank_loan_payments, interest)
    if current_ds > 0:
        current_dscr = noi / current_ds
        if current_dscr < 1.00:
            severity = "red"
            dscr_verdict = f"CURRENT DSCR {current_dscr:.2f}x — business cannot cover existing debt from NOI"
        elif current_dscr < 1.25:
            severity = "amber"
            dscr_verdict = f"CURRENT DSCR {current_dscr:.2f}x — below SBA minimum (1.25x required)"
        else:
            severity = "amber"
            dscr_verdict = f"CURRENT DSCR {current_dscr:.2f}x on existing obligations"
    else:
        severity = "amber"
        dscr_verdict = "No significant existing debt service identified — financing capacity modelled below"
        current_dscr = None

    financing_text = " | ".join(financing_lines)
    multiple_text = " | ".join(multiple_lines)

    description = (
        f"EBITDA: ${ebitda:,.0f} | SDE: ${sde:,.0f} | NOI used for DSCR: ${noi:,.0f}. "
        f"{dscr_verdict}. "
        f"Maximum supportable acquisition debt (at 1.25x DSCR): {financing_text}. "
        f"DSCR by acquisition multiple (80% financed): {multiple_text}. "
        "SBA 7(a) requires DSCR ≥ 1.25x on a global basis (including all business and personal "
        "obligations of the guarantor). These figures are indicative — lenders will use "
        "tax-return EBITDA not P&L EBITDA, and will apply their own global cash flow analysis. "
        "Engage an SBA lender or commercial banker for pre-qualification before making an offer."
    )

    results.append({
        "signal_type": "dscr",
        "severity": severity,
        "merchant": f"DSCR ANALYSIS: NOI ${noi:,.0f} | {dscr_verdict.split('—')[0].strip()}",
        "amount": noi,
        "transaction_date": "",
        "description": description[:1500],
        "library_match": "DSCR_ACQUISITION",
        "confidence_weight": 0.70,
    })

    return results
