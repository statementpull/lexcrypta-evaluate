"""Signal 35: Government Contract Risk.

Federal and state government contracts contain change-of-control provisions
that can terminate contracts automatically on acquisition. Missing this can
eliminate the business's primary revenue stream on day one post-close.

Key risks:
  FAR Clause 52.215-19 (Federal): Requires contractor to notify contracting
    officer of ownership changes. Some contracts require novation (new agreement)
    — a lengthy process requiring government approval.
  SBA 8(a) / Small Business Set-Asides: Change of ownership can instantly
    disqualify the business from small business set-aside programs if the new
    owner exceeds size standards or does not qualify for the certification.
  GSA Schedule Contracts: Must be novated to new owner — can take 6–12 months.
  Classified contracts: Require facility and personnel security clearances
    that cannot be transferred — buyer must independently obtain clearances.
  State/local contracts: Similar provisions at state/municipal level.
  Grant-funded contracts: Federal grants (NIH, NSF, DOD SBIR) are generally
    non-transferable and terminate on PI/owner change.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

FEDERAL_CONTRACT_KW = [
    "DEPT OF DEFENSE", "DOD CONTRACT", "DEFENSE CONTRACT", "DARPA",
    "US ARMY", "US NAVY", "US AIR FORCE", "USAF", "US MARINES",
    "DEPT OF ENERGY", "DOE CONTRACT", "NASA CONTRACT",
    "DEPT OF HOMELAND", "DHS CONTRACT", "FEMA CONTRACT",
    "DEPT OF HEALTH", "HHS CONTRACT", "NIH CONTRACT", "CDC CONTRACT",
    "DEPT OF TRANSPORTATION", "DOT CONTRACT", "FAA CONTRACT",
    "GSA CONTRACT", "GSA SCHEDULE", "FEDERAL SUPPLY",
    "DFAS ", "DEFENSE FINANCE", "US TREASURY CONTRACT",
    "SBA CONTRACT", "SBIR", "STTR",
    "VETERANS AFFAIRS", "VA CONTRACT", "DVA ",
    "DEPT OF EDUCATION", "DOE GRANT",
    "ACH FEDERAL", "TREAS 310", "MISC PAY",
]

STATE_CONTRACT_KW = [
    "STATE CONTRACT", "COUNTY CONTRACT", "MUNICIPAL CONTRACT",
    "CITY CONTRACT", "STATE OF ", "COMMONWEALTH OF",
    "DEPT OF CORRECTIONS", "STATE HEALTH DEPT", "STATE TRANSPORTATION",
    "PUBLIC SCHOOL", "SCHOOL DISTRICT", "STATE UNIVERSITY",
]

GRANT_KW = [
    "GRANT PAYMENT", "FEDERAL GRANT", "STATE GRANT", "GRANT AWARD",
    "SBIR GRANT", "STTR GRANT", "NIH GRANT", "NSF GRANT",
    "DOE GRANT", "HHS GRANT", "ECONOMIC DEVELOPMENT GRANT",
    "COMMUNITY DEVELOPMENT", "CDBG", "EDA GRANT",
]

SECURITY_CLEARANCE_KW = [
    "DSS ", "DCSA ", "SECURITY CLEARANCE", "CLASSIFIED",
    "FACILITY CLEARANCE", "FCL ", "DISS ",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    federal_txns, state_txns, grant_txns, clearance_txns = [], [], [], []

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in FEDERAL_CONTRACT_KW):
            federal_txns.append(t)
        if any(kw in merchant for kw in STATE_CONTRACT_KW):
            state_txns.append(t)
        if any(kw in merchant for kw in GRANT_KW):
            grant_txns.append(t)
        if any(kw in merchant for kw in SECURITY_CLEARANCE_KW):
            clearance_txns.append(t)

    # ── Federal contracts ─────────────────────────────────────────────────────
    if federal_txns:
        total = sum(t["amount"] for t in federal_txns if t["amount"] > 0)
        results.append({
            "signal_type": "government_contract",
            "severity": "red",
            "merchant": f"FEDERAL CONTRACT REVENUE: {len(federal_txns)} government payments",
            "amount": total,
            "transaction_date": federal_txns[0].get("transaction_date", ""),
            "description": (
                f"Federal government contract payments identified: {len(federal_txns)} transactions, "
                f"${total:,.0f} inflows. "
                "CRITICAL acquisition risk — federal contracts are NOT automatically transferable: "
                "(1) FAR 52.215-19 requires written notification to contracting officer of any "
                "ownership change — failure to notify may trigger contract termination, "
                "(2) Novation Agreement required for contract assignment (FAR 42.12) — "
                "government must approve the transfer, typically taking 3–12 months, "
                "(3) SBA 8(a), HUBZone, SDVOSB, or WOSB certifications terminate immediately "
                "if new owner does not qualify — contracts under these programs become ineligible, "
                "(4) Past Performance records (CPARS/PPIRS) transfer with the contract but not always, "
                "(5) ITAR/EAR export-controlled contracts require new owner to register with State Dept. "
                "Engage a government contracts attorney before LOI. "
                "Obtain copies of all active contracts and modifications."
            ),
            "library_match": "GOVT_CONTRACT_FEDERAL",
            "confidence_weight": 0.90,
        })

    # ── Grants ────────────────────────────────────────────────────────────────
    if grant_txns:
        total = sum(t["amount"] for t in grant_txns if t["amount"] > 0)
        results.append({
            "signal_type": "government_contract",
            "severity": "red",
            "merchant": f"GRANT REVENUE: ${total:,.0f} — non-transferable funding",
            "amount": total,
            "transaction_date": grant_txns[0].get("transaction_date", ""),
            "description": (
                f"Grant and subsidy revenue: {len(grant_txns)} transactions totalling ${total:,.0f}. "
                "Federal grants (NIH, NSF, DOD SBIR/STTR, DOE) are awarded to a specific legal entity "
                "and PI (Principal Investigator) — they are generally NOT transferable to a new owner. "
                "An acquisition may trigger: (1) grant termination and required repayment of unexpended funds, "
                "(2) loss of SBIR eligibility if buyer exceeds 500 employee threshold, "
                "(3) agency approval requirements for ownership change. "
                "If grants represent material revenue, the business may be worth significantly less "
                "post-acquisition than during the grant period. "
                "Verify each grant's transferability with the program officer before proceeding."
            ),
            "library_match": "GOVT_GRANT_NONTRANSFERABLE",
            "confidence_weight": 0.85,
        })

    # ── State/local contracts ─────────────────────────────────────────────────
    if state_txns:
        total = sum(t["amount"] for t in state_txns if t["amount"] > 0)
        results.append({
            "signal_type": "government_contract",
            "severity": "amber",
            "merchant": f"STATE/LOCAL CONTRACT REVENUE: ${total:,.0f}",
            "amount": total,
            "transaction_date": state_txns[0].get("transaction_date", ""),
            "description": (
                f"State, county, or municipal government payments: {len(state_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "State and local government contracts vary widely in their change-of-control provisions. "
                "Many school district, healthcare, and social services contracts require re-procurement "
                "on ownership change — the new owner must compete for the contract again. "
                "Verify: (1) contract terms regarding assignment and change of control, "
                "(2) required bonds, insurance levels, or MBE/WBE certifications, "
                "(3) whether the contract was awarded on competitive bid or sole-source basis."
            ),
            "library_match": "GOVT_CONTRACT_STATE",
            "confidence_weight": 0.70,
        })

    # ── Security clearance ────────────────────────────────────────────────────
    if clearance_txns:
        results.append({
            "signal_type": "government_contract",
            "severity": "red",
            "merchant": "SECURITY CLEARANCE / CLASSIFIED ACTIVITY DETECTED",
            "amount": 0,
            "transaction_date": clearance_txns[0].get("transaction_date", ""),
            "description": (
                "Security clearance-related payments detected. "
                "Classified contracts require both a Facility Clearance (FCL) and Personnel Security "
                "Clearances (PSC) for key staff. Neither is automatically transferable: "
                "(1) FCL requires DCSA (Defense Counterintelligence and Security Agency) re-investigation "
                "of the new owner — can take 6–24 months, "
                "(2) Foreign ownership, control, or influence (FOCI) by a non-US acquirer may "
                "prevent obtaining or maintaining a clearance entirely, "
                "(3) During the gap period, the business may not be able to perform classified work. "
                "Engage a cleared facility security officer (FSO) and FOCI counsel before any LOI "
                "on a business with classified contracts."
            ),
            "library_match": "GOVT_SECURITY_CLEARANCE",
            "confidence_weight": 0.90,
        })

    return results
