"""Signal 18: Customer & Revenue Concentration Risk.

Detects dangerous concentration in revenue sources — a key valuation and
deal risk factor. Heavy dependence on a small number of customers or payers
creates:
  - Churn risk: single customer departure can devastate revenue
  - Negotiating leverage: dominant customers extract price concessions
  - Key-man dependency: customer loyalty may be personal, not institutional
  - Lender risk: banks apply haircuts to concentrated revenue in DSCR models

Herfindahl-Hirschman Index (HHI) is used to quantify concentration:
  HHI = sum of squared market shares. Range 0–10,000.
  HHI > 2,500 = highly concentrated
  HHI > 1,500 = moderately concentrated
  HHI < 1,500 = competitive / diversified

Industry benchmarks (Pepperdine Private Capital Markets, IBBA):
  - Single customer >25% of revenue = acquirer financing often unavailable
  - Single customer >40% = material deal risk, lenders typically decline
  - Top 3 customers >60% = standard M&A disclosure requirement
  - Top 5 customers >80% = value discount typically applied to EBITDA multiple

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict


def _normalize_payer(merchant: str) -> str:
    m = merchant.upper().strip()
    # Strip common payment processor prefixes
    for prefix in ["ACH ", "WIRE ", "CHECK ", "EFT ", "ZELLE ", "VENMO ",
                   "DIRECT DEP ", "DIRECT DEPOSIT ", "DEPOSIT - ", "PAYMENT FROM "]:
        if m.startswith(prefix):
            m = m[len(prefix):]
    # Remove trailing reference numbers (e.g., "ACME INC 123456" → "ACME INC")
    m = re.sub(r"\s+\d{4,}$", "", m).strip()
    # Collapse whitespace
    m = re.sub(r"\s+", " ", m)
    return m[:60]


def _is_internal(merchant: str) -> bool:
    m = merchant.upper()
    internal_kw = [
        "TRANSFER", "OWNER DRAW", "PAYROLL", "DIRECT DEP PAYROLL",
        "INTERNAL", "SWEEP", "SAVINGS", "ZELLE TO", "VENMO TO",
        "LOAN PAYMENT", "IRS", "EFTPS", "SBA", "INSURANCE",
        "BANK FEE", "SERVICE FEE", "MONTHLY FEE",
    ]
    return any(kw in m for kw in internal_kw)


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    # Only look at inflows — these represent customer/payer revenue
    inflows = [t for t in transactions if t["amount"] > 0]
    if not inflows:
        return []

    total_inflow = sum(t["amount"] for t in inflows)
    if total_inflow < 10000:
        return []

    # Aggregate by normalized payer name
    payer_totals: dict[str, float] = defaultdict(float)
    for t in inflows:
        merchant = t.get("merchant", "")
        if _is_internal(merchant):
            continue
        key = _normalize_payer(merchant)
        if key and len(key) > 3:
            payer_totals[key] += t["amount"]

    if not payer_totals:
        return []

    # Sort descending by amount
    ranked = sorted(payer_totals.items(), key=lambda x: x[1], reverse=True)
    identified_total = sum(v for _, v in ranked)

    if identified_total < total_inflow * 0.3:
        # Less than 30% of inflows are identifiable payers — too noisy to report
        return []

    results = []

    # ── HHI calculation ───────────────────────────────────────────────────────
    hhi = sum((v / identified_total * 100) ** 2 for _, v in ranked)

    # ── Top customer concentration ─────────────────────────────────────────────
    top1_pct = ranked[0][1] / identified_total if ranked else 0
    top3_total = sum(v for _, v in ranked[:3])
    top3_pct = top3_total / identified_total
    top5_total = sum(v for _, v in ranked[:5])
    top5_pct = top5_total / identified_total

    # Format top customers for display
    top5_display = []
    for i, (name, amt) in enumerate(ranked[:5], 1):
        pct = amt / identified_total
        top5_display.append(f"#{i} {name}: ${amt:,.0f} ({pct:.0%})")

    top5_text = " | ".join(top5_display)

    # ── Signal: single dominant customer ─────────────────────────────────────
    if top1_pct >= 0.25:
        sev = "red" if top1_pct >= 0.40 else "amber"
        results.append({
            "signal_type": "concentration_risk",
            "severity": sev,
            "merchant": f"CUSTOMER CONCENTRATION: #{1} payer = {top1_pct:.0%} of revenue",
            "amount": ranked[0][1],
            "transaction_date": "",
            "description": (
                f"Single-customer concentration: '{ranked[0][0]}' represents {top1_pct:.0%} "
                f"(${ranked[0][1]:,.0f}) of identified revenue. "
                f"{'CRITICAL: ' if top1_pct >= 0.40 else ''}"
                f"Lenders typically decline acquisition financing when any single customer "
                f"exceeds 25% of revenue. At 40%+, most acquirers apply a material valuation "
                f"discount and require customer estoppel letters or assignment agreements at closing. "
                f"Verify: (1) contract status and remaining term, (2) whether relationship is "
                f"transferable to new ownership, (3) customer's own financial health, "
                f"(4) whether relationship is personal to the current owner."
            ),
            "library_match": "CONCENTRATION_SINGLE_CUSTOMER",
            "confidence_weight": 0.80 if top1_pct >= 0.40 else 0.65,
        })

    # ── Signal: top-3 concentration ───────────────────────────────────────────
    if top3_pct >= 0.60 and len(ranked) >= 3:
        sev = "red" if top3_pct >= 0.80 else "amber"
        results.append({
            "signal_type": "concentration_risk",
            "severity": sev,
            "merchant": f"TOP-3 CUSTOMER CONCENTRATION: {top3_pct:.0%} of revenue",
            "amount": top3_total,
            "transaction_date": "",
            "description": (
                f"Top-3 payer concentration: {top3_pct:.0%} (${top3_total:,.0f}) of revenue "
                f"from three sources. Standard M&A disclosure requires listing customers above "
                f"10% of revenue; SBA lenders decline when top-3 exceeds 60%. "
                f"Top customers: {' | '.join(f[0]+' ('+f'{ranked[i][1]/identified_total:.0%}'+')'  for i, f in enumerate(ranked[:3]))}. "
                f"HHI concentration index: {hhi:.0f} "
                f"({'highly concentrated' if hhi > 2500 else 'moderately concentrated'}). "
                f"Request full customer list with annual revenue per customer for last 3 years."
            ),
            "library_match": "CONCENTRATION_TOP3",
            "confidence_weight": 0.70 if top3_pct >= 0.80 else 0.60,
        })

    # ── Signal: HHI-based concentration (even if no single large customer) ────
    elif hhi > 2500 and not results:
        results.append({
            "signal_type": "concentration_risk",
            "severity": "amber",
            "merchant": f"HHI CONCENTRATION INDEX: {hhi:.0f} (highly concentrated revenue base)",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"Herfindahl-Hirschman Index (HHI) of {hhi:.0f} indicates a highly concentrated "
                f"revenue base (HHI >2,500 = highly concentrated; >1,500 = moderate). "
                f"Top revenue sources: {top5_text}. "
                "Even without a single dominant customer, a highly concentrated payer base "
                "increases churn risk and reduces revenue quality. "
                "Request customer-by-customer revenue breakdown for last 3 years."
            ),
            "library_match": "CONCENTRATION_HHI",
            "confidence_weight": 0.55,
        })

    # ── Always append a revenue source summary if concentration is notable ────
    if results and len(ranked) >= 3:
        results.append({
            "signal_type": "concentration_risk",
            "severity": "amber",
            "merchant": f"REVENUE SOURCE MAP: {len(ranked)} identified payers",
            "amount": identified_total,
            "transaction_date": "",
            "description": (
                f"Revenue payer breakdown ({len(ranked)} identified sources, "
                f"${identified_total:,.0f} of ${total_inflow:,.0f} total inflows): "
                f"{top5_text}. "
                f"Top-5 represent {top5_pct:.0%} of identified revenue. "
                "Use this map to prioritise customer interview and contract review in due diligence. "
                "Obtain signed estoppel letters or comfort letters from customers representing "
                ">10% of revenue before closing."
            ),
            "library_match": None,
            "confidence_weight": 0.50,
        })

    return results
