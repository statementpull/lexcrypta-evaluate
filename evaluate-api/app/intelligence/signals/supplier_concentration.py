"""Signal 20: Supplier & Vendor Concentration Risk.

The supply-side mirror of customer concentration. A business reliant on a
single supplier for a large share of its COGS or operating inputs faces:
  - Supply disruption risk: one supplier failure stops operations
  - Price leverage: dominant supplier can extract margin at renewal
  - Transferability risk: supplier relationship may be personal to the owner
  - Force majeure: natural disaster, bankruptcy, or geopolitical event

Industry benchmarks (supply chain risk management standards):
  Single supplier >40% of outflows = material supply chain risk
  Single supplier >60% = critical dependency — often flagged in M&A due diligence
  Top-3 suppliers >80% of COGS = highly concentrated supply chain

Herfindahl-Hirschman Index (HHI) applied to supplier base:
  HHI > 2,500 = highly concentrated
  HHI 1,500–2,500 = moderately concentrated

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict


def _normalize_vendor(merchant: str) -> str:
    m = merchant.upper().strip()
    # Strip payment method prefixes
    for prefix in ["ACH DEBIT ", "ACH PMT ", "WIRE TO ", "CHECK TO ", "EFT TO ",
                   "PAYMENT TO ", "PMT TO ", "BILL PMT "]:
        if m.startswith(prefix):
            m = m[len(prefix):]
    m = re.sub(r"\s+\d{4,}$", "", m).strip()
    m = re.sub(r"\s+", " ", m)
    return m[:60]


EXCLUDE_KW = [
    # Financial/tax obligations (not supply chain)
    "IRS", "EFTPS", "TAX", "PAYROLL", "SALARY", "WAGES",
    "LOAN", "MORTGAGE", "SBA", "LEASE PMT", "INSURANCE PMT",
    "AMERICAN EXPRESS", "VISA PMT", "MASTERCARD", "DISCOVER",
    "TRANSFER", "OWNER", "MEMBER DIST", "SHAREHOLDER",
    "UTILITY", "ELECTRIC", "GAS CO", "WATER BILL", "AT&T", "VERIZON",
    "COMCAST", "XFINITY", "GOOGLE", "MICROSOFT", "AMAZON WEB",
    "BANK FEE", "SERVICE CHARGE", "WIRE FEE",
]

COGS_VENDOR_KW = [
    # Indicators this is a product/material supplier (stronger signal)
    "WHOLESALE", "SUPPLY CO", "SUPPLIER", "DISTRIBUTOR", "MANUFACTURER",
    "MATERIALS", "PARTS", "COMPONENTS", "INVENTORY", "STOCK",
    "IMPORT", "EXPORT", "FREIGHT", "SHIPPING", "LOGISTICS",
]


def _is_excluded(merchant: str) -> bool:
    m = merchant.upper()
    return any(kw in m for kw in EXCLUDE_KW)


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    outflows = [t for t in transactions if t["amount"] < 0]
    if not outflows:
        return []

    total_outflow = sum(abs(t["amount"]) for t in outflows)
    if total_outflow < 10000:
        return []

    # Aggregate by normalized vendor
    vendor_totals: dict[str, float] = defaultdict(float)
    vendor_is_cogs: dict[str, bool] = defaultdict(bool)

    for t in outflows:
        merchant = t.get("merchant", "")
        if _is_excluded(merchant):
            continue
        key = _normalize_vendor(merchant)
        if not key or len(key) <= 3:
            continue
        vendor_totals[key] += abs(t["amount"])
        if any(kw in merchant.upper() for kw in COGS_VENDOR_KW):
            vendor_is_cogs[key] = True

    if not vendor_totals:
        return []

    ranked = sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)
    identified_total = sum(v for _, v in ranked)

    if identified_total < total_outflow * 0.20:
        return []

    results = []

    # HHI
    hhi = sum((v / identified_total * 100) ** 2 for _, v in ranked)

    top1_pct = ranked[0][1] / identified_total
    top3_total = sum(v for _, v in ranked[:3])
    top3_pct = top3_total / identified_total if len(ranked) >= 3 else top1_pct
    top5_total = sum(v for _, v in ranked[:5])
    top5_pct = top5_total / identified_total

    top5_display = []
    for i, (name, amt) in enumerate(ranked[:5], 1):
        pct = amt / identified_total
        cogs_tag = " [COGS]" if vendor_is_cogs.get(name) else ""
        top5_display.append(f"#{i} {name}{cogs_tag}: ${amt:,.0f} ({pct:.0%})")

    top5_text = " | ".join(top5_display)

    # ── Single dominant supplier ───────────────────────────────────────────────
    if top1_pct >= 0.30:
        sev = "red" if top1_pct >= 0.50 else "amber"
        results.append({
            "signal_type": "supplier_concentration",
            "severity": sev,
            "merchant": f"SUPPLIER CONCENTRATION: #{1} vendor = {top1_pct:.0%} of outflows",
            "amount": -ranked[0][1],
            "transaction_date": "",
            "description": (
                f"Single-supplier concentration: '{ranked[0][0]}' represents {top1_pct:.0%} "
                f"(${ranked[0][1]:,.0f}) of identified vendor outflows. "
                f"{'CRITICAL: ' if top1_pct >= 0.50 else ''}"
                "Heavy reliance on a single supplier creates operational and pricing risk "
                "that may not survive an ownership change. Verify: "
                "(1) contract status, term, and transferability to new owner, "
                "(2) whether pricing is locked or at-market, "
                "(3) supplier's own financial stability and capacity, "
                "(4) availability of alternative suppliers and switching costs, "
                "(5) whether the relationship is personal to the current owner."
            ),
            "library_match": "SUPPLIER_CONCENTRATION_SINGLE",
            "confidence_weight": 0.75 if top1_pct >= 0.50 else 0.60,
        })

    # ── Top-3 concentration ───────────────────────────────────────────────────
    if top3_pct >= 0.70 and len(ranked) >= 3:
        sev = "red" if top3_pct >= 0.85 else "amber"
        results.append({
            "signal_type": "supplier_concentration",
            "severity": sev,
            "merchant": f"TOP-3 SUPPLIER CONCENTRATION: {top3_pct:.0%} of vendor outflows",
            "amount": -top3_total,
            "transaction_date": "",
            "description": (
                f"Top-3 supplier concentration: {top3_pct:.0%} (${top3_total:,.0f}) "
                f"of identifiable outflows from three vendors. "
                f"HHI: {hhi:.0f} ({'highly' if hhi > 2500 else 'moderately'} concentrated). "
                f"Top vendors: {' | '.join(f'{n} ({v/identified_total:.0%})' for n, v in ranked[:3])}. "
                "Request full vendor list with annual spend per supplier for last 3 years. "
                "Assess transferability of all major supplier contracts before closing."
            ),
            "library_match": "SUPPLIER_CONCENTRATION_TOP3",
            "confidence_weight": 0.65,
        })

    # ── HHI only if no individual flags hit ──────────────────────────────────
    if not results and hhi > 2500:
        results.append({
            "signal_type": "supplier_concentration",
            "severity": "amber",
            "merchant": f"SUPPLIER HHI: {hhi:.0f} — concentrated vendor base",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"Supplier HHI of {hhi:.0f} indicates a concentrated vendor base "
                f"(>2,500 = highly concentrated). "
                f"Top vendors: {top5_text}. "
                "Even without a single dominant supplier, a narrow vendor base increases "
                "supply chain fragility. Request full supplier breakdown."
            ),
            "library_match": "SUPPLIER_CONCENTRATION_HHI",
            "confidence_weight": 0.50,
        })

    # ── Vendor map ────────────────────────────────────────────────────────────
    if results:
        results.append({
            "signal_type": "supplier_concentration",
            "severity": "amber",
            "merchant": f"VENDOR SPEND MAP: {len(ranked)} identified suppliers",
            "amount": -identified_total,
            "transaction_date": "",
            "description": (
                f"Vendor outflow breakdown ({len(ranked)} identified suppliers, "
                f"${identified_total:,.0f} of ${total_outflow:,.0f} total outflows): "
                f"{top5_text}. "
                f"Top-5 represent {top5_pct:.0%} of identified vendor spend. "
                "[COGS] tagged vendors are likely direct material/product suppliers — "
                "prioritise their contract review in due diligence."
            ),
            "library_match": None,
            "confidence_weight": 0.45,
        })

    return results
