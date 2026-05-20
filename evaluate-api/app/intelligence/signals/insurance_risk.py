"""Signal 24: Insurance Coverage Risk & Adequacy Assessment.

Analyses insurance payment patterns to detect under-insurance, coverage gaps,
and abnormal insurance costs — all of which create risk for an acquirer.

An under-insured business exposes the buyer to uninsured losses from day one.
Insurance costs also reveal information about the business's risk profile:
high premiums indicate past claims; missing insurance indicates the business
is operating exposed.

What we detect:
  Coverage Gaps: Absence of expected insurance types for the business category
    (e.g., a contractor with no general liability, a healthcare business with
    no professional liability / E&O).
  Workers' Comp Gap: Any business with employees must carry workers' comp —
    absence is illegal in most states and creates unlimited personal liability.
  Premium Spikes: Sudden increase in insurance premiums signals a claims event
    or underwriter reassessment of risk profile.
  Excessive Premiums: Premium level disproportionate to revenue may indicate
    a high-risk business or active claims history.
  D&O / E&O Absence: Businesses selling to private equity or institutional
    buyers typically require D&O tail coverage for pre-closing acts.

Industry benchmarks (Insurance Information Institute, IRMI):
  General Liability: 0.3–1.5% of revenue (varies by industry)
  Workers' Comp: 1–5% of payroll (varies by EMR/industry)
  Professional Liability / E&O: 0.5–2% of revenue (service businesses)
  Commercial Property: based on asset value and location
  Cyber Liability: $1,000–$5,000+/year (becoming standard)

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict


GL_KEYWORDS = [
    "GENERAL LIABILITY", "GL INSURANCE", "CGL", "COMMERCIAL GENERAL",
    "LIABILITY INS", "LIABILITY PREMIUM", "BUSINESS LIABILITY",
]

WC_KEYWORDS = [
    "WORKERS COMP", "WORKERS COMPENSATION", "WORK COMP", "WC PREMIUM",
    "ACCIDENT FUND", "STATE COMP", "EMPLOYERS LIABILITY",
]

PL_EO_KEYWORDS = [
    "PROFESSIONAL LIABILITY", "ERRORS OMISSIONS", "E&O", "E&O PREMIUM",
    "MALPRACTICE", "PROFESSIONAL INDEMNITY", "E & O", "ERRORS AND OMISSIONS",
]

DO_KEYWORDS = [
    "DIRECTORS OFFICERS", "D&O", "D & O PREMIUM", "D&O INSURANCE",
    "MANAGEMENT LIABILITY", "EXECUTIVE LIABILITY",
]

CYBER_KEYWORDS = [
    "CYBER LIABILITY", "CYBER INSURANCE", "DATA BREACH", "CYBER POLICY",
    "TECHNOLOGY E&O", "NETWORK SECURITY", "INFORMATION SECURITY INS",
]

PROPERTY_KEYWORDS = [
    "PROPERTY INSURANCE", "COMMERCIAL PROPERTY", "BUILDING INSURANCE",
    "PROPERTY PREMIUM", "BOP", "BUSINESSOWNERS POLICY", "BUSINESS OWNERS POLICY",
    "FIRE INSURANCE", "PROPERTY & CASUALTY",
]

VEHICLE_KEYWORDS = [
    "AUTO INSURANCE", "COMMERCIAL AUTO", "FLEET INSURANCE", "VEHICLE INSURANCE",
    "TRUCK INSURANCE", "BUSINESS AUTO", "COMMERCIAL VEHICLE",
]

UMBRELLA_KEYWORDS = [
    "UMBRELLA", "EXCESS LIABILITY", "UMBRELLA POLICY",
]

HEALTH_INS_KEYWORDS = [
    "HEALTH INSURANCE", "MEDICAL INSURANCE", "GROUP HEALTH", "BLUE CROSS",
    "BLUE SHIELD", "BCBS", "AETNA", "CIGNA", "UNITED HEALTH", "HUMANA",
    "HEALTHCARE PREMIUM", "EMPLOYEE BENEFITS",
]

ALL_INSURANCE_KEYWORDS = (
    GL_KEYWORDS + WC_KEYWORDS + PL_EO_KEYWORDS + DO_KEYWORDS + CYBER_KEYWORDS +
    PROPERTY_KEYWORDS + VEHICLE_KEYWORDS + UMBRELLA_KEYWORDS + HEALTH_INS_KEYWORDS
)


def _sum_rows_revenue(pl_rows) -> float:
    if not pl_rows:
        return 0.0
    rev_kw = ["REVENUE", "SALES", "INCOME", "NET SALES", "GROSS REVENUE"]
    total = 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        desc = str(r.get("description", "")).upper()
        if any(kw in acc or kw in desc for kw in rev_kw):
            for key in ("ytd", "amount", "value", "this_month"):
                v = r.get(key)
                if v is not None:
                    try:
                        val = float(re.sub(r"[,$\s%]", "", str(v)))
                        if val != 0:
                            total += val
                            break
                    except (ValueError, TypeError):
                        pass
    return total


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    outflows = [t for t in transactions if t["amount"] < 0]

    # Collect insurance payments by type
    gl_txns, wc_txns, pl_txns, do_txns, cyber_txns = [], [], [], [], []
    property_txns, vehicle_txns, umbrella_txns, health_txns = [], [], [], []
    all_insurance_txns = []

    for t in outflows:
        merchant = t["merchant"].upper()
        matched = False
        if any(kw in merchant for kw in GL_KEYWORDS):
            gl_txns.append(t); matched = True
        if any(kw in merchant for kw in WC_KEYWORDS):
            wc_txns.append(t); matched = True
        if any(kw in merchant for kw in PL_EO_KEYWORDS):
            pl_txns.append(t); matched = True
        if any(kw in merchant for kw in DO_KEYWORDS):
            do_txns.append(t); matched = True
        if any(kw in merchant for kw in CYBER_KEYWORDS):
            cyber_txns.append(t); matched = True
        if any(kw in merchant for kw in PROPERTY_KEYWORDS):
            property_txns.append(t); matched = True
        if any(kw in merchant for kw in VEHICLE_KEYWORDS):
            vehicle_txns.append(t); matched = True
        if any(kw in merchant for kw in UMBRELLA_KEYWORDS):
            umbrella_txns.append(t); matched = True
        if any(kw in merchant for kw in HEALTH_INS_KEYWORDS):
            health_txns.append(t); matched = True
        if matched:
            all_insurance_txns.append(t)

    # Also catch generic insurance payments
    for t in outflows:
        if t not in all_insurance_txns:
            m = t["merchant"].upper()
            if "INSURANCE" in m or " INS " in m or m.endswith(" INS"):
                all_insurance_txns.append(t)

    if not all_insurance_txns:
        # No insurance payments at all is itself a red flag for any business
        total_outflow = sum(abs(t["amount"]) for t in outflows)
        if total_outflow > 50000:
            results.append({
                "signal_type": "insurance_risk",
                "severity": "amber",
                "merchant": "INSURANCE GAP: No insurance payments detected in bank data",
                "amount": 0,
                "transaction_date": "",
                "description": (
                    "No identifiable insurance premium payments detected in bank statement data. "
                    "This may indicate: (1) insurance is paid via credit card (not visible in bank), "
                    "(2) the business is operating without adequate coverage, "
                    "(3) insurance payments are made from a different account. "
                    "Every operating business should carry at minimum: general liability, "
                    "workers' compensation (if employees), and commercial property (if owned/leased). "
                    "Request full current certificate of insurance (COI) with limits and effective dates. "
                    "Confirm all policies will remain in force or be replaced at closing."
                ),
                "library_match": "INSURANCE_NONE_DETECTED",
                "confidence_weight": 0.45,
            })
        return results

    total_insurance = sum(abs(t["amount"]) for t in all_insurance_txns)
    revenue = _sum_rows_revenue(pl_rows) if pl_rows else 0
    total_outflow = sum(abs(t["amount"]) for t in outflows)

    # ── Coverage present — build summary ─────────────────────────────────────
    covered = []
    if gl_txns: covered.append(f"General Liability (${sum(abs(t['amount']) for t in gl_txns):,.0f})")
    if wc_txns: covered.append(f"Workers' Comp (${sum(abs(t['amount']) for t in wc_txns):,.0f})")
    if pl_txns: covered.append(f"Professional Liability/E&O (${sum(abs(t['amount']) for t in pl_txns):,.0f})")
    if do_txns: covered.append(f"D&O (${sum(abs(t['amount']) for t in do_txns):,.0f})")
    if cyber_txns: covered.append(f"Cyber (${sum(abs(t['amount']) for t in cyber_txns):,.0f})")
    if property_txns: covered.append(f"Commercial Property (${sum(abs(t['amount']) for t in property_txns):,.0f})")
    if vehicle_txns: covered.append(f"Commercial Auto (${sum(abs(t['amount']) for t in vehicle_txns):,.0f})")
    if health_txns: covered.append(f"Health/Benefits (${sum(abs(t['amount']) for t in health_txns):,.0f})")

    missing = []
    if not gl_txns: missing.append("General Liability")
    if not wc_txns: missing.append("Workers' Compensation")
    if not property_txns and not vehicle_txns: missing.append("Property/Casualty")
    if not cyber_txns: missing.append("Cyber Liability (increasingly required)")

    severity = "amber"
    if not wc_txns and total_outflow > 30000:
        severity = "red"  # Missing WC when there's clearly a payroll is a legal violation

    # ── Premium spike detection ───────────────────────────────────────────────
    spike_note = ""
    if len(all_insurance_txns) >= 2:
        amounts = sorted([abs(t["amount"]) for t in all_insurance_txns], reverse=True)
        if amounts[0] > amounts[1] * 2.0:
            spike_note = (
                f" Premium spike detected: largest payment ${amounts[0]:,.0f} vs "
                f"typical ${amounts[1]:,.0f} — may indicate mid-term premium adjustment "
                "after a claims event or policy restructuring."
            )

    # ── Premium as % of revenue ───────────────────────────────────────────────
    rev_note = ""
    if revenue > 0:
        ins_pct = total_insurance / revenue
        if ins_pct > 0.05:
            rev_note = (
                f" Insurance premiums represent {ins_pct:.1%} of revenue — "
                "above the 3–5% threshold for most industries. "
                "High premiums may reflect above-average claims history or a high-risk business classification."
            )

    description = (
        f"Total insurance spend: ${total_insurance:,.0f}. "
        f"Coverage identified: {', '.join(covered) if covered else 'None clearly identified'}. "
        f"Potential gaps: {', '.join(missing) if missing else 'None detected — coverage appears adequate'}. "
        f"{spike_note}{rev_note}"
        "At closing: (1) request full certificates of insurance with policy limits, "
        "(2) confirm all policies are assignable or that replacement coverage is arranged, "
        "(3) request 5-year claims history from insurer, "
        "(4) verify workers' comp experience mod (EMR) — values above 1.0 indicate above-average claims, "
        "(5) consider D&O tail coverage for pre-closing acts of prior management."
    )

    results.append({
        "signal_type": "insurance_risk",
        "severity": severity,
        "merchant": f"INSURANCE COVERAGE ASSESSMENT: ${total_insurance:,.0f} total premiums | {len(missing)} potential gaps",
        "amount": -total_insurance,
        "transaction_date": all_insurance_txns[0].get("transaction_date", "") if all_insurance_txns else "",
        "description": description[:1500],
        "library_match": "INSURANCE_ASSESSMENT",
        "confidence_weight": 0.60,
    })

    return results
