"""Signal 49: Utility & Infrastructure Cost Analysis.

Utility costs are a proxy for operational scale — they cannot be easily
faked. Electricity, gas, water, and telecom costs scale with actual
production, headcount, and facility use.

Key patterns:
  Utility cost vs revenue ratio: Manufacturing businesses typically spend
    3–8% of revenue on energy. Restaurants 4–7%. Offices 1–2%. Ratios
    far above benchmark signal inefficiency; ratios far below may indicate
    shared/subleased facilities not on the books.
  Sudden utility cost drop: Could indicate facility closure, production
    cutback, or lease termination — verify against reported revenue levels.
  Utility cost spike: New facility, equipment installation, or operational
    surge — verify against CAPEX and headcount records.
  Multiple utility accounts: Multiple accounts across different addresses
    confirm the business operates multiple locations — cross-reference with
    disclosed facilities in the purchase agreement.
  Infrastructure concentration: Businesses dependent on a single telecom
    or internet provider face concentration risk — provider pricing power
    and outage risk.

Industry utility benchmarks (% of revenue, EIA / BLS):
  Manufacturing: 3–8%
  Restaurants / Food Service: 4–7%
  Retail: 1–3%
  Office / Professional Services: 1–2%
  Healthcare: 2–4%
  Warehouse / Distribution: 2–5%

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime

ELECTRIC_KW = [
    "ELECTRIC", "ELECTRICITY", "CONSUMERS ENERGY", "DTE ENERGY", "DETROIT EDISON",
    "DUKE ENERGY", "DOMINION ENERGY", "COMMONWEALTH EDISON", "AMEREN",
    "XCEL ENERGY", "ENTERGY", "EVERGY", "EVERSOURCE", "NATIONAL GRID",
    "PPL ELECTRIC", "PACIFIC GAS", "SOUTHERN CALIFORNIA EDISON", "PG&E",
    "UTILITY PAYMENT", "ELECTRIC BILL",
]

GAS_KW = [
    "NATURAL GAS", "GAS COMPANY", "NICOR GAS", "CENTERPOINT ENERGY",
    "ATMOS ENERGY", "SPIRE GAS", "NEW JERSEY NATURAL GAS", "PIEDMONT NATURAL GAS",
    "GAS BILL", "GAS UTILITY",
]

WATER_KW = [
    "WATER DEPARTMENT", "WATER BILL", "WATER UTILITY", "SEWAGE",
    "WATER AND SEWER", "MUNICIPAL WATER",
]

TELECOM_KW = [
    "AT&T", "VERIZON", "T-MOBILE", "COMCAST", "CHARTER", "COX COMMUNICATIONS",
    "CENTURYLINK", "LUMEN", "SPECTRUM BUSINESS", "FRONTIER COMMUNICATIONS",
    "WINDSTREAM", "CONSOLIDATED COMMUNICATIONS",
    "INTERNET SERVICE", "BROADBAND", "FIBER SERVICE",
    "PHONE SERVICE", "BUSINESS PHONE", "TELEPHONE",
]

REVENUE_KW = ["REVENUE", "SALES", "NET SALES"]


def _row_amount(r: dict) -> float:
    for key in ("ytd", "amount", "value", "this_month", "balance"):
        v = r.get(key)
        if v is not None:
            try:
                val = float(re.sub(r"[,$\s%]", "", str(v)))
                if val != 0:
                    return val
            except (ValueError, TypeError):
                pass
    return 0.0


def _parse_date(d):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt)
        except:
            pass
    return None


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    electric_txns, gas_txns, water_txns, telecom_txns = [], [], [], []

    for t in transactions:
        if t["amount"] >= 0:
            continue
        m = t["merchant"].upper()
        if any(kw in m for kw in ELECTRIC_KW):
            electric_txns.append(t)
        elif any(kw in m for kw in GAS_KW):
            gas_txns.append(t)
        elif any(kw in m for kw in WATER_KW):
            water_txns.append(t)
        elif any(kw in m for kw in TELECOM_KW):
            telecom_txns.append(t)

    all_utility = electric_txns + gas_txns + water_txns + telecom_txns
    if not all_utility:
        return []

    total_utility = sum(abs(t["amount"]) for t in all_utility)
    electric_total = sum(abs(t["amount"]) for t in electric_txns)
    telecom_total = sum(abs(t["amount"]) for t in telecom_txns)

    # Revenue context from P&L
    revenue = 0.0
    if pl_rows:
        for r in pl_rows:
            acc = str(r.get("account", "")).upper()
            if any(kw in acc for kw in REVENUE_KW):
                revenue += _row_amount(r)

    # Multiple utility accounts / locations
    utility_vendors = {t["merchant"] for t in all_utility}
    electric_vendors = {t["merchant"] for t in electric_txns}

    # Trend: utility cost stability or spike
    monthly: dict[str, float] = defaultdict(float)
    for t in all_utility:
        d = _parse_date(t.get("transaction_date", ""))
        if d:
            monthly[f"{d.year}-{d.month:02d}"] += abs(t["amount"])

    spike_note = ""
    drop_note = ""
    if len(monthly) >= 4:
        months = sorted(monthly.keys())
        vals = [monthly[m] for m in months]
        avg_prior = sum(vals[:-3]) / max(len(vals) - 3, 1)
        avg_recent = sum(vals[-3:]) / 3
        if avg_prior > 0:
            if avg_recent > avg_prior * 1.8:
                spike_note = (
                    f" UTILITY COST SPIKE: recent 3-month average (${avg_recent:,.0f}/month) "
                    f"is {avg_recent/avg_prior:.1f}x the prior period (${avg_prior:,.0f}/month). "
                    "Verify: new facility, equipment installation, or rate increase."
                )
            elif avg_recent < avg_prior * 0.55:
                drop_note = (
                    f" UTILITY COST DROP: recent 3-month average (${avg_recent:,.0f}/month) "
                    f"is only {avg_recent/avg_prior:.0%} of the prior period (${avg_prior:,.0f}/month). "
                    "Possible explanations: facility closure, production cutback, or sublease. "
                    "Cross-reference against revenue trend for the same period."
                )

    if spike_note:
        results.append({
            "signal_type": "utility_infrastructure",
            "severity": "amber",
            "merchant": f"UTILITY COST SPIKE: ${total_utility:,.0f} total",
            "amount": -total_utility,
            "transaction_date": all_utility[0].get("transaction_date", ""),
            "description": (
                f"Total utility spend: ${total_utility:,.0f} "
                f"(electric: ${electric_total:,.0f}, telecom: ${telecom_total:,.0f}).{spike_note}{drop_note} "
                f"{len(utility_vendors)} utility vendor(s) identified — "
                f"{'multiple electric accounts may indicate multi-location operations. ' if len(electric_vendors) > 1 else ''}"
                "Verify that all disclosed facilities are represented and no undisclosed "
                "locations are funded through this account."
            ),
            "library_match": "UTILITY_SPIKE",
            "confidence_weight": 0.60,
        })
    elif drop_note:
        results.append({
            "signal_type": "utility_infrastructure",
            "severity": "amber",
            "merchant": f"UTILITY COST DROP: ${total_utility:,.0f} total",
            "amount": -total_utility,
            "transaction_date": all_utility[0].get("transaction_date", ""),
            "description": (
                f"Total utility spend: ${total_utility:,.0f}.{drop_note} "
                "A significant decline in utility costs not explained by efficiency improvements "
                "may indicate a reduction in operational scale. Verify against revenue records."
            ),
            "library_match": "UTILITY_DROP",
            "confidence_weight": 0.55,
        })

    # High utility intensity vs revenue
    if revenue > 0 and total_utility > 0:
        util_pct = total_utility / revenue
        if util_pct > 0.08:
            results.append({
                "signal_type": "utility_infrastructure",
                "severity": "amber",
                "merchant": f"HIGH UTILITY INTENSITY: {util_pct:.1%} of revenue",
                "amount": -total_utility,
                "transaction_date": all_utility[0].get("transaction_date", ""),
                "description": (
                    f"Utility costs (${total_utility:,.0f}) = {util_pct:.1%} of revenue (${revenue:,.0f}). "
                    "This exceeds typical benchmarks for most industries (manufacturing 3–8%, "
                    "restaurants 4–7%, office 1–2%). "
                    "High utility intensity reduces operating leverage — any revenue decline "
                    "has outsized impact on cash flow because utility costs are largely fixed. "
                    "Investigate: (1) energy efficiency of major equipment, "
                    "(2) whether utility contracts have favourable rates locked in, "
                    "(3) whether renewable energy or efficiency upgrades are feasible post-acquisition."
                ),
                "library_match": "UTILITY_HIGH_INTENSITY",
                "confidence_weight": 0.55,
            })

    # Multi-location signal (informational)
    if len(electric_vendors) >= 2:
        results.append({
            "signal_type": "utility_infrastructure",
            "severity": "green",
            "merchant": f"MULTI-LOCATION: {len(electric_vendors)} electric accounts detected",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"{len(electric_vendors)} separate electric utility accounts identified. "
                "Multiple utility accounts confirm multi-location operations. "
                "Verify: (1) all locations are disclosed in the purchase agreement, "
                "(2) lease status and remaining terms at each location, "
                "(3) whether all locations are included in the acquisition or any are being retained."
            ),
            "library_match": "UTILITY_MULTI_LOCATION",
            "confidence_weight": 0.50,
        })

    return results
