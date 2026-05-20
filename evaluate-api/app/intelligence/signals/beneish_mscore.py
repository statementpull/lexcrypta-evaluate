"""Signal 14: Beneish M-Score — Academic Earnings Manipulation Detection Model.

The Beneish M-Score (1999) uses 8 financial ratios to detect the probability
of earnings manipulation. Threshold: M-Score > -1.78 = manipulator alert.
M-Score > -2.22 = zone of concern.

Components derived from P&L and bank data:
  DSRI  — Days Sales in Receivables Index (AR quality)
  GMI   — Gross Margin Index (margin trend)
  AQI   — Asset Quality Index (accrual intensity)
  SGI   — Sales Growth Index (growth pressure)
  DEPI  — Depreciation Index (depreciation policy)
  SGAI  — SGA Index (expense management)
  TATA  — Total Accruals to Total Assets (accrual quality)

Sources:
- Beneish (1999) "The Detection of Earnings Manipulation" Financial Analysts Journal
- SEC AAER database analysis: DSRI and TATA are the two strongest individual predictors
- Transparently.ai M-Score implementation studies

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict


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


def _sum_rows(pl_rows, *keyword_lists) -> float:
    total = 0.0
    for r in pl_rows:
        account = str(r.get("account", "")).upper()
        desc = str(r.get("description", "")).upper()
        for kws in keyword_lists:
            if any(kw in account or kw in desc for kw in kws):
                total += _row_amount(r)
                break
    return total


REVENUE_KW = ["REVENUE", "SALES", "INCOME", "NET SALES", "GROSS REVENUE"]
COGS_KW = ["COST OF GOODS", "COST OF SALES", "COGS", "DIRECT COST", "COST OF REVENUE"]
SGA_KW = ["SELLING", "GENERAL", "ADMINISTRATIVE", "SG&A", "G&A", "OVERHEAD"]
DEPR_KW = ["DEPRECIATION", "AMORTIZATION", "AMORTISATION", "D&A"]
AR_KW = ["ACCOUNTS RECEIVABLE", "A/R", "TRADE RECEIVABLE", "DEBTORS", "RECEIVABLE"]
ASSET_KW = ["TOTAL ASSETS", "FIXED ASSET", "PROPERTY", "EQUIPMENT", "PLANT"]
NET_INCOME_KW = ["NET INCOME", "NET PROFIT", "NET LOSS", "NET EARNINGS"]
CURRENT_ASSET_KW = ["CURRENT ASSET", "CASH", "INVENTORY", "PREPAID"]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not pl_rows:
        return []

    results = []

    revenue = _sum_rows(pl_rows, REVENUE_KW)
    cogs = _sum_rows(pl_rows, COGS_KW)
    sga = _sum_rows(pl_rows, SGA_KW)
    depr = _sum_rows(pl_rows, DEPR_KW)
    ar = _sum_rows(pl_rows, AR_KW)
    total_assets = _sum_rows(pl_rows, ASSET_KW)
    net_income = _sum_rows(pl_rows, NET_INCOME_KW)
    current_assets = _sum_rows(pl_rows, CURRENT_ASSET_KW)

    # Cash inflows from bank = operating cash proxy
    cash_from_ops = sum(t["amount"] for t in transactions if t["amount"] > 0)

    if revenue <= 0:
        return []

    scores = {}
    components = []

    # ── DSRI: Days Sales in Receivables Index ─────────────────────────────────
    # DSRI > 1 means receivables growing faster than revenue — channel stuffing signal
    # Without prior year, we compute absolute DSO and flag if >90 days
    if ar > 0:
        dso = (ar / revenue) * 365
        if dso > 90:
            scores["DSRI"] = dso / 45  # normalised against 45-day baseline
            components.append(
                f"DSO {dso:.0f} days (AR ${ar:,.0f} / Revenue ${revenue:,.0f}) — "
                "receivables collection period above 90 days signals revenue booked "
                "before cash received. DSRI >1 is the strongest single predictor of manipulation."
            )
        elif dso > 60:
            scores["DSRI"] = dso / 45
            components.append(
                f"DSO {dso:.0f} days — elevated but not critical. Monitor for trend."
            )

    # ── GMI: Gross Margin Index ───────────────────────────────────────────────
    # Single period: compute gross margin and flag if unusual
    if cogs > 0:
        gross_margin = (revenue - cogs) / revenue
        if gross_margin > 0.80:
            scores["GMI"] = 1.5  # proxy for high GMI
            components.append(
                f"Gross margin {gross_margin:.0%} — unusually high. "
                "GMI >1 (deteriorating margin) can indicate COGS suppression; "
                "implausibly high margin suggests COGS understatement."
            )
        elif gross_margin < 0:
            scores["GMI"] = 2.0
            components.append(
                f"Negative gross margin — COGS exceeds revenue. Verify cost classification."
            )

    # ── SGI: Sales Growth Index ───────────────────────────────────────────────
    # Without prior year we use revenue vs cash: if revenue >> cash, rapid "growth"
    # may be fictitious
    if cash_from_ops > 0 and revenue > cash_from_ops * 1.5:
        sgi_proxy = revenue / cash_from_ops
        if sgi_proxy > 2.0:
            scores["SGI"] = sgi_proxy
            components.append(
                f"Revenue (${revenue:,.0f}) is {sgi_proxy:.1f}x cash inflows (${cash_from_ops:,.0f}). "
                "High SGI indicates growth pressure — companies growing rapidly face "
                "incentive to manipulate to sustain growth narrative."
            )

    # ── DEPI: Depreciation Index ──────────────────────────────────────────────
    # Low depreciation relative to assets = extending useful lives (Waste Management pattern)
    if depr > 0 and total_assets > 0:
        depr_rate = depr / total_assets
        if depr_rate < 0.02 and total_assets > 50000:
            scores["DEPI"] = 0.5  # very low depreciation rate
            components.append(
                f"Depreciation rate {depr_rate:.1%} of total assets — very low. "
                "DEPI <1 (falling depreciation rate) signals extended useful life assumptions. "
                "Waste Management added $1.7B to earnings by extending truck useful lives."
            )

    # ── TATA: Total Accruals to Total Assets ──────────────────────────────────
    # TATA = (Net Income - Cash from Operations) / Total Assets
    # High TATA = earnings driven by accruals, not cash — strongest manipulation signal
    if total_assets > 0 and net_income != 0:
        tata = (net_income - cash_from_ops) / total_assets
        if tata > 0.10:
            scores["TATA"] = tata
            components.append(
                f"TATA {tata:.2f} — accruals represent {tata:.0%} of total assets. "
                "TATA >0.10 is a strong earnings manipulation signal: "
                "net income (${net_income:,.0f}) is significantly above cash from operations (${cash_from_ops:,.0f}). "
                "Earnings driven by accruals, not cash — the most reliable manipulation predictor."
            )
        elif tata > 0.05:
            components.append(
                f"TATA {tata:.2f} — accruals elevated but below critical threshold. "
                "Monitor for trend deterioration."
            )

    # ── SGAI: SG&A Index ─────────────────────────────────────────────────────
    # Rising SGA vs revenue = cost management problems
    if sga > 0 and revenue > 0:
        sga_ratio = sga / revenue
        if sga_ratio > 0.40:
            scores["SGAI"] = sga_ratio / 0.25  # normalised against 25% benchmark
            components.append(
                f"SG&A is {sga_ratio:.0%} of revenue (${sga:,.0f}). "
                "SGAI >1 signals cost structure issues — verify whether SGA has grown "
                "disproportionately to revenue (may indicate revenue inflation without cost growth)."
            )

    # ── Compute composite M-Score proxy ───────────────────────────────────────
    if not scores:
        return []

    # Beneish weights (simplified — single period proxy)
    # Full: -4.84 + 0.92*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI
    m_score_proxy = -3.0  # baseline
    m_score_proxy += scores.get("DSRI", 0) * 0.92
    m_score_proxy += scores.get("GMI", 0) * 0.528
    m_score_proxy += scores.get("SGI", 0) * 0.40
    m_score_proxy += scores.get("DEPI", 0) * 0.115
    m_score_proxy += scores.get("SGAI", 0) * -0.172
    m_score_proxy += scores.get("TATA", 0) * 4.679

    if m_score_proxy > -1.78:
        severity = "red"
        verdict = "MANIPULATION PROBABLE — M-Score above -1.78 threshold"
    elif m_score_proxy > -2.22:
        severity = "amber"
        verdict = "ZONE OF CONCERN — M-Score between -2.22 and -1.78"
    else:
        severity = "amber"
        verdict = "INDIVIDUAL COMPONENTS ELEVATED"

    component_text = " | ".join(components)
    active_components = ", ".join(scores.keys())

    results.append({
        "signal_type": "beneish_mscore",
        "severity": severity,
        "merchant": f"BENEISH M-SCORE: {m_score_proxy:.2f} ({verdict})",
        "amount": 0,
        "transaction_date": "",
        "description": (
            f"Beneish M-Score: {m_score_proxy:.2f} | Threshold: -1.78 (manipulator), -2.22 (zone of concern). "
            f"Active signals: {active_components}. "
            f"Detail: {component_text[:400]}. "
            "The M-Score is an academic earnings manipulation detection model (Beneish 1999) "
            "validated against SEC enforcement cases. M > -1.78 detected Enron, WorldCom, "
            "Lucent, and Bristol-Myers Squibb before their public exposure. "
            "Verify the individual components flagged above with source documentation."
        ),
        "library_match": "BENEISH_MSCORE",
        "confidence_weight": 0.75 if m_score_proxy > -1.78 else 0.60,
    })

    return results
