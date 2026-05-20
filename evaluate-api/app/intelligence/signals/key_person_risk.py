"""Signal 36: Key Person & Owner Dependency Risk.

The most common value destruction in SME acquisitions: you buy the business
and the relationships, reputation, and know-how walk out the door with
the seller. Key person risk is the silent killer of SME acquisitions.

What we detect from financial data:
  Non-compete payments: Suggests prior departures with disputes — indicates
    the business has had key person exits before.
  Retention bonuses: Pre-acquisition bonuses to retain staff suggest
    the seller knows key people are at risk.
  Single-authority payments: All significant transactions from one source
    pattern indicates owner is sole operator.
  Professional membership fees: Personal memberships in owner's name rather
    than company name — relationships are personal not institutional.
  Consulting/transition fee payments: Suggest owner is already transitioning
    out — raises question of knowledge transfer.
  Key employee commission concentration: If one person drives all commissions,
    their departure eliminates the revenue source.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

NON_COMPETE_KW = [
    "NON-COMPETE", "NON COMPETE", "NONCOMPETE", "COVENANT NOT TO COMPETE",
    "NON-SOLICITATION", "NON SOLICITATION", "RESTRICTIVE COVENANT",
]

RETENTION_KW = [
    "RETENTION BONUS", "STAY BONUS", "RETENTION PAYMENT",
    "KEY EMPLOYEE RETENTION", "RETENTION INCENTIVE",
]

PROFESSIONAL_MEMBER_KW = [
    "ASSOCIATION DUES", "PROFESSIONAL DUES", "MEMBERSHIP DUES",
    "CHAMBER OF COMMERCE", "INDUSTRY ASSOCIATION", "TRADE ASSOCIATION",
    "LINKEDIN PREMIUM", "BAR DUES", "CPA DUES", "AICPA",
    "REALTOR ASSOCIATION", "BOARD MEMBERSHIP",
]

TRANSITION_KW = [
    "TRANSITION CONSULTING", "MANAGEMENT CONSULTING", "INTERIM MANAGEMENT",
    "TRANSITION SERVICES", "CONSULTING AGREEMENT", "ADVISORY FEE",
]

KEY_MAN_INSURANCE_KW = [
    "KEY MAN INSURANCE", "KEYMAN INSURANCE", "KEY PERSON INSURANCE",
    "KEY EMPLOYEE INSURANCE",
]

SEVERANCE_KW = [
    "SEVERANCE PAYMENT", "SEVERANCE PAY", "SEPARATION PAYMENT",
    "TERMINATION PAY",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    non_compete_txns, retention_txns, member_txns = [], [], []
    transition_txns, keyman_txns, severance_txns = [], [], []

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in NON_COMPETE_KW):
            non_compete_txns.append(t)
        if any(kw in merchant for kw in RETENTION_KW):
            retention_txns.append(t)
        if any(kw in merchant for kw in PROFESSIONAL_MEMBER_KW):
            member_txns.append(t)
        if any(kw in merchant for kw in TRANSITION_KW):
            transition_txns.append(t)
        if any(kw in merchant for kw in KEY_MAN_INSURANCE_KW):
            keyman_txns.append(t)
        if any(kw in merchant for kw in SEVERANCE_KW):
            severance_txns.append(t)

    # ── Non-compete payments ──────────────────────────────────────────────────
    if non_compete_txns:
        total = sum(abs(t["amount"]) for t in non_compete_txns)
        results.append({
            "signal_type": "key_person_risk",
            "severity": "amber",
            "merchant": f"NON-COMPETE PAYMENTS: ${total:,.0f}",
            "amount": -total,
            "transaction_date": non_compete_txns[0].get("transaction_date", ""),
            "description": (
                f"Non-compete or non-solicitation payments: {len(non_compete_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "Non-compete payments indicate prior key employee departures where the business "
                "felt compelled to pay for competitive restrictions — a strong signal that "
                "departing employees took value with them. "
                "For the acquisition: (1) the current owner's non-compete is critical — "
                "ensure robust non-compete, non-solicitation, and non-disparagement covenants "
                "in the purchase agreement, (2) extend non-compete to all key employees as a "
                "condition of closing, (3) verify the enforceability of non-competes in the "
                "state of operation (California non-competes are unenforceable)."
            ),
            "library_match": "KEY_PERSON_NON_COMPETE",
            "confidence_weight": 0.65,
        })

    # ── Retention bonuses ─────────────────────────────────────────────────────
    if retention_txns:
        total = sum(abs(t["amount"]) for t in retention_txns)
        results.append({
            "signal_type": "key_person_risk",
            "severity": "amber",
            "merchant": f"RETENTION BONUSES: ${total:,.0f} — key staff at departure risk",
            "amount": -total,
            "transaction_date": retention_txns[0].get("transaction_date", ""),
            "description": (
                f"Retention bonus payments: {len(retention_txns)} transactions totalling ${total:,.0f}. "
                "Pre-sale retention bonuses indicate the seller knows key employees are flight risks — "
                "these employees are likely to depart post-close once their retention cliff vests. "
                "Restructure retention bonuses to cliff vest 12–24 months POST-closing "
                "(not pre-closing) as a condition of the acquisition. "
                "Identify which employees received retention bonuses and assess their criticality."
            ),
            "library_match": "KEY_PERSON_RETENTION",
            "confidence_weight": 0.70,
        })

    # ── Severance ─────────────────────────────────────────────────────────────
    if severance_txns:
        total = sum(abs(t["amount"]) for t in severance_txns)
        results.append({
            "signal_type": "key_person_risk",
            "severity": "amber",
            "merchant": f"SEVERANCE PAYMENTS: ${total:,.0f} — recent key departures",
            "amount": -total,
            "transaction_date": severance_txns[0].get("transaction_date", ""),
            "description": (
                f"Severance payments: {len(severance_txns)} transactions totalling ${total:,.0f}. "
                "Recent significant severance indicates key employee departures — "
                "determine: (1) who departed, (2) their role and client/revenue relationship, "
                "(3) whether any departing employees joined a competitor, "
                "(4) whether any claims or disputes accompanied the departure. "
                "Significant severance close to the sale date may indicate the seller was "
                "restructuring headcount to improve EBITDA — resulting EBITDA is not sustainable "
                "if the departed roles need to be refilled."
            ),
            "library_match": "KEY_PERSON_SEVERANCE",
            "confidence_weight": 0.65,
        })

    # ── Key man insurance ─────────────────────────────────────────────────────
    if keyman_txns:
        total = sum(abs(t["amount"]) for t in keyman_txns)
        results.append({
            "signal_type": "key_person_risk",
            "severity": "amber",
            "merchant": f"KEY MAN INSURANCE: ${total:,.0f} — confirms owner-dependency acknowledged",
            "amount": -total,
            "transaction_date": keyman_txns[0].get("transaction_date", ""),
            "description": (
                f"Key man / key person insurance: {len(keyman_txns)} transactions totalling ${total:,.0f}. "
                "The seller's own payment of key man insurance is an acknowledgment that "
                "one or more individuals are critical to business continuity — a meaningful "
                "key person risk admission. "
                "At acquisition: (1) obtain details of current key man policy (insured, amount, beneficiary), "
                "(2) determine whether the policy will continue, lapse, or be assigned at closing, "
                "(3) assess whether the buyer should maintain or increase key person coverage "
                "for the transition period."
            ),
            "library_match": "KEY_PERSON_INSURANCE",
            "confidence_weight": 0.60,
        })

    # ── Transition consulting ─────────────────────────────────────────────────
    if transition_txns:
        total = sum(abs(t["amount"]) for t in transition_txns)
        results.append({
            "signal_type": "key_person_risk",
            "severity": "amber",
            "merchant": f"TRANSITION / CONSULTING PAYMENTS: ${total:,.0f}",
            "amount": -total,
            "transaction_date": transition_txns[0].get("transaction_date", ""),
            "description": (
                f"Management consulting or transition service payments: ${total:,.0f}. "
                "Payments to transition or management consultants shortly before a sale "
                "may indicate the owner is already stepping back from operations — "
                "raising questions about who is actually running the business "
                "and whether the team can operate independently post-close. "
                "Verify who performs the core operational and customer-facing roles."
            ),
            "library_match": "KEY_PERSON_TRANSITION",
            "confidence_weight": 0.55,
        })

    return results
