"""Signal 40: Pension & Defined Benefit Plan Obligations.

Defined benefit (DB) pension plans are one of the most dangerous hidden
liabilities in M&A. Unlike defined contribution plans (401k), where the
employer's obligation ends with the contribution, a DB plan obligates the
employer to pay a specified benefit regardless of investment performance.

The funding gap (unfunded pension liability) = PBO - Plan Assets.
In a stock deal, this gap transfers entirely to the buyer.
In an asset deal, PBGC can still pursue successor liability for DB plans.

Key risks:
  Underfunding: If plan assets < pension benefit obligation (PBO), the buyer
    faces an immediate balance sheet liability.
  PBGC premiums: Rising PBGC flat-rate and variable-rate premiums signal
    an underfunded or at-risk plan.
  Withdrawal liability: If the business participates in a multiemployer
    (union) pension plan, withdrawal on sale triggers a cash payment
    that can equal years of contributions.
  Frozen plans: Even frozen DB plans retain obligations for past service —
    actuarial assumptions changes can swing the liability significantly.

Sources:
- ERISA Title IV — PBGC insurance and termination rules
- PBGC Technical Update 22-1 — variable rate premium calculation
- IRC Section 415 — benefit limits
- Multiemployer Pension Reform Act (MPRA) — withdrawal liability rules

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

PENSION_KW = [
    "PENSION FUND", "PENSION PLAN", "DEFINED BENEFIT", "DB PLAN",
    "PENSION CONTRIBUTION", "PENSION PREMIUM",
    "PBGC", "PENSION BENEFIT GUARANTY",
    "PENSION ANNUITY", "RETIREMENT ANNUITY",
]

MULTIEMPLOYER_KW = [
    "UNION PENSION", "MULTIEMPLOYER", "MULTI-EMPLOYER",
    "TEAMSTERS PENSION", "UFCW PENSION", "IBEW PENSION",
    "SEIU PENSION", "UAW PENSION", "PENSION WITHDRAWAL",
    "WITHDRAWAL LIABILITY", "MASS WITHDRAWAL",
]

ACTUARIAL_KW = [
    "ACTUARIAL", "ACTUARY", "ACTUARIAL STUDY", "PENSION ACTUARIAL",
    "VALUATION ACTUARY", "ENROLLED ACTUARY",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    pension_txns, multiemployer_txns, actuarial_txns = [], [], []

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in PENSION_KW):
            pension_txns.append(t)
        if any(kw in merchant for kw in MULTIEMPLOYER_KW):
            multiemployer_txns.append(t)
        if any(kw in merchant for kw in ACTUARIAL_KW):
            actuarial_txns.append(t)

    # ── Defined benefit pension plan ──────────────────────────────────────────
    if pension_txns:
        total = sum(abs(t["amount"]) for t in pension_txns)
        sev = "red"
        results.append({
            "signal_type": "pension_risk",
            "severity": sev,
            "merchant": f"DEFINED BENEFIT PENSION PLAN: ${total:,.0f} in contributions/premiums",
            "amount": -total,
            "transaction_date": pension_txns[0].get("transaction_date", ""),
            "description": (
                f"Defined benefit pension contributions or PBGC premiums: "
                f"{len(pension_txns)} transactions totalling ${total:,.0f}. "
                "DB pension plans represent UNLIMITED potential liability — the employer "
                "must fund the plan regardless of investment performance. "
                "CRITICAL due diligence: "
                "(1) Obtain most recent actuarial valuation report — specifically the "
                "Pension Benefit Obligation (PBO) and Plan Assets to calculate funding gap, "
                "(2) Confirm PBGC premium filings are current (flat-rate + variable rate), "
                "(3) Assess whether the plan is 'at-risk' under IRC 430 — triggers accelerated funding, "
                "(4) In a stock acquisition, the full unfunded liability transfers to buyer — "
                "negotiate a purchase price reduction for any funding gap, "
                "(5) Asset deal: PBGC has successor liability authority — consult ERISA counsel, "
                "(6) Consider requiring plan termination before closing (standard distress termination "
                "requires PBGC approval and may trigger an immediate cash contribution)."
            ),
            "library_match": "PENSION_DEFINED_BENEFIT",
            "confidence_weight": 0.90,
        })

    # ── Multiemployer / union pension ─────────────────────────────────────────
    if multiemployer_txns:
        total = sum(abs(t["amount"]) for t in multiemployer_txns)
        results.append({
            "signal_type": "pension_risk",
            "severity": "red",
            "merchant": f"MULTIEMPLOYER PENSION: ${total:,.0f} — withdrawal liability risk",
            "amount": -total,
            "transaction_date": multiemployer_txns[0].get("transaction_date", ""),
            "description": (
                f"Multiemployer (union) pension plan contributions: "
                f"{len(multiemployer_txns)} transactions totalling ${total:,.0f}. "
                "WITHDRAWAL LIABILITY is a critical acquisition risk unique to multiemployer plans: "
                "(1) If the acquisition results in a 'complete withdrawal' from the plan "
                "(ownership change + cessation of covered work), a withdrawal liability assessment "
                "is triggered — this can equal 10–20x annual contributions, "
                "(2) The MPPAA imposes withdrawal liability even in asset deals, "
                "(3) 'Partial withdrawal' also triggers liability if covered hours decline >70%, "
                "(4) The plan's funded status determines the liability amount — "
                "many Teamsters and UFCW plans are critically underfunded. "
                "Request the plan's Zone Status (Green/Yellow/Red) from the plan administrator "
                "and obtain a withdrawal liability estimate before closing."
            ),
            "library_match": "PENSION_MULTIEMPLOYER",
            "confidence_weight": 0.90,
        })

    # ── Actuarial engagement ──────────────────────────────────────────────────
    if actuarial_txns and not pension_txns:
        total = sum(abs(t["amount"]) for t in actuarial_txns)
        results.append({
            "signal_type": "pension_risk",
            "severity": "amber",
            "merchant": f"ACTUARIAL SERVICES: ${total:,.0f}",
            "amount": -total,
            "transaction_date": actuarial_txns[0].get("transaction_date", ""),
            "description": (
                f"Actuarial service payments: ${total:,.0f}. "
                "Actuarial engagements indicate the business has pension, retiree health, "
                "or other post-employment benefit (OPEB) obligations requiring professional valuation. "
                "Obtain the most recent actuarial report and assess the nature and magnitude "
                "of the underlying obligation before proceeding."
            ),
            "library_match": "PENSION_ACTUARIAL",
            "confidence_weight": 0.65,
        })

    return results
