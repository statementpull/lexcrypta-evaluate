"""Signal 57: Entity Map — Related Party Detection & Structure Risk.

In small business acquisitions, the legal structure around the operating entity
is often more complex than it appears. Sellers commonly operate through:
  - Multiple LLCs with intercompany transactions
  - Trusts holding real estate leased back to the business
  - Family members on payroll or as shareholders
  - Management companies extracting profit before the P&L

These structures are not illegal — but they create acquisition risks:
  - The entity being sold may depend on contracts with related entities
  - Profit may be routed out of the acquirable entity
  - Real estate may be separately owned, creating a forced lease obligation post-close
  - The business may not be separable from the seller's other interests

This signal analyses transaction data for entity name patterns that suggest
related parties, management fees, intercompany transfers, and lease-back structures.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""

import re
from collections import defaultdict

MANAGEMENT_FEE_KW = [
    "MANAGEMENT FEE", "MGMT FEE", "MANAGEMENT SERVICES", "ADMIN FEE",
    "ADMINISTRATION FEE", "CONSULTING FEE", "ADVISORY FEE", "DIRECTOR FEE",
]

INTERCOMPANY_KW = [
    "INTERCOMPANY", "INTER-COMPANY", "RELATED PARTY", "RELATED-PARTY",
    "SISTER COMPANY", "PARENT COMPANY", "HOLDING", "HOLDCO", "SHAREHOLDER LOAN",
    "DIRECTOR LOAN", "LOAN FROM", "LOAN TO",
]

TRUST_KW = [
    " TRUST", " TRUSTEE", "FAMILY TRUST", "DISCRETIONARY TRUST",
    " SETTLEMENT", "BARE TRUST",
]

LEASEBACK_KW = [
    "LEASE", "RENTAL", "RENT", "PROPERTY LEASE", "EQUIPMENT LEASE",
    "VEHICLE LEASE", "FACILITY LEASE",
]

LLC_ENTITY_KW = [
    " LLC", " L.L.C", " INC", " CORP", " PTY LTD", " LTD", " COMPANY",
    " CO.", " HOLDINGS", " GROUP", " ENTERPRISES", " SOLUTIONS",
]


def _upper(s):
    return (s or "").upper()


def _is_entity_name(name: str) -> bool:
    n = name.upper()
    return any(kw in n for kw in LLC_ENTITY_KW) or any(kw in n for kw in TRUST_KW)


def run(transactions: list[dict], pl_rows: list[dict] | None = None,
        loader=None, supplementary: dict | None = None) -> list[dict]:
    if not transactions:
        return []

    results = []

    # ── Management fee / consulting fee extraction ─────────────────────────
    mgmt_fee_txns = [
        t for t in transactions
        if any(kw in _upper(t.get("description", "")) or kw in _upper(t.get("merchant", ""))
               for kw in MANAGEMENT_FEE_KW)
    ]
    if mgmt_fee_txns:
        total_fees = sum(abs(t.get("amount", 0)) for t in mgmt_fee_txns)
        payees = list({t.get("merchant", "Unknown") for t in mgmt_fee_txns})
        results.append({
            "signal_type": "entity_map",
            "severity": "red",
            "merchant": f"MANAGEMENT FEES: ${total_fees:,.0f} paid to {len(payees)} payee(s)",
            "amount": -total_fees,
            "transaction_date": mgmt_fee_txns[0].get("transaction_date", ""),
            "description": (
                f"Management or consulting fees totalling ${total_fees:,.0f} detected "
                f"across {len(mgmt_fee_txns)} transactions. "
                f"Payees: {', '.join(payees[:5])}. "
                "Management fees are a common profit-extraction mechanism in owner-operated businesses. "
                "In an acquisition, these fees must be unwound — but the services they represent "
                "may need to be replaced at market cost. "
                "Buyer risk: (1) are these fees to related parties (seller's other entities or family)? "
                "(2) are the underlying services (e.g. IT, accounting, HR) currently provided by "
                "the related entity — and will the buyer need to source them elsewhere? "
                "(3) verify whether these fees are included in the seller's EBITDA addback. "
                "If so, confirm the replacement cost estimate is realistic, not zero."
            ),
            "library_match": "ENTITY_MANAGEMENT_FEES",
            "confidence_weight": 0.85,
        })

    # ── Intercompany transfers ─────────────────────────────────────────────
    interco_txns = [
        t for t in transactions
        if any(kw in _upper(t.get("description", "")) or kw in _upper(t.get("merchant", ""))
               for kw in INTERCOMPANY_KW)
    ]
    if interco_txns:
        total_interco = sum(abs(t.get("amount", 0)) for t in interco_txns)
        entities = list({t.get("merchant", "Unknown") for t in interco_txns})
        results.append({
            "signal_type": "entity_map",
            "severity": "red",
            "merchant": f"INTERCOMPANY TRANSACTIONS: ${total_interco:,.0f} with related entities",
            "amount": -total_interco,
            "transaction_date": interco_txns[0].get("transaction_date", ""),
            "description": (
                f"Intercompany or related-party transactions totalling ${total_interco:,.0f} "
                f"detected across {len(interco_txns)} transactions. "
                f"Entities involved: {', '.join(entities[:5])}. "
                "Intercompany transactions can mask true business performance — "
                "costs can be shifted out (inflating profit) or revenue can be shifted in "
                "(inflating top line) via intercompany pricing. "
                "Buyer risk: (1) obtain a full related-party transaction schedule for the past 3 years, "
                "(2) verify that intercompany pricing was at arm's length, "
                "(3) confirm whether any of these entities own assets used by the business "
                "(real property, equipment, IP) that the buyer needs to acquire or lease separately, "
                "(4) require all intercompany balances to be settled before close."
            ),
            "library_match": "ENTITY_INTERCOMPANY",
            "confidence_weight": 0.85,
        })

    # ── Trust structures ───────────────────────────────────────────────────
    trust_txns = [
        t for t in transactions
        if any(kw in _upper(t.get("merchant", "")) or kw in _upper(t.get("description", ""))
               for kw in TRUST_KW)
    ]
    if trust_txns:
        trust_amt = sum(abs(t.get("amount", 0)) for t in trust_txns)
        trust_names = list({t.get("merchant", "Unknown") for t in trust_txns})
        results.append({
            "signal_type": "entity_map",
            "severity": "amber",
            "merchant": f"TRUST INVOLVEMENT: {len(trust_names)} trust(s) in transaction history",
            "amount": -trust_amt,
            "transaction_date": trust_txns[0].get("transaction_date", ""),
            "description": (
                f"Transactions with trust entities detected: {', '.join(trust_names[:5])}. "
                f"Total flow: ${trust_amt:,.0f}. "
                "Trusts in the seller's structure may hold: "
                "(1) real estate that the business operates from — verify lease terms and whether "
                "the lease survives an ownership change, "
                "(2) intellectual property licensed to the operating company, "
                "(3) equity in the company itself — confirm who controls the trust and "
                "whether they are a party to the purchase agreement. "
                "Trust-held assets are not automatically included in a business sale — "
                "verify the precise scope of what is being acquired."
            ),
            "library_match": "ENTITY_TRUST_STRUCTURE",
            "confidence_weight": 0.75,
        })

    # ── Lease / rent payments to potential related entities ────────────────
    lease_txns = [
        t for t in transactions
        if any(kw in _upper(t.get("description", "")) for kw in LEASEBACK_KW)
        and _is_entity_name(t.get("merchant", ""))
    ]
    if lease_txns:
        lease_groups = defaultdict(list)
        for t in lease_txns:
            lease_groups[t.get("merchant", "Unknown")].append(t)

        for landlord, txns in list(lease_groups.items())[:3]:
            total_rent = sum(abs(t.get("amount", 0)) for t in txns)
            months = len({t.get("transaction_date", "")[:7] for t in txns if t.get("transaction_date")})
            results.append({
                "signal_type": "entity_map",
                "severity": "amber",
                "merchant": f"LEASEBACK RISK: Rent to entity '{landlord}' — ${total_rent:,.0f}",
                "amount": -total_rent,
                "transaction_date": txns[0].get("transaction_date", ""),
                "description": (
                    f"Lease/rental payments to '{landlord}' totalling ${total_rent:,.0f} "
                    f"over {months} months. "
                    "Payments to a company entity (rather than an individual or national landlord) "
                    "may indicate a sale-leaseback arrangement where the seller owns the property "
                    "through a related entity. "
                    "Buyer risk: (1) if the seller owns the landlord entity, this is a related-party "
                    "lease — verify it was at market rate and that it contains standard assignment provisions, "
                    "(2) confirm the lease term and renewal rights available to the buyer post-close, "
                    "(3) if the property is essential to operations, assess whether it should be "
                    "acquired as part of the transaction rather than leased."
                ),
                "library_match": "ENTITY_LEASEBACK_RISK",
                "confidence_weight": 0.70,
            })

    # ── Multiple distinct entity payees from bank transactions ────────────
    all_merchants = [t.get("merchant", "") for t in transactions if t.get("merchant")]
    entity_merchants = list({m for m in all_merchants if _is_entity_name(m)})

    if len(entity_merchants) > 10:
        results.append({
            "signal_type": "entity_map",
            "severity": "amber",
            "merchant": f"COMPLEX PAYEE NETWORK: {len(entity_merchants)} distinct company payees",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"{len(entity_merchants)} distinct company entities appear in transaction history "
                f"as payees. This is a complex supplier/entity network — "
                "some of these may be related parties, nominee entities, or shell structures. "
                "Request a complete related-party declaration from the seller as part of the "
                "due diligence information memorandum. "
                "Any entity connected to the seller personally (by ownership, directorship, "
                "or family relationship) must be disclosed and all transactions verified as arm's length."
            ),
            "library_match": "ENTITY_COMPLEX_NETWORK",
            "confidence_weight": 0.55,
        })

    return results
