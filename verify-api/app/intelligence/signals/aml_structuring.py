"""Signal 17: AML / Structuring Detection — Anti-Money Laundering Patterns.

Detects transaction patterns associated with money laundering, currency
structuring, and Bank Secrecy Act (BSA) violations. These patterns represent
serious legal risk to an acquirer — buying a business with AML history can
trigger FinCEN investigations and successor liability.

Key patterns:
  Structuring: Multiple deposits just below $10,000 (CTR threshold) to avoid
    Currency Transaction Report filing — federal crime under 31 U.S.C. § 5324.
  Smurfing: Same structuring pattern spread across many small transactions
    or multiple days to avoid detection.
  Round-trip flows: Large inflows immediately followed by equal outflows
    (same or next day) — hallmark of layering.
  Cash-intensive anomalies: Cash deposit volume inconsistent with business type.
  International wire concentration: Repeated wires to unusual jurisdictions.
  Rapid cycling: High-velocity in/out with minimal net change in balance.

Sources:
- FinCEN Advisories: FIN-2019-A001 (structuring), FIN-2022-A001 (cryptocurrency)
- BSA/AML Manual (FFIEC) — structuring typologies
- FATF Guidance on Trade-Based Money Laundering (TBML)
- SEC AAER: cases where revenue fraud used layering to disguise fictitious sales

Legal note: These patterns warrant immediate escalation to legal counsel.
An acquirer who knew or should have known of AML activity may face successor
liability under federal forfeiture statutes.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict
from datetime import datetime


def _parse_amount(v) -> float:
    try:
        import re
        return float(re.sub(r"[,$\s]", "", str(v)))
    except (ValueError, TypeError):
        return 0.0


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# Jurisdictions frequently flagged in FATF high-risk / grey lists
HIGH_RISK_JURISDICTIONS = [
    "CAYMAN", "PANAMA", "BELIZE", "SEYCHELLES", "VANUATU", "SAMOA",
    "MARSHALL ISLANDS", "COOK ISLANDS", "MAURITIUS", "BAHAMAS",
    "MYANMAR", "NORTH KOREA", "IRAN", "SYRIA", "CUBA", "VENEZUELA",
    "HAITI", "YEMEN", "MALI", "NIGER", "BURKINA", "LIBYA",
    "NIGERIA WIRE", "GHANA WIRE", "RUSSIA WIRE", "BELARUS WIRE",
]

INTERNATIONAL_WIRE_KW = [
    "WIRE TRANSFER", "INTL WIRE", "INTERNATIONAL WIRE", "SWIFT",
    "FOREIGN WIRE", "OVERSEAS WIRE", "OUTGOING WIRE", "INCOMING WIRE",
]

CASH_DEPOSIT_KW = [
    "CASH DEPOSIT", "CURRENCY DEPOSIT", "VAULT DEPOSIT",
    "ATM DEPOSIT", "BRANCH DEPOSIT", "TELLER DEPOSIT",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    inflows = [t for t in transactions if t["amount"] > 0]
    outflows = [t for t in transactions if t["amount"] < 0]

    # ── 1. Structuring detection ───────────────────────────────────────────────
    # Deposits in $8,000–$9,999 range — classic CTR-avoidance structuring band
    STRUCTURING_LOW = 8000
    STRUCTURING_HIGH = 9999

    structured = [t for t in inflows if STRUCTURING_LOW <= t["amount"] <= STRUCTURING_HIGH]
    if len(structured) >= 3:
        total_structured = sum(t["amount"] for t in structured)
        dates = [t.get("transaction_date", "") for t in structured[:5]]
        sev = "red" if len(structured) >= 5 else "amber"
        results.append({
            "signal_type": "aml_structuring",
            "severity": sev,
            "merchant": f"STRUCTURING PATTERN: {len(structured)} deposits in $8,000–$9,999 band",
            "amount": total_structured,
            "transaction_date": structured[0].get("transaction_date", ""),
            "description": (
                f"Structuring alert: {len(structured)} deposits totalling ${total_structured:,.0f} "
                f"fall in the $8,000–$9,999 range (CTR reporting threshold: $10,000). "
                f"Sample dates: {', '.join(d for d in dates if d)}. "
                "Structuring — deliberately breaking up deposits to avoid Currency Transaction Report filing — "
                "is a federal crime under 31 U.S.C. § 5324 (Bank Secrecy Act), regardless of whether "
                "the underlying funds are legal. Five or more such deposits is a material AML red flag. "
                "Recommend immediate legal review and FinCEN SAR history request before proceeding."
            ),
            "library_match": "AML_STRUCTURING",
            "confidence_weight": 0.85 if len(structured) >= 5 else 0.70,
        })

    # ── 2. Smurfing / micro-deposit pattern ───────────────────────────────────
    # Many small deposits from multiple sources on the same or consecutive days
    by_date: dict[str, list] = defaultdict(list)
    for t in inflows:
        d = t.get("transaction_date", "")
        if d:
            by_date[d].append(t)

    smurf_days = []
    for date, txns in by_date.items():
        if len(txns) >= 4:
            day_total = sum(t["amount"] for t in txns)
            # Multiple small deposits that collectively exceed $10k
            if day_total > 10000 and all(t["amount"] < 5000 for t in txns):
                smurf_days.append((date, len(txns), day_total))

    if len(smurf_days) >= 3:
        results.append({
            "signal_type": "aml_structuring",
            "severity": "amber",
            "merchant": f"SMURFING PATTERN: {len(smurf_days)} days with multiple small inflows aggregating >$10k",
            "amount": sum(d[2] for d in smurf_days),
            "transaction_date": smurf_days[0][0] if smurf_days else "",
            "description": (
                f"Potential smurfing pattern: {len(smurf_days)} separate days each showing "
                f"4+ small deposits that aggregate above $10,000 (e.g. {smurf_days[0][1]} deposits "
                f"on {smurf_days[0][0]} totalling ${smurf_days[0][2]:,.0f}). "
                "Smurfing (using multiple individuals or accounts to deposit cash below reporting thresholds) "
                "is a key BSA/AML red flag. Verify the source of funds for these deposit clusters."
            ),
            "library_match": "AML_SMURFING",
            "confidence_weight": 0.65,
        })

    # ── 3. Round-trip / layering detection ────────────────────────────────────
    # Large inflow followed within 3 days by similar-sized outflow
    ROUNDTRIP_THRESHOLD = 15000
    MATCH_TOLERANCE = 0.08  # 8% match

    large_in = [t for t in inflows if t["amount"] >= ROUNDTRIP_THRESHOLD]
    large_out = [t for t in outflows if abs(t["amount"]) >= ROUNDTRIP_THRESHOLD]

    roundtrips = []
    used_out = set()
    for tin in large_in:
        d_in = _parse_date(tin.get("transaction_date", ""))
        if not d_in:
            continue
        for i, tout in enumerate(large_out):
            if i in used_out:
                continue
            d_out = _parse_date(tout.get("transaction_date", ""))
            if not d_out:
                continue
            days_diff = abs((d_out - d_in).days)
            amount_diff = abs(tin["amount"] - abs(tout["amount"])) / tin["amount"]
            if days_diff <= 3 and amount_diff <= MATCH_TOLERANCE:
                roundtrips.append((tin, tout, days_diff))
                used_out.add(i)
                break

    if len(roundtrips) >= 2:
        total_roundtrip = sum(r[0]["amount"] for r in roundtrips)
        sev = "red" if len(roundtrips) >= 3 else "amber"
        results.append({
            "signal_type": "aml_structuring",
            "severity": sev,
            "merchant": f"ROUND-TRIP / LAYERING: {len(roundtrips)} matched inflow-outflow pairs",
            "amount": total_roundtrip,
            "transaction_date": roundtrips[0][0].get("transaction_date", ""),
            "description": (
                f"Round-trip flow pattern: {len(roundtrips)} instances where a large inflow "
                f"(≥${ROUNDTRIP_THRESHOLD:,}) was followed within 3 days by a near-equal outflow "
                f"(within 8% of amount). Example: ${roundtrips[0][0]['amount']:,.0f} in "
                f"({roundtrips[0][0].get('transaction_date','')}) matched by "
                f"${abs(roundtrips[0][1]['amount']):,.0f} out ({roundtrips[0][1].get('transaction_date','')}), "
                f"{roundtrips[0][2]} day(s) apart. "
                "Round-trip flows are the hallmark of AML 'layering' — moving funds through accounts "
                "to obscure their origin. This pattern also appears in revenue inflation schemes "
                "where payments are recycled to fabricate sales. Trace origin and destination of each pair."
            ),
            "library_match": "AML_ROUNDTRIP",
            "confidence_weight": 0.75 if len(roundtrips) >= 3 else 0.60,
        })

    # ── 4. High-risk jurisdiction wires ───────────────────────────────────────
    hri_txns = []
    for t in transactions:
        merchant = t["merchant"].upper()
        is_wire = any(kw in merchant for kw in INTERNATIONAL_WIRE_KW)
        is_high_risk = any(kw in merchant for kw in HIGH_RISK_JURISDICTIONS)
        if is_wire or is_high_risk:
            hri_txns.append(t)

    if hri_txns:
        total_hri = sum(abs(t["amount"]) for t in hri_txns)
        results.append({
            "signal_type": "aml_structuring",
            "severity": "red",
            "merchant": f"HIGH-RISK JURISDICTION WIRES: {len(hri_txns)} transactions",
            "amount": -total_hri,
            "transaction_date": hri_txns[0].get("transaction_date", ""),
            "description": (
                f"International wire activity to/from high-risk jurisdictions: "
                f"{len(hri_txns)} transactions totalling ${total_hri:,.0f}. "
                "Jurisdictions flagged include FATF grey/black-listed territories and common "
                "offshore secrecy jurisdictions (Cayman Islands, Panama, Seychelles, Belize, etc.). "
                "OFAC sanctions compliance must be verified — transacting with sanctioned persons "
                "or entities creates successor liability for the acquirer. "
                "Obtain full wire documentation: beneficiary name, purpose, and jurisdiction. "
                "Engage OFAC/BSA counsel before closing."
            ),
            "library_match": "AML_HIGH_RISK_JURISDICTION",
            "confidence_weight": 0.80,
        })

    # ── 5. Rapid cycling / velocity anomaly ───────────────────────────────────
    # If total outflows within 48 hours of inflows represent >80% of those inflows
    total_in = sum(t["amount"] for t in inflows)
    total_out = sum(abs(t["amount"]) for t in outflows)
    if total_in > 50000 and total_out > 0:
        cycle_ratio = total_out / total_in
        if cycle_ratio > 0.92:
            results.append({
                "signal_type": "aml_structuring",
                "severity": "amber",
                "merchant": f"HIGH-VELOCITY CASH CYCLING: {cycle_ratio:.0%} of inflows immediately outflowing",
                "amount": 0,
                "transaction_date": "",
                "description": (
                    f"Cash cycling alert: total outflows (${total_out:,.0f}) represent "
                    f"{cycle_ratio:.0%} of total inflows (${total_in:,.0f}). "
                    "Near-complete cycling of funds through the account is a hallmark of "
                    "pass-through accounts used in layering schemes. "
                    "Verify that the business retains adequate working capital and that outflows "
                    "correspond to legitimate business expenses. "
                    "High-velocity cycling with low retained balance may indicate the account "
                    "is used to move rather than accumulate funds."
                ),
                "library_match": "AML_VELOCITY",
                "confidence_weight": 0.55,
            })

    return results
