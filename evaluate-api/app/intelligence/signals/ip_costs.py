"""Signal 30: Intellectual Property Costs & IP Risk.

IP payments reveal whether a business is building or maintaining proprietary
assets — or paying to use someone else's. Both matter in acquisition:

  Owned IP (patents, trademarks, copyrights): Must be properly registered,
    maintained, and assigned in the purchase agreement. IP not formally
    registered may be unenforceable. IP in the founder's personal name
    (not the company's) does not transfer with the business.

  Licensed IP (royalties paid, software licenses): Assignment restrictions
    may block the business from continuing to use licensed technology after
    a change of control. Missing consent = operational shutdown risk.

  IP maintenance lapses: Failure to pay maintenance fees results in IP
    abandonment — a business may believe it owns patents/trademarks that
    have actually lapsed.

  Trade secret protection: If the business's value is in trade secrets
    (formulas, processes, customer lists), verify NDAs, non-competes, and
    employment agreements are in place and enforceable.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

PATENT_KEYWORDS = [
    "USPTO", "US PATENT", "PATENT FILING", "PATENT FEE", "PATENT MAINTENANCE",
    "PATENT APPLICATION", "PATENT ATTORNEY", "PATENT AGENT",
    "EPO ", "WIPO ", "PCT APPLICATION", "PATENT ANNUITY",
]

TRADEMARK_KEYWORDS = [
    "TRADEMARK", "TRADE MARK", "TM FILING", "TM FEE",
    "TRADEMARK ATTORNEY", "TRADEMARK APPLICATION", "TRADEMARK RENEWAL",
    "SERVICE MARK", "BRAND REGISTRATION",
]

COPYRIGHT_KEYWORDS = [
    "COPYRIGHT", "COPYRIGHT REGISTRATION", "COPYRIGHT FEE",
    "DMCA", "PERFORMING RIGHTS", "ASCAP", "BMI", "SESAC",
    "SYNC LICENSE", "MECHANICAL LICENSE",
]

LICENSING_ROYALTY_KEYWORDS = [
    "ROYALTY PAYMENT", "LICENSE ROYALTY", "IP LICENSE", "PATENT LICENSE",
    "TECHNOLOGY LICENSE", "KNOW-HOW LICENSE", "FRANCHISE ROYALTY",
    "BRAND LICENSE FEE",
]

IP_ATTORNEY_KEYWORDS = [
    "IP ATTORNEY", "INTELLECTUAL PROPERTY", "IP LAW", "IP COUNSEL",
    "IP FIRM", "PATENT LAW", "TRADEMARK LAW",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    patent_txns, trademark_txns, copyright_txns = [], [], []
    royalty_txns, ip_attorney_txns = [], []

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in PATENT_KEYWORDS):
            patent_txns.append(t)
        if any(kw in merchant for kw in TRADEMARK_KEYWORDS):
            trademark_txns.append(t)
        if any(kw in merchant for kw in COPYRIGHT_KEYWORDS):
            copyright_txns.append(t)
        if any(kw in merchant for kw in LICENSING_ROYALTY_KEYWORDS):
            royalty_txns.append(t)
        if any(kw in merchant for kw in IP_ATTORNEY_KEYWORDS):
            ip_attorney_txns.append(t)

    all_ip = patent_txns + trademark_txns + copyright_txns + royalty_txns + ip_attorney_txns
    if not all_ip:
        return []

    # ── Owned IP (patents, trademarks) ────────────────────────────────────────
    owned_ip = patent_txns + trademark_txns + copyright_txns + ip_attorney_txns
    if owned_ip:
        total = sum(abs(t["amount"]) for t in owned_ip)
        ip_types = []
        if patent_txns: ip_types.append(f"Patents ({len(patent_txns)} txns, ${sum(abs(t['amount']) for t in patent_txns):,.0f})")
        if trademark_txns: ip_types.append(f"Trademarks ({len(trademark_txns)} txns, ${sum(abs(t['amount']) for t in trademark_txns):,.0f})")
        if copyright_txns: ip_types.append(f"Copyrights/Performing Rights (${sum(abs(t['amount']) for t in copyright_txns):,.0f})")

        results.append({
            "signal_type": "ip_costs",
            "severity": "amber",
            "merchant": f"INTELLECTUAL PROPERTY PORTFOLIO: ${total:,.0f} in IP maintenance",
            "amount": -total,
            "transaction_date": owned_ip[0].get("transaction_date", ""),
            "description": (
                f"IP maintenance and registration spend: ${total:,.0f}. "
                f"Types identified: {', '.join(ip_types) if ip_types else 'General IP costs'}. "
                "IP due diligence checklist: "
                "(1) Verify all patents, trademarks, and copyrights are registered in the COMPANY's "
                "name — not the founder's personal name (personal IP does not transfer with the business), "
                "(2) Confirm all IP maintenance fees are current — lapsed IP may be unenforceable, "
                "(3) Search USPTO/IP databases for all claimed IP and confirm ownership chain, "
                "(4) Verify no third-party IP infringement claims (freedom to operate opinion), "
                "(5) Confirm IP assignment agreements exist for all employee/contractor-created IP, "
                "(6) Include comprehensive IP assignment schedule in the purchase agreement."
            ),
            "library_match": "IP_OWNED_PORTFOLIO",
            "confidence_weight": 0.65,
        })

    # ── Licensed IP (royalties paid out) ─────────────────────────────────────
    if royalty_txns:
        total = sum(abs(t["amount"]) for t in royalty_txns)
        results.append({
            "signal_type": "ip_costs",
            "severity": "amber",
            "merchant": f"IP ROYALTIES PAID: ${total:,.0f} — licensed technology/content",
            "amount": -total,
            "transaction_date": royalty_txns[0].get("transaction_date", ""),
            "description": (
                f"Outbound royalty payments: {len(royalty_txns)} transactions totalling ${total:,.0f}. "
                "Licensed IP is critical operational infrastructure — if the license cannot be "
                "assigned to the buyer, the business may lose the right to operate key systems "
                "or use core technology on day one post-close. "
                "For each license: (1) confirm the license agreement permits assignment or "
                "requires licensor consent, (2) negotiate assignment consent pre-closing "
                "(not post-closing — licensor has leverage after close), "
                "(3) check for most-favoured-nation pricing clauses that may reset at transfer, "
                "(4) verify remaining term and renewal rights are adequate."
            ),
            "library_match": "IP_LICENSED_RISK",
            "confidence_weight": 0.70,
        })

    return results
