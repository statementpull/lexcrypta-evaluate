"""Signal 23: Regulatory & Compliance Risk Detection.

Detects payments to government regulatory agencies, compliance consultants,
and enforcement bodies that indicate regulatory obligations transferring with
the business — or active regulatory scrutiny.

In M&A: regulatory violations, fines, and open investigations follow the
business, not the owner. An acquirer who fails to identify regulatory exposure
before closing inherits those obligations in full.

Key patterns:
  Environmental (EPA/DEQ): Hazardous waste, environmental cleanup, emissions
    violations — can create unlimited successor liability under CERCLA.
  OSHA / Workplace Safety: Open citations, willful violations, repeat offender
    status — new owner inherits open OSHA citations.
  DEA / FDA / FTC: Controlled substance, product safety, consumer protection
    — industry-specific regulatory exposure.
  Licensing & Permits: Failing to detect license renewal payments may signal
    the business operates on licenses that don't transfer automatically.
  Government Settlements: Payments to DOJ, state AGs, or regulatory bodies
    may indicate resolved OR ongoing enforcement actions.
  Professional Regulatory Bodies: State boards (medical, legal, engineering)
    — professional license issues follow the licensee, not the entity.

Sources:
- CERCLA Superfund successor liability (42 U.S.C. § 9607)
- OSHA successor employer doctrine (29 CFR Part 1977)
- FTC Act Section 5 — successor liability in deceptive practice settlements
- FDA 21 CFR — license transfer requirements

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict


EPA_KEYWORDS = [
    "EPA ", "ENVIRONMENTAL PROTECTION", "DEQ", "DEPARTMENT OF ENVIRONMENTAL",
    "ENVIRONMENTAL AGENCY", "AIR QUALITY", "HAZMAT", "HAZARDOUS WASTE",
    "SUPERFUND", "CERCLA", "ENVIRONMENTAL CLEANUP", "REMEDIATION",
    "STORMWATER", "NPDES", "RCRA",
]

OSHA_KEYWORDS = [
    "OSHA", "OCCUPATIONAL SAFETY", "WORKPLACE SAFETY", "SAFETY VIOLATION",
    "OSHA FINE", "OSHA PENALTY", "LABOR SAFETY", "MSHA",
]

FDA_KEYWORDS = [
    "FDA", "FOOD AND DRUG", "FOOD SAFETY", "DRUG ENFORCEMENT", "DEA REGISTRATION",
    "DEA FEE", "CONTROLLED SUBSTANCE", "PHARMACY BOARD", "DEA ",
]

FTC_DOJ_KEYWORDS = [
    "FTC ", "FEDERAL TRADE", "DOJ ", "DEPARTMENT OF JUSTICE",
    "CONSUMER PROTECTION", "ANTITRUST", "ATTORNEY GENERAL",
    "STATE AG ", "SETTLEMENT PAYMENT", "CONSENT DECREE",
]

LICENSING_KEYWORDS = [
    "LICENSE RENEWAL", "LICENSE FEE", "PERMIT FEE", "BUSINESS LICENSE",
    "PROFESSIONAL LICENSE", "STATE LICENSE", "LICENSE APPLICATION",
    "LIQUOR LICENSE", "CONTRACTOR LICENSE", "HEALTH PERMIT",
    "BUILDING PERMIT", "ZONING FEE", "CONDITIONAL USE",
]

GOVERNMENT_FINE_KEYWORDS = [
    "FINE PAYMENT", "PENALTY PAYMENT", "CITATION PAYMENT", "VIOLATION FEE",
    "REGULATORY FEE", "COMPLIANCE FEE", "ENFORCEMENT", "ADMINISTRATIVE FINE",
    "MUNICIPAL COURT", "COUNTY FINE", "STATE FINE",
]

COMPLIANCE_CONSULTANT_KEYWORDS = [
    "COMPLIANCE CONSULT", "REGULATORY CONSULT", "ENVIRONMENTAL CONSULT",
    "SAFETY CONSULT", "COMPLIANCE OFFICER", "REGULATORY AFFAIRS",
    "COMPLIANCE AUDIT", "ENVIRONMENTAL AUDIT", "SAFETY AUDIT",
]

WORKERS_COMP_KEYWORDS = [
    "WORKERS COMP", "WORKERS COMPENSATION", "WORK COMP", "WC PREMIUM",
    "ACCIDENT FUND", "STATE COMP FUND", "EMPLOYERS LIABILITY",
]

UNEMPLOYMENT_KEYWORDS = [
    "UNEMPLOYMENT", "UNEMPLOYMENT TAX", "FUTA", "SUTA", "UI TAX",
    "STATE UNEMPLOYMENT", "REEMPLOYMENT TAX",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []

    epa_txns, osha_txns, fda_txns, ftc_txns = [], [], [], []
    license_txns, fine_txns, consultant_txns = [], [], []
    wc_txns, ui_txns = [], []

    for t in transactions:
        merchant = t["merchant"].upper()
        amt = t["amount"]

        if any(kw in merchant for kw in EPA_KEYWORDS):
            epa_txns.append(t)
        if any(kw in merchant for kw in OSHA_KEYWORDS):
            osha_txns.append(t)
        if any(kw in merchant for kw in FDA_KEYWORDS):
            fda_txns.append(t)
        if any(kw in merchant for kw in FTC_DOJ_KEYWORDS):
            ftc_txns.append(t)
        if any(kw in merchant for kw in LICENSING_KEYWORDS):
            license_txns.append(t)
        if any(kw in merchant for kw in GOVERNMENT_FINE_KEYWORDS):
            fine_txns.append(t)
        if any(kw in merchant for kw in COMPLIANCE_CONSULTANT_KEYWORDS):
            consultant_txns.append(t)
        if any(kw in merchant for kw in WORKERS_COMP_KEYWORDS):
            wc_txns.append(t)
        if any(kw in merchant for kw in UNEMPLOYMENT_KEYWORDS):
            ui_txns.append(t)

    # ── 1. Environmental (EPA/DEQ) — highest risk, unlimited liability ────────
    if epa_txns:
        total = sum(abs(t["amount"]) for t in epa_txns)
        results.append({
            "signal_type": "regulatory_risk",
            "severity": "red",
            "merchant": f"ENVIRONMENTAL REGULATORY PAYMENTS: {len(epa_txns)} transactions",
            "amount": -total,
            "transaction_date": epa_txns[0].get("transaction_date", ""),
            "description": (
                f"Payments to environmental regulatory agencies (EPA/DEQ/environmental bodies): "
                f"{len(epa_txns)} transactions totalling ${total:,.0f}. "
                "CRITICAL: Environmental liability under CERCLA (Superfund) can be UNLIMITED "
                "and attaches to the business entity, not the owner — an acquirer inherits full "
                "environmental cleanup obligations regardless of who caused the contamination. "
                "Mandatory due diligence: (1) Phase I Environmental Site Assessment, "
                "(2) Phase II if Phase I identifies Recognized Environmental Conditions (RECs), "
                "(3) review EPA/state compliance history, open NOVs (Notices of Violation), "
                "(4) confirm no open enforcement actions or consent orders. "
                "Environmental indemnification in the purchase agreement is essential."
            ),
            "library_match": "REGULATORY_ENVIRONMENTAL",
            "confidence_weight": 0.85,
        })

    # ── 2. OSHA / Workplace Safety ─────────────────────────────────────────────
    if osha_txns:
        total = sum(abs(t["amount"]) for t in osha_txns)
        results.append({
            "signal_type": "regulatory_risk",
            "severity": "red",
            "merchant": f"OSHA / WORKPLACE SAFETY PAYMENTS: {len(osha_txns)} transactions",
            "amount": -total,
            "transaction_date": osha_txns[0].get("transaction_date", ""),
            "description": (
                f"OSHA or workplace safety payments: {len(osha_txns)} transactions totalling ${total:,.0f}. "
                "Under the OSHA successor employer doctrine, open OSHA citations and abatement "
                "orders survive an acquisition — the new owner must complete abatement and may "
                "face enhanced penalties for repeat violations. "
                "Verify: (1) OSHA inspection history and open citations, "
                "(2) whether any willful or repeat violation classifications are active, "
                "(3) workers' compensation claim history (3-year experience mod), "
                "(4) open personal injury claims against the business."
            ),
            "library_match": "REGULATORY_OSHA",
            "confidence_weight": 0.80,
        })

    # ── 3. FDA / DEA ──────────────────────────────────────────────────────────
    if fda_txns:
        total = sum(abs(t["amount"]) for t in fda_txns)
        results.append({
            "signal_type": "regulatory_risk",
            "severity": "red",
            "merchant": f"FDA / DEA REGULATORY PAYMENTS: {len(fda_txns)} transactions",
            "amount": -total,
            "transaction_date": fda_txns[0].get("transaction_date", ""),
            "description": (
                f"FDA or DEA regulatory payments: {len(fda_txns)} transactions totalling ${total:,.0f}. "
                "FDA-regulated businesses (food, pharma, medical devices) and DEA registrants "
                "(controlled substance handlers) require specific license transfers at acquisition. "
                "DEA registration is not automatically transferable — new owner must apply separately. "
                "FDA consent decrees, warning letters, and 483 observations transfer with the business. "
                "Verify: (1) all FDA/DEA registrations and their transferability, "
                "(2) open FDA warning letters or consent decrees, "
                "(3) DEA Schedule I–V registration status and inspection history."
            ),
            "library_match": "REGULATORY_FDA_DEA",
            "confidence_weight": 0.85,
        })

    # ── 4. FTC / DOJ / Settlement payments ───────────────────────────────────
    if ftc_txns:
        total = sum(abs(t["amount"]) for t in ftc_txns)
        results.append({
            "signal_type": "regulatory_risk",
            "severity": "red",
            "merchant": f"GOVERNMENT SETTLEMENT / ENFORCEMENT PAYMENTS: {len(ftc_txns)} transactions",
            "amount": -total,
            "transaction_date": ftc_txns[0].get("transaction_date", ""),
            "description": (
                f"Payments to FTC, DOJ, State AG, or consent decree obligations: "
                f"{len(ftc_txns)} transactions totalling ${total:,.0f}. "
                "Government settlements and consent decrees bind the business entity — "
                "an acquirer inherits ongoing compliance obligations and monitoring requirements. "
                "FTC Act Section 5 successor liability: buyers who knew or should have known "
                "of prior deceptive practices may be liable. "
                "Request: (1) full settlement agreement and compliance schedule, "
                "(2) confirmation of compliance monitoring status, "
                "(3) legal opinion on successor liability risk."
            ),
            "library_match": "REGULATORY_GOVERNMENT_SETTLEMENT",
            "confidence_weight": 0.90,
        })

    # ── 5. Government fines / penalty payments ───────────────────────────────
    if fine_txns:
        total = sum(abs(t["amount"]) for t in fine_txns)
        results.append({
            "signal_type": "regulatory_risk",
            "severity": "amber",
            "merchant": f"FINES & PENALTY PAYMENTS: {len(fine_txns)} transactions",
            "amount": -total,
            "transaction_date": fine_txns[0].get("transaction_date", ""),
            "description": (
                f"Government fine and penalty payments: {len(fine_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "Regulatory fines signal compliance failures. Recurring fines indicate "
                "a pattern rather than an isolated incident. "
                "Verify: (1) which agencies issued fines and for what violations, "
                "(2) whether underlying violations have been remediated, "
                "(3) whether any fines are still under appeal or payment plan."
            ),
            "library_match": "REGULATORY_FINES",
            "confidence_weight": 0.70,
        })

    # ── 6. License fees (transferability risk) ────────────────────────────────
    if license_txns:
        total = sum(abs(t["amount"]) for t in license_txns)
        results.append({
            "signal_type": "regulatory_risk",
            "severity": "amber",
            "merchant": f"LICENSE & PERMIT FEES: {len(license_txns)} transactions",
            "amount": -total,
            "transaction_date": license_txns[0].get("transaction_date", ""),
            "description": (
                f"Business license and permit fee payments: {len(license_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "Many business licenses are not automatically transferable on ownership change — "
                "buyer may need to reapply and face processing delays that could interrupt operations. "
                "Critical examples: liquor licenses (lengthy transfer process), contractor licenses "
                "(often require re-examination), professional licenses (non-transferable), "
                "healthcare facility licenses, food service permits. "
                "Inventory all licenses and permits with issuing authority to confirm transferability."
            ),
            "library_match": "REGULATORY_LICENSING",
            "confidence_weight": 0.65,
        })

    # ── 7. Compliance consultant spend ───────────────────────────────────────
    if consultant_txns:
        total = sum(abs(t["amount"]) for t in consultant_txns)
        if total > 5000:
            results.append({
                "signal_type": "regulatory_risk",
                "severity": "amber",
                "merchant": f"COMPLIANCE CONSULTANT SPEND: ${total:,.0f}",
                "amount": -total,
                "transaction_date": consultant_txns[0].get("transaction_date", ""),
                "description": (
                    f"Regulatory and compliance consultant payments: {len(consultant_txns)} transactions "
                    f"totalling ${total:,.0f}. "
                    "Heavy compliance consulting spend may indicate: "
                    "(1) active regulatory scrutiny requiring expert support, "
                    "(2) remediation of identified compliance gaps, "
                    "(3) preparation for regulatory audit or inspection. "
                    "Request scope of engagement letters — determine what compliance issues drove the spend."
                ),
                "library_match": None,
                "confidence_weight": 0.55,
            })

    # ── 8. Workers' comp — experience mod risk ────────────────────────────────
    if wc_txns:
        total = sum(abs(t["amount"]) for t in wc_txns)
        results.append({
            "signal_type": "regulatory_risk",
            "severity": "amber",
            "merchant": f"WORKERS' COMPENSATION: ${total:,.0f}",
            "amount": -total,
            "transaction_date": wc_txns[0].get("transaction_date", ""),
            "description": (
                f"Workers' compensation insurance payments: {len(wc_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "Workers' comp experience modification rate (EMR/Mod) transfers with the business — "
                "a high mod (above 1.0) signals above-average workplace injury history and "
                "results in premium surcharges that follow the business for 3 years. "
                "Request: (1) current EMR/Mod rating, (2) 3-year loss run, "
                "(3) open claims inventory. A mod above 1.25 may affect bid eligibility "
                "on government contracts and increase ongoing insurance costs materially."
            ),
            "library_match": "REGULATORY_WORKERS_COMP",
            "confidence_weight": 0.60,
        })

    return results
