"""Signal 52: UCC Lien & Secured Creditor Analysis.

Uniform Commercial Code (UCC-1) financing statements are public records
filed with the Secretary of State whenever a creditor takes a security
interest in business assets. Every UCC lien is a claim that stands between
the buyer and clean title to business assets.

In an asset acquisition, every active UCC lien against the seller's assets
must be identified and terminated (or assumed) before closing. In a stock
acquisition, all liens transfer automatically — the buyer inherits every
secured obligation.

Critical UCC patterns:
  Blanket lien: A single UCC covering "all assets", "all personal property",
    or "all assets now owned or hereafter acquired" gives the secured party
    first claim on everything the business owns. Lender must consent to sale
    or be paid off at closing. Banks routinely file blanket liens for operating
    lines of credit — these often survive even after the line is paid down.
  SBA / government lien: SBA EIDL loans >$25k have collateral securing them.
    The SBA files a UCC blanket lien. At acquisition: the EIDL loan triggers
    a change-of-control notification requirement. SBA must approve or the loan
    goes into default. Outstanding EIDL balance reduces net acquisition value.
  Floorplan lender lien: NextGear, AFC, DealerTrack, and Manheim Finance hold
    security interests in all financed inventory. At acquisition, the floorplan
    lender must be paid out from closing proceeds — failure to do so means they
    can repossess the inventory post-close.
  Intellectual property collateral: If patents, trademarks, or software are
    listed as collateral, the lender's consent is required for IP assignment.
    This can block or delay the transfer of the business's most valuable assets.
  Multiple secured creditors: Multiple active UCCs create a priority lien
    stack. The senior lienholder gets paid first — junior creditors may be
    impaired if asset values decline. Buyer needs a full payoff waterfall.
  Future advances clause: Lenders often include "future advances" language,
    meaning the UCC secures not just the original loan but any future credit
    extended under the same facility. The outstanding balance may exceed
    what appears in historical financial statements.
  Lapsed UCC: UCC-1 filings expire after 5 years unless renewed with a
    UCC-3 continuation statement. A recently lapsed UCC may indicate a
    paid-off creditor — or an administrative failure by a creditor who
    still has an outstanding claim.
  Amended UCC: UCC-3 amendments indicate the original filing was modified —
    collateral may have been added or changed. Request the full amendment
    history.

Action required for every active UCC:
  1. Identify the secured party and obtain current payoff statement
  2. Confirm whether the loan/facility is still outstanding
  3. Calculate payoff amount and include in enterprise value bridge
  4. Obtain release/termination statement (UCC-3) at closing
  5. For SBA: notify SBA of change of control, obtain SBA approval

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""

BLANKET_LIEN_KW = [
    "ALL ASSETS", "ALL PERSONAL PROPERTY", "ALL INVENTORY",
    "ALL ACCOUNTS", "ALL EQUIPMENT", "ALL RECEIVABLES",
    "SUBSTANTIALLY ALL", "ALL NOW OWNED OR HEREAFTER",
    "ALL PROPERTY", "BLANKET LIEN",
]

SBA_SECURED_KW = [
    "SMALL BUSINESS ADMINISTRATION", "U.S. SMALL BUSINESS",
    "SBA ", "UNITED STATES OF AMERICA", "US SBA",
]

FLOORPLAN_SECURED_KW = [
    "NEXTGEAR CAPITAL", "AFC ", "AUTOMOTIVE FINANCE CORP",
    "DEALERTRACK", "MANHEIM FINANCE", "FLOOR PLAN",
    "FLOORPLAN FINANCING", "WESTLAKE FLOORING",
]

IP_COLLATERAL_KW = [
    "PATENT", "TRADEMARK", "COPYRIGHT", "INTELLECTUAL PROPERTY",
    "SOFTWARE", "TRADE SECRET", "LICENSE RIGHTS", "IP RIGHTS",
    "DOMAIN NAME",
]

FUTURE_ADVANCES_KW = [
    "FUTURE ADVANCES", "FUTURE EXTENSIONS", "FUTURE LOANS",
    "ALL OBLIGATIONS", "NOW OR HEREAFTER",
]

REAL_PROPERTY_KW = [
    "FIXTURE FILING", "REAL PROPERTY", "REAL ESTATE",
    "LAND", "BUILDING", "MORTGAGE",
]

EQUIPMENT_KW = [
    "EQUIPMENT", "MACHINERY", "VEHICLE", "TRUCK", "FORKLIFT",
    "COMPUTER", "MEDICAL EQUIPMENT",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None,
        loader=None, ucc_records: list[dict] | None = None) -> list[dict]:
    if not ucc_records:
        return []

    results = []
    active = [r for r in ucc_records if r.get("status", "active").lower() in ("active", "amended")]
    lapsed = [r for r in ucc_records if r.get("status", "").lower() == "lapsed"]
    terminated = [r for r in ucc_records if r.get("status", "").lower() == "terminated"]

    if not active and not lapsed:
        if terminated:
            results.append({
                "signal_type": "ucc_analysis",
                "severity": "green",
                "merchant": f"UCC CLEAR: {len(terminated)} terminated filing(s) — no active liens",
                "amount": 0,
                "transaction_date": "",
                "description": (
                    f"{len(terminated)} UCC filing(s) found — all terminated. "
                    "No active secured creditor claims on business assets. "
                    "Confirm terminations were properly filed with the Secretary of State "
                    "and obtain copies of all UCC-3 termination statements. "
                    "Run a final UCC search within 3 business days of closing to confirm "
                    "no new filings were made during the transaction period."
                ),
                "library_match": "UCC_CLEAR",
                "confidence_weight": 0.90,
            })
        return results

    # ── Multiple active UCCs — lien stack complexity ───────────────────────
    if len(active) >= 3:
        secured_parties = [r.get("secured_party", "Unknown") for r in active]
        stated_amounts = sum(r.get("amount_stated", 0) or 0 for r in active)
        results.append({
            "signal_type": "ucc_analysis",
            "severity": "red",
            "merchant": f"COMPLEX LIEN STACK: {len(active)} active UCC filings",
            "amount": -stated_amounts if stated_amounts else 0,
            "transaction_date": "",
            "description": (
                f"{len(active)} active UCC-1 financing statements found. "
                f"Secured parties: {', '.join(secured_parties[:5])}{'...' if len(secured_parties) > 5 else ''}. "
                "Multiple active liens create a priority waterfall: secured parties are paid "
                "in order of filing date, not loan size. "
                "Required actions: "
                "(1) Obtain a current payoff statement from EVERY secured party, "
                "(2) Build a lien priority waterfall — first filer gets paid first, "
                "(3) Confirm which liens attach to specific assets vs blanket, "
                "(4) Obtain UCC-3 termination or payoff letters before closing, "
                "(5) Run a bring-down UCC search 3 days before closing to catch new filings. "
                "Include all payoffs in the closing settlement statement."
            ),
            "library_match": "UCC_COMPLEX_LIEN_STACK",
            "confidence_weight": 0.95,
        })
    elif len(active) > 0:
        # Summarise active filings
        secured_parties = [r.get("secured_party", "Unknown") for r in active]
        stated_amounts = sum(r.get("amount_stated", 0) or 0 for r in active)
        results.append({
            "signal_type": "ucc_analysis",
            "severity": "amber",
            "merchant": f"ACTIVE UCC: {len(active)} filing(s) — secured interests on business assets",
            "amount": -stated_amounts if stated_amounts else 0,
            "transaction_date": "",
            "description": (
                f"{len(active)} active UCC-1 filing(s). "
                f"Secured party: {', '.join(secured_parties)}. "
                "Active UCC filings represent secured creditor claims on business assets. "
                "Each must be resolved before or at closing: "
                "obtain a payoff statement, pay at closing from proceeds, "
                "and receive a UCC-3 termination filing from the secured party. "
                "Confirm the outstanding loan balance (which may differ from stated UCC amount)."
            ),
            "library_match": "UCC_ACTIVE_FILING",
            "confidence_weight": 0.90,
        })

    # ── Individual filing analysis ─────────────────────────────────────────
    for r in active:
        collateral = (r.get("collateral_description") or "").upper()
        secured = (r.get("secured_party") or "").upper()
        filing_num = r.get("filing_number", "")
        state = r.get("state", "")
        amount = r.get("amount_stated") or 0

        # Blanket lien
        if any(kw in collateral for kw in BLANKET_LIEN_KW):
            results.append({
                "signal_type": "ucc_analysis",
                "severity": "red",
                "merchant": f"BLANKET LIEN: {r.get('secured_party', 'Unknown')[:60]}",
                "amount": -amount if amount else 0,
                "transaction_date": r.get("filing_date", ""),
                "description": (
                    f"Blanket UCC lien filed by {r.get('secured_party', 'Unknown')} "
                    f"(Filing #{filing_num}, {state}). "
                    f"Collateral: {r.get('collateral_description', 'All assets')[:200]}. "
                    "A blanket lien gives the secured party a security interest in ALL business "
                    "assets — accounts receivable, inventory, equipment, IP, and goodwill. "
                    "The buyer cannot take clean title to any business asset without either: "
                    "(a) paying off the secured party at closing, or "
                    "(b) obtaining the secured party's written consent to the sale. "
                    "Obtain a current payoff statement immediately and include in closing waterfall."
                ),
                "library_match": "UCC_BLANKET_LIEN",
                "confidence_weight": 0.95,
            })

        # SBA lien
        if any(kw in secured for kw in SBA_SECURED_KW):
            results.append({
                "signal_type": "ucc_analysis",
                "severity": "red",
                "merchant": f"SBA LIEN: {r.get('secured_party', 'SBA')} — change-of-control clause",
                "amount": -amount if amount else 0,
                "transaction_date": r.get("filing_date", ""),
                "description": (
                    f"SBA (Small Business Administration) UCC lien (Filing #{filing_num}, {state}). "
                    "SBA loans (EIDL, SBA 7(a), SBA 504) contain a CHANGE-OF-CONTROL clause: "
                    "the borrower must notify the SBA of any ownership change and obtain SBA approval. "
                    "Failure to notify is a default under the loan agreement. "
                    "Required actions: "
                    "(1) Obtain current SBA loan payoff from SBA portal (pay.gov), "
                    "(2) Determine if the SBA loan will be paid off at closing or assumed, "
                    "(3) If assuming: submit SBA assumption request — process takes 60–90 days, "
                    "(4) SBA loans >$500k require additional SBA consent for transfer. "
                    "Do not close without SBA payoff confirmation or written assumption approval."
                ),
                "library_match": "UCC_SBA_LIEN",
                "confidence_weight": 0.95,
            })

        # Floorplan lender
        if any(kw in secured for kw in FLOORPLAN_SECURED_KW):
            results.append({
                "signal_type": "ucc_analysis",
                "severity": "red",
                "merchant": f"FLOORPLAN LIEN: {r.get('secured_party', 'Floorplan Lender')[:60]}",
                "amount": -amount if amount else 0,
                "transaction_date": r.get("filing_date", ""),
                "description": (
                    f"Inventory floorplan lender UCC (Filing #{filing_num}, {state}): "
                    f"{r.get('secured_party', 'Unknown')}. "
                    "The floorplan lender holds a security interest in all financed inventory. "
                    "CRITICAL: The floorplan lender MUST be paid in full from closing proceeds. "
                    "If the business is sold without paying off the floorplan lender, "
                    "the lender can repossess the inventory post-closing — leaving the buyer "
                    "with empty shelves and no recourse against the seller. "
                    "Obtain a current floorplan balance (not the UCC amount — it may be a blanket), "
                    "include payoff in the closing statement, and obtain a UCC-3 termination at closing."
                ),
                "library_match": "UCC_FLOORPLAN_LIEN",
                "confidence_weight": 0.95,
            })

        # IP collateral
        if any(kw in collateral for kw in IP_COLLATERAL_KW):
            results.append({
                "signal_type": "ucc_analysis",
                "severity": "red",
                "merchant": f"IP COLLATERAL: {r.get('secured_party', 'Unknown')[:50]} has lien on IP",
                "amount": 0,
                "transaction_date": r.get("filing_date", ""),
                "description": (
                    f"Intellectual property listed as UCC collateral "
                    f"(Filing #{filing_num} by {r.get('secured_party', 'Unknown')}). "
                    f"Collateral: {r.get('collateral_description', '')[:200]}. "
                    "When IP is pledged as collateral, the secured party's consent is required "
                    "to assign or transfer any IP rights — including in an asset sale. "
                    "A lender who holds a security interest in patents or trademarks can block "
                    "the IP assignment that is typically the core value of the acquisition. "
                    "Required: written consent from the secured party to IP assignment, "
                    "AND payoff and UCC-3 termination before IP assignment is recorded."
                ),
                "library_match": "UCC_IP_COLLATERAL",
                "confidence_weight": 0.90,
            })

        # Future advances
        if any(kw in collateral for kw in FUTURE_ADVANCES_KW):
            results.append({
                "signal_type": "ucc_analysis",
                "severity": "amber",
                "merchant": f"FUTURE ADVANCES: {r.get('secured_party', 'Unknown')[:50]}",
                "amount": 0,
                "transaction_date": r.get("filing_date", ""),
                "description": (
                    f"UCC filing includes 'future advances' language (Filing #{filing_num}). "
                    "A future advances clause means the UCC secures not just the original loan "
                    "but any future credit extended by the same lender under the same facility. "
                    "The payoff amount may be higher than any single loan balance shown in "
                    "financial statements — request a complete account statement showing "
                    "ALL obligations secured by this UCC before closing."
                ),
                "library_match": "UCC_FUTURE_ADVANCES",
                "confidence_weight": 0.75,
            })

        # Real property fixture filing
        if any(kw in collateral for kw in REAL_PROPERTY_KW):
            results.append({
                "signal_type": "ucc_analysis",
                "severity": "amber",
                "merchant": f"FIXTURE/REAL PROPERTY: {r.get('secured_party', 'Unknown')[:50]}",
                "amount": 0,
                "transaction_date": r.get("filing_date", ""),
                "description": (
                    f"UCC filing references real property or fixture filing (Filing #{filing_num}). "
                    "A fixture filing attaches to equipment that is affixed to real property — "
                    "this lien may appear in both the UCC index AND the real estate title search. "
                    "If the acquisition is a personal property (asset) deal, confirm whether "
                    "any fixtures are included, and if so, whether the real property lender's "
                    "consent is required for their removal or transfer."
                ),
                "library_match": "UCC_FIXTURE_FILING",
                "confidence_weight": 0.70,
            })

    # ── Lapsed UCCs ────────────────────────────────────────────────────────
    if lapsed:
        lapsed_parties = [r.get("secured_party", "Unknown") for r in lapsed]
        results.append({
            "signal_type": "ucc_analysis",
            "severity": "amber",
            "merchant": f"LAPSED UCC: {len(lapsed)} expired filing(s) — verify payoff",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"{len(lapsed)} UCC filing(s) have lapsed (expired without renewal): "
                f"{', '.join(lapsed_parties[:3])}{'...' if len(lapsed_parties) > 3 else ''}. "
                "A lapsed UCC-1 loses its priority position but does NOT automatically mean "
                "the underlying debt is paid. The creditor still has an unsecured claim if "
                "the loan is outstanding. "
                "Verify: (1) is the underlying loan actually paid off? "
                "(2) if paid off — request a payoff confirmation letter from the creditor, "
                "(3) if not paid off — the creditor may refile a new UCC, reverting to "
                "a junior priority position — this is a negotiating opportunity."
            ),
            "library_match": "UCC_LAPSED_FILING",
            "confidence_weight": 0.70,
        })

    return results
