"""Signal 25: Franchise & Royalty Risk.

Franchise agreements have specific change-of-control provisions that can
derail or significantly complicate an acquisition. Many franchisors:
  - Require written approval before ownership transfer
  - Charge transfer fees ($5,000–$50,000+ depending on brand)
  - Have first right of refusal to buy the unit themselves
  - Require new owner to complete training programs (6–12 weeks)
  - May deny transfer if the franchisee is in default on any obligation
  - Impose new franchise agreement terms at transfer (resetting royalty rates)

Royalty-based businesses (licensed technology, content, brand) face similar
issues — licensing agreements often have assignment restrictions.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

FRANCHISE_KEYWORDS = [
    "FRANCHISE FEE", "ROYALTY FEE", "ROYALTY PAYMENT", "BRAND FEE",
    "MARKETING FUND", "ADVERTISING FUND", "NATIONAL ADVERTISING",
    "AREA DEVELOPER", "FRANCHISEE FEE", "SYSTEM FUND",
]

KNOWN_FRANCHISORS = [
    "MCDONALDS", "SUBWAY", "7-ELEVEN", "DUNKIN", "BURGER KING",
    "DOMINOS", "PIZZA HUT", "KFC", "TACO BELL", "CHICK-FIL-A",
    "ANYTIME FITNESS", "SNAP FITNESS", "CURVES", "JAZZERCISE",
    "SERVPRO", "SERVICEMASTER", "JAN-PRO", "MOLLY MAID", "COVERALL",
    "SUPERCUTS", "GREAT CLIPS", "FANTASTIC SAMS",
    "CENTURY 21", "KELLER WILLIAMS", "RE/MAX", "COLDWELL BANKER",
    "KUMON", "SYLVAN LEARNING", "HUNTINGTON LEARNING",
    "UPS STORE", "MAILBOXES ETC", "PAK MAIL",
    "ORANGETHEORY", "F45", "PLANET FITNESS",
]

ROYALTY_KEYWORDS = [
    "ROYALTY", "LICENSE ROYALTY", "IP ROYALTY", "PATENT ROYALTY",
    "CONTENT LICENSE", "BRAND LICENSE", "FRANCHISE ROYALT",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    franchise_txns = []
    royalty_txns = []
    franchisor_name = None

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in FRANCHISE_KEYWORDS):
            franchise_txns.append(t)
        elif any(kw in merchant for kw in KNOWN_FRANCHISORS):
            franchise_txns.append(t)
            if not franchisor_name:
                for brand in KNOWN_FRANCHISORS:
                    if brand in merchant:
                        franchisor_name = brand.title()
                        break
        if any(kw in merchant for kw in ROYALTY_KEYWORDS):
            royalty_txns.append(t)

    if franchise_txns:
        total = sum(abs(t["amount"]) for t in franchise_txns)
        monthly_avg = total / max(len({t.get("transaction_date","")[:7] for t in franchise_txns}), 1)
        annual_est = monthly_avg * 12
        brand_note = f"Franchisor identified: {franchisor_name}. " if franchisor_name else ""

        results.append({
            "signal_type": "franchise_risk",
            "severity": "red",
            "merchant": f"FRANCHISE OBLIGATIONS: ${total:,.0f} detected | Est. ${annual_est:,.0f}/year",
            "amount": -total,
            "transaction_date": franchise_txns[0].get("transaction_date", ""),
            "description": (
                f"Franchise fee/royalty payments: {len(franchise_txns)} transactions totalling ${total:,.0f}. "
                f"{brand_note}"
                "CRITICAL acquisition implications: "
                "(1) Franchisor written approval is required for ownership transfer — process takes 60–120 days, "
                "(2) Transfer fee applies — typically $5,000–$50,000+ depending on brand, "
                "(3) Franchisor has first right of refusal to purchase the unit at your offer price, "
                "(4) New owner must complete franchisor training program before operating, "
                "(5) Transfer may trigger a new franchise agreement with updated royalty rates and terms, "
                "(6) Any existing defaults (royalty arrears, system standards violations) will block transfer. "
                "Obtain the full franchise agreement immediately. Engage a franchise attorney before LOI."
            ),
            "library_match": "FRANCHISE_TRANSFER_RISK",
            "confidence_weight": 0.90,
        })

    if royalty_txns:
        total = sum(abs(t["amount"]) for t in royalty_txns)
        results.append({
            "signal_type": "franchise_risk",
            "severity": "amber",
            "merchant": f"ROYALTY / LICENSE PAYMENTS: ${total:,.0f}",
            "amount": -total,
            "transaction_date": royalty_txns[0].get("transaction_date", ""),
            "description": (
                f"Royalty or license fee payments: {len(royalty_txns)} transactions totalling ${total:,.0f}. "
                "IP and content license agreements typically contain assignment restrictions — "
                "the business may not be able to continue using licensed IP after an ownership change "
                "without licensor consent. "
                "Verify: (1) whether each license agreement permits assignment, "
                "(2) whether licensor consent is required and what the consent process entails, "
                "(3) whether license rates can be renegotiated at transfer, "
                "(4) remaining license term and renewal rights."
            ),
            "library_match": "ROYALTY_LICENSE_RISK",
            "confidence_weight": 0.70,
        })

    return results
