"""Signal 26: Legal & Litigation Risk Detection.

High or escalating legal fees are one of the strongest leading indicators of
undisclosed litigation — sellers rarely volunteer that they are defendants.
Active lawsuits, regulatory proceedings, and unresolved claims transfer with
the business entity and create post-closing liability for the acquirer.

Patterns detected:
  High legal spend: >2% of revenue signals active legal matters
  Multiple law firms: simultaneous payments to different firms = multi-front litigation
  Legal spend trend: escalating payments signal new or growing dispute
  Settlement payments: confirm resolved (but may indicate more to come)
  Court / filing fees: direct evidence of active proceedings
  Expert witness / forensic fees: signals complex litigation in progress

Sources:
- ABA Model Rules — attorney-client privilege survives acquisition
- Delaware Court of Chancery — successor liability in asset vs stock deals
- Sandbagging doctrine — buyer's knowledge of disclosed litigation matters

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime


ATTORNEY_KEYWORDS = [
    "LAW FIRM", "ATTORNEY", "ATTORNEYS", "LAW OFFICE", "LEGAL FEES",
    "LEGAL SERVICES", "COUNSEL", "LLP LEGAL", "PC ATTORNEY",
    "PLLC LEGAL", "LAW GROUP", "LAWYERS", "BARRISTER",
    "SOLICITOR", "LITIGATION", "LEGAL RETAINER",
]

LAW_FIRM_SUFFIXES = [" LLP", " PC", " PLLC", " LLC ATTORNEY", " LAW", " ESQ"]

COURT_KEYWORDS = [
    "COURT FILING", "COURT FEE", "FILING FEE", "CLERK OF COURT",
    "DISTRICT COURT", "CIRCUIT COURT", "SUPERIOR COURT",
    "COURT COST", "DOCKET FEE", "ARBITRATION FEE", "MEDIATION FEE",
    "AAAM ", "JAMS ARBITRATION", "AAA ARBITRATION",
]

SETTLEMENT_KEYWORDS = [
    "SETTLEMENT", "SETTLEMENT PAYMENT", "LEGAL SETTLEMENT",
    "JUDGMENT PAYMENT", "CONSENT JUDGMENT", "STIPULATED",
]

EXPERT_KEYWORDS = [
    "EXPERT WITNESS", "FORENSIC ACCOUNTANT", "LITIGATION SUPPORT",
    "EXPERT FEES", "DEPOSITION", "DISCOVERY COST",
]


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _is_law_firm(merchant: str) -> bool:
    m = merchant.upper()
    if any(kw in m for kw in ATTORNEY_KEYWORDS):
        return True
    if any(m.endswith(sfx.upper()) or sfx.upper() in m for sfx in LAW_FIRM_SUFFIXES):
        if any(c.isalpha() for c in m):
            return True
    return False


def _sum_rows_revenue(pl_rows) -> float:
    if not pl_rows:
        return 0.0
    rev_kw = ["REVENUE", "SALES", "INCOME", "NET SALES"]
    total = 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        if any(kw in acc for kw in rev_kw):
            for key in ("ytd", "amount", "value", "this_month"):
                v = r.get(key)
                if v is not None:
                    try:
                        val = float(re.sub(r"[,$\s%]", "", str(v)))
                        if val != 0:
                            total += val
                            break
                    except (ValueError, TypeError):
                        pass
    return total


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    legal_txns = []
    court_txns = []
    settlement_txns = []
    expert_txns = []
    law_firms = defaultdict(float)

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        if _is_law_firm(merchant) or any(kw in merchant for kw in ATTORNEY_KEYWORDS):
            legal_txns.append(t)
            # Normalise firm name (first 40 chars)
            firm_key = merchant[:40].strip()
            law_firms[firm_key] += abs(t["amount"])
        if any(kw in merchant for kw in COURT_KEYWORDS):
            court_txns.append(t)
        if any(kw in merchant for kw in SETTLEMENT_KEYWORDS):
            settlement_txns.append(t)
        if any(kw in merchant for kw in EXPERT_KEYWORDS):
            expert_txns.append(t)

    all_legal = legal_txns + court_txns + settlement_txns + expert_txns
    if not all_legal:
        return []

    total_legal = sum(abs(t["amount"]) for t in all_legal)
    revenue = _sum_rows_revenue(pl_rows)

    # ── Multi-firm detection ──────────────────────────────────────────────────
    active_firms = len([f for f, v in law_firms.items() if v > 1000])

    # ── Trend: is legal spend escalating? ────────────────────────────────────
    monthly_legal: dict[str, float] = defaultdict(float)
    for t in legal_txns:
        d = _parse_date(t.get("transaction_date", ""))
        if d:
            monthly_legal[f"{d.year}-{d.month:02d}"] += abs(t["amount"])

    trend_note = ""
    if len(monthly_legal) >= 3:
        months = sorted(monthly_legal.keys())
        vals = [monthly_legal[m] for m in months]
        recent_avg = sum(vals[-2:]) / 2
        prior_avg = sum(vals[:-2]) / max(len(vals) - 2, 1)
        if prior_avg > 0 and recent_avg > prior_avg * 1.5:
            trend_note = (
                f" ESCALATING: recent legal spend ${recent_avg:,.0f}/month vs "
                f"prior average ${prior_avg:,.0f}/month — litigation appears to be growing."
            )

    # ── Revenue ratio ─────────────────────────────────────────────────────────
    rev_note = ""
    if revenue > 0:
        legal_pct = total_legal / revenue
        if legal_pct > 0.03:
            rev_note = (
                f" Legal spend is {legal_pct:.1%} of revenue — "
                "above the 1–2% threshold that typically indicates active disputed matters."
            )

    # ── Severity ──────────────────────────────────────────────────────────────
    if active_firms >= 3 or settlement_txns or (revenue > 0 and total_legal / revenue > 0.05):
        severity = "red"
    elif active_firms >= 2 or expert_txns or trend_note:
        severity = "amber"
    else:
        severity = "amber"

    firm_list = ", ".join(f"{f[:30]} (${v:,.0f})" for f, v in sorted(law_firms.items(), key=lambda x: -x[1])[:4])

    description = (
        f"Legal spend: {len(all_legal)} transactions totalling ${total_legal:,.0f}. "
        f"Law firms identified: {active_firms} ({firm_list}). "
        f"{trend_note}{rev_note} "
        "Undisclosed litigation is one of the most common post-closing disputes in SME acquisitions. "
        "Sellers are not required to volunteer litigation unless specifically asked and represented. "
        "Required actions: (1) request litigation schedule from seller (all pending, threatened, and "
        "settled claims in last 5 years), (2) run court record searches in all states of operation, "
        "(3) check UCC lien filings, judgment liens, and lis pendens on all business assets, "
        "(4) request attorney opinion letters on open matters, "
        "(5) obtain rep and warranty insurance quote — covers undisclosed pre-closing liabilities."
    )
    if settlement_txns:
        stotal = sum(abs(t["amount"]) for t in settlement_txns)
        description += f" Settlement payments detected: ${stotal:,.0f} — confirm all matters fully resolved."
    if court_txns:
        description += f" Court filing fees detected ({len(court_txns)}) — confirms active proceedings."

    results.append({
        "signal_type": "legal_risk",
        "severity": severity,
        "merchant": f"LEGAL / LITIGATION RISK: ${total_legal:,.0f} legal spend | {active_firms} firms",
        "amount": -total_legal,
        "transaction_date": all_legal[0].get("transaction_date", ""),
        "description": description[:1500],
        "library_match": "LEGAL_LITIGATION_RISK",
        "confidence_weight": 0.75 if severity == "red" else 0.60,
    })

    return results
