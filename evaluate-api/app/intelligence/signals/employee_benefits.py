"""Signal 28: Employee Benefits & HR Cost Analysis.

Employee benefits represent a significant hidden cost in SME acquisitions.
ERISA-qualified retirement plans (401k, SEP-IRA, SIMPLE IRA) create ongoing
obligations and potential liabilities that transfer to the acquirer.

Key risks:
  401(k) Plan: Fiduciary liability for plan administration; compliance failures
    create personal liability. Plans must be tested annually for discrimination.
    New owner may need to terminate or merge the plan — both have costs.
  Health Insurance: Rising premiums, COBRA obligations for terminated employees,
    ACA compliance (50+ employees requires sponsored coverage).
  PEO Agreements: Many SMEs use PEOs (Trinet, Insperity, ADP TotalSource).
    PEO co-employment terminates at acquisition — new setup required.
  Benefits as % of Payroll: Below-market benefits create hidden post-acquisition
    cost if buyer needs to improve packages to retain staff.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict

RETIREMENT_KEYWORDS = [
    "401K", "401(K)", "RETIREMENT CONTRIBUTION", "RETIREMENT PLAN",
    "SEP IRA", "SIMPLE IRA", "PROFIT SHARING", "PENSION",
    "RETIREMENT FUND", "EMPOWER RETIREMENT", "FIDELITY RETIREMENT",
    "VANGUARD RETIREMENT", "JOHN HANCOCK", "PRINCIPAL FINANCIAL",
    "TRANSAMERICA", "NATIONWIDE RETIREMENT", "MASS MUTUAL",
]

PEO_KEYWORDS = [
    "TRINET", "INSPERITY", "ADP TOTALSOURCE", "PAYCHEX PEO",
    "JUSTWORKS", "RIPPLING", "GUSTO PEO", "BAMBOOHR", "ZENEFITS",
    "OASIS", "COADVANTAGE", "QUESTCO", "EMPLOYER SOLUTIONS",
]

HEALTH_KEYWORDS = [
    "HEALTH INSURANCE", "MEDICAL INSURANCE", "GROUP HEALTH",
    "BLUE CROSS", "BLUE SHIELD", "BCBS", "AETNA", "CIGNA",
    "UNITED HEALTH", "HUMANA", "KAISER", "OSCAR HEALTH",
    "HEALTHCARE PREMIUM", "EMPLOYEE BENEFITS",
]

FSA_HSA_KEYWORDS = [
    "FSA ", "HSA ", "FLEXIBLE SPENDING", "HEALTH SAVINGS",
    "BENEFIT WALLET", "WAGEWORKS", "HEALTH EQUITY",
]

COBRA_KEYWORDS = [
    "COBRA", "COBRA PAYMENT", "CONTINUATION COVERAGE",
]

LIFE_DISABILITY_KEYWORDS = [
    "LIFE INSURANCE", "GROUP LIFE", "DISABILITY INS", "STD INS",
    "LTD INS", "LONG TERM DISABILITY", "SHORT TERM DISABILITY",
    "GUARDIAN LIFE", "UNUM", "METLIFE",
]

PAYROLL_KW = ["PAYROLL", "ADP PAYROLL", "PAYCHEX", "GUSTO PAYROLL",
              "INTUIT PAYROLL", "RIPPLING PAYROLL", "SALARY", "WAGES"]


def _sum_rows_payroll(pl_rows) -> float:
    if not pl_rows:
        return 0.0
    kws = ["PAYROLL", "SALARY", "WAGES", "LABOUR", "LABOR"]
    total = 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        if any(kw in acc for kw in kws):
            for key in ("ytd", "amount", "value", "this_month"):
                v = r.get(key)
                if v is not None:
                    try:
                        val = float(re.sub(r"[,$\s%]", "", str(v)))
                        if val != 0:
                            total += abs(val)
                            break
                    except (ValueError, TypeError):
                        pass
    return total


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    retirement_txns, peo_txns, health_txns = [], [], []
    fsa_txns, cobra_txns, disability_txns = [], [], []

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in RETIREMENT_KEYWORDS):
            retirement_txns.append(t)
        if any(kw in merchant for kw in PEO_KEYWORDS):
            peo_txns.append(t)
        if any(kw in merchant for kw in HEALTH_KEYWORDS):
            health_txns.append(t)
        if any(kw in merchant for kw in FSA_HSA_KEYWORDS):
            fsa_txns.append(t)
        if any(kw in merchant for kw in COBRA_KEYWORDS):
            cobra_txns.append(t)
        if any(kw in merchant for kw in LIFE_DISABILITY_KEYWORDS):
            disability_txns.append(t)

    if not any([retirement_txns, peo_txns, health_txns, cobra_txns, disability_txns]):
        return []

    payroll = _sum_rows_payroll(pl_rows)

    # ── 401(k) / Retirement plan ──────────────────────────────────────────────
    if retirement_txns:
        total = sum(abs(t["amount"]) for t in retirement_txns)
        contrib_rate = total / payroll if payroll > 0 else 0
        results.append({
            "signal_type": "employee_benefits",
            "severity": "amber",
            "merchant": f"RETIREMENT PLAN (401k/SEP): ${total:,.0f} contributions",
            "amount": -total,
            "transaction_date": retirement_txns[0].get("transaction_date", ""),
            "description": (
                f"Retirement plan contributions: {len(retirement_txns)} transactions totalling ${total:,.0f} "
                f"{'(' + str(round(contrib_rate*100,1)) + '% of payroll)' if payroll > 0 else ''}. "
                "ERISA plan obligations at acquisition: "
                "(1) Fiduciary liability for plan administration transfers to new owner — "
                "ensure plan is current on Form 5500 filings and non-discrimination testing, "
                "(2) New owner may continue, terminate, or freeze the plan — each option has costs, "
                "(3) Plan termination requires IRS filing and distribution to participants, "
                "(4) Unfunded pension obligations (defined benefit plans) become buyer's liability. "
                "Obtain most recent Form 5500 and plan compliance testing results."
            ),
            "library_match": "BENEFITS_RETIREMENT",
            "confidence_weight": 0.65,
        })

    # ── PEO agreement ─────────────────────────────────────────────────────────
    if peo_txns:
        total = sum(abs(t["amount"]) for t in peo_txns)
        results.append({
            "signal_type": "employee_benefits",
            "severity": "amber",
            "merchant": f"PEO CO-EMPLOYMENT ARRANGEMENT: ${total:,.0f}",
            "amount": -total,
            "transaction_date": peo_txns[0].get("transaction_date", ""),
            "description": (
                f"PEO (Professional Employer Organization) payments: {len(peo_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "PEO co-employment agreements terminate on change of ownership — "
                "the buyer must establish new payroll, benefits, and HR infrastructure from day one. "
                "Risks: (1) employees may lose benefits during transition gap, "
                "(2) workers' comp coverage under PEO terminates — new policy required immediately, "
                "(3) PEO transition typically takes 30–60 days — plan pre-closing, "
                "(4) PEO may have retained employment records, I-9s, and tax documents — "
                "ensure full data transfer is negotiated. "
                "Request full PEO agreement and termination provisions."
            ),
            "library_match": "BENEFITS_PEO",
            "confidence_weight": 0.70,
        })

    # ── COBRA ─────────────────────────────────────────────────────────────────
    if cobra_txns:
        total = sum(abs(t["amount"]) for t in cobra_txns)
        results.append({
            "signal_type": "employee_benefits",
            "severity": "amber",
            "merchant": f"COBRA PAYMENTS: ${total:,.0f} — recent terminations indicated",
            "amount": -total,
            "transaction_date": cobra_txns[0].get("transaction_date", ""),
            "description": (
                f"COBRA continuation coverage payments: {len(cobra_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "COBRA payments indicate recent employee terminations. "
                "Verify: (1) headcount trend — is the business shrinking?, "
                "(2) open COBRA election windows that create ongoing obligation post-closing, "
                "(3) whether terminations involved disputes (wrongful termination exposure)."
            ),
            "library_match": "BENEFITS_COBRA",
            "confidence_weight": 0.60,
        })

    # ── Benefits summary ──────────────────────────────────────────────────────
    all_benefits = retirement_txns + health_txns + fsa_txns + disability_txns
    if all_benefits and payroll > 0:
        total_benefits = sum(abs(t["amount"]) for t in all_benefits)
        benefits_ratio = total_benefits / payroll
        if benefits_ratio < 0.05:
            results.append({
                "signal_type": "employee_benefits",
                "severity": "amber",
                "merchant": f"BELOW-MARKET BENEFITS: {benefits_ratio:.0%} of payroll",
                "amount": 0,
                "transaction_date": "",
                "description": (
                    f"Identified benefits spend (${total_benefits:,.0f}) represents only "
                    f"{benefits_ratio:.0%} of payroll (${payroll:,.0f}). "
                    "Typical benefits cost 20–35% of payroll (health, retirement, disability, PTO). "
                    "Under-market benefits packages create hidden post-acquisition cost: "
                    "a new owner who improves benefits to retain staff will face higher operating costs "
                    "than the historical P&L shows. Adjust normalized EBITDA for benefit cost "
                    "normalisation before applying acquisition multiples."
                ),
                "library_match": "BENEFITS_BELOW_MARKET",
                "confidence_weight": 0.50,
            })

    return results
