"""Signal 46: PPP / COVID Revenue Distortion Detection.

PPP loans, EIDL grants, and COVID-era government programs inflated business
financials in 2020–2022. A business that looks healthy in a 3-year trailing
average may owe that health to non-recurring government stimulus — not real
earning power.

Key distortions:
  PPP loan forgiveness: Treated as tax-free income — inflated net income
    without corresponding revenue. A business with $500k PPP forgiveness
    looks $500k more profitable than it really is.
  EIDL loans: Not revenue, but low-rate loans (3.75%) that must be repaid
    over 30 years. At acquisition, outstanding EIDL balance transfers.
  EIDL grants: $1,000 per employee advance — genuinely non-recurring.
  Employee Retention Credit (ERC): Payroll tax credit (now under IRS
    scrutiny for abuse) — inflated cash flow in 2020–2021.
  COVID-era rent deferrals: Deferred rent that is now being paid —
    inflating current period expenses vs historical.
  Government grants: State/local COVID relief — one-time.

Deal team implication: ALWAYS recast the trailing 3-year P&L to EXCLUDE
all COVID-era non-recurring items before applying EBITDA multiples.
A business that "earned" $300k EBITDA but $150k came from PPP forgiveness
is only worth half as much as it appears.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

PPP_KEYWORDS = [
    "PPP LOAN", "PPP FORGIVENESS", "PPP PROCEEDS", "PAYCHECK PROTECTION",
    "SBA PPP", "PPP DEPOSIT",
]

EIDL_KEYWORDS = [
    "EIDL", "EIDL GRANT", "EIDL LOAN", "EIDL ADVANCE", "EIDL DEPOSIT",
    "ECONOMIC INJURY", "SBA EIDL",
]

ERC_KEYWORDS = [
    "EMPLOYEE RETENTION CREDIT", "ERC CREDIT", "ERC REFUND",
    "941 REFUND", "PAYROLL TAX REFUND", "COVID CREDIT",
]

COVID_GRANT_KEYWORDS = [
    "COVID RELIEF", "COVID GRANT", "CARES ACT", "RESTAURANT REVITALIZATION",
    "RRF GRANT", "SHUTTERED VENUE", "SVOG", "MAIN STREET LENDING",
    "STATE COVID GRANT", "ECONOMIC RELIEF",
]

RENT_DEFERRAL_KEYWORDS = [
    "DEFERRED RENT", "BACK RENT", "RENT DEFERRAL", "CATCH-UP RENT",
    "RENT ARREARS",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    ppp_txns, eidl_txns, erc_txns, grant_txns, rent_txns = [], [], [], [], []

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in PPP_KEYWORDS):
            ppp_txns.append(t)
        if any(kw in merchant for kw in EIDL_KEYWORDS):
            eidl_txns.append(t)
        if any(kw in merchant for kw in ERC_KEYWORDS):
            erc_txns.append(t)
        if any(kw in merchant for kw in COVID_GRANT_KEYWORDS):
            grant_txns.append(t)
        if any(kw in merchant for kw in RENT_DEFERRAL_KEYWORDS):
            rent_txns.append(t)

    if ppp_txns:
        total = sum(t["amount"] for t in ppp_txns if t["amount"] > 0)
        results.append({
            "signal_type": "ppp_distortion",
            "severity": "red",
            "merchant": f"PPP LOAN PROCEEDS: ${total:,.0f} — recast EBITDA required",
            "amount": total,
            "transaction_date": ppp_txns[0].get("transaction_date", ""),
            "description": (
                f"PPP (Paycheck Protection Program) loan deposits: ${total:,.0f}. "
                "PPP EBITDA DISTORTION: PPP loan forgiveness was recorded as tax-free income "
                "in 2020–2021 financials, inflating reported net income and EBITDA. "
                "MANDATORY recast: remove PPP forgiveness income from all trailing period "
                "financials before applying acquisition multiples. "
                "If the seller's trailing 3-year EBITDA includes PPP years, recalculate "
                "excluding non-recurring items. A 3x EBITDA multiple on PPP-inflated earnings "
                "is effectively a higher multiple on real earning power."
            ),
            "library_match": "COVID_PPP_DISTORTION",
            "confidence_weight": 0.85,
        })

    if eidl_txns:
        loan_deposits = sum(t["amount"] for t in eidl_txns if t["amount"] > 0)
        loan_payments = sum(abs(t["amount"]) for t in eidl_txns if t["amount"] < 0)
        outstanding_est = max(loan_deposits - loan_payments, 0)
        results.append({
            "signal_type": "ppp_distortion",
            "severity": "red",
            "merchant": f"EIDL LOAN: ~${outstanding_est:,.0f} estimated outstanding",
            "amount": -outstanding_est,
            "transaction_date": eidl_txns[0].get("transaction_date", ""),
            "description": (
                f"SBA EIDL (Economic Injury Disaster Loan) activity: "
                f"${loan_deposits:,.0f} received, ${loan_payments:,.0f} repaid. "
                f"Estimated outstanding: ${outstanding_est:,.0f}. "
                "EIDL implications at acquisition: "
                "(1) EIDL loans have a change-of-control clause — SBA must approve transfer, "
                "(2) EIDL loans >$25k have collateral (business assets and personal guarantee), "
                "(3) EIDL loans >$500k require additional collateral, "
                "(4) Outstanding balance is a real liability that reduces net acquisition value — "
                "treat as debt in enterprise value bridge calculation. "
                "Obtain current EIDL payoff statement from SBA portal before closing."
            ),
            "library_match": "COVID_EIDL_OUTSTANDING",
            "confidence_weight": 0.85,
        })

    if erc_txns:
        total = sum(t["amount"] for t in erc_txns if t["amount"] > 0)
        results.append({
            "signal_type": "ppp_distortion",
            "severity": "amber",
            "merchant": f"EMPLOYEE RETENTION CREDIT: ${total:,.0f}",
            "amount": total,
            "transaction_date": erc_txns[0].get("transaction_date", ""),
            "description": (
                f"ERC (Employee Retention Credit) refunds: ${total:,.0f}. "
                "ERC is a non-recurring payroll tax credit — it inflated cash flow in "
                "the period received and must be excluded from normalised EBITDA. "
                "IRS RISK: The IRS announced aggressive ERC audit programs in 2023–2024. "
                "Many businesses that claimed ERC improperly face repayment obligations. "
                "Request ERC eligibility documentation (revenue decline test or shutdown test) "
                "and confirm the claim was properly supported. An IRS ERC audit in progress "
                "creates contingent liability for the buyer."
            ),
            "library_match": "COVID_ERC",
            "confidence_weight": 0.75,
        })

    if grant_txns:
        total = sum(t["amount"] for t in grant_txns if t["amount"] > 0)
        results.append({
            "signal_type": "ppp_distortion",
            "severity": "amber",
            "merchant": f"COVID GRANTS / RELIEF: ${total:,.0f} non-recurring",
            "amount": total,
            "transaction_date": grant_txns[0].get("transaction_date", ""),
            "description": (
                f"COVID-era grant and relief payments: ${total:,.0f}. "
                "Non-recurring — exclude from all normalised EBITDA calculations. "
                "RRF (Restaurant Revitalization Fund), SVOG (Shuttered Venue Operators Grant), "
                "and state/local COVID relief are one-time income items. "
                "Recast trailing financials to remove these items before applying multiples."
            ),
            "library_match": "COVID_GRANTS",
            "confidence_weight": 0.70,
        })

    return results
