"""Signal 15: Business Valuation Intelligence — Fair Value & Quality Assessment.

Computes normalized EBITDA, Seller's Discretionary Earnings (SDE), implied
acquisition multiples, revenue quality score, and working capital adequacy.
Surfaces over/under-valuation signals for the deal team's consideration.

Methodology:
- SDE = Net Income + Owner Salary + D&A + Interest + Non-Cash + Personal Addbacks
- EBITDA = Net Income + Interest + Taxes + D&A (institutional benchmark)
- Revenue Quality Score = weighted factors: cash-backed, recurring, concentration
- Implied Multiple = Ask Price / EBITDA (if ask price is available)

Industry multiple benchmarks (IBBA / BizBuySell / Pepperdine 2023–2024):
  SME (<$1M EBITDA): 2.5–4.0x EBITDA, 0.3–0.7x Revenue
  Lower Middle Market ($1–5M): 4.0–7.0x EBITDA, 0.5–1.5x Revenue
  Healthcare/SaaS: 6.0–12.0x EBITDA
  Retail/Restaurant: 1.5–3.0x EBITDA (highest risk)
  Real Estate Services: 3.0–5.0x EBITDA
  Manufacturing: 3.5–6.0x EBITDA

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


# ── Keyword banks ─────────────────────────────────────────────────────────────
REVENUE_KW = ["REVENUE", "SALES", "INCOME", "NET SALES", "GROSS REVENUE", "SERVICE REVENUE"]
COGS_KW = ["COST OF GOODS", "COST OF SALES", "COGS", "DIRECT COST", "COST OF REVENUE", "DIRECT LABOUR"]
SGA_KW = ["SELLING", "GENERAL", "ADMINISTRATIVE", "SG&A", "G&A", "OVERHEAD", "OFFICE EXPENSE"]
DEPR_KW = ["DEPRECIATION", "AMORTIZATION", "AMORTISATION", "D&A"]
INTEREST_KW = ["INTEREST EXPENSE", "INTEREST PAID", "FINANCE CHARGE", "BANK CHARGE", "LOAN INTEREST"]
TAX_KW = ["INCOME TAX", "TAX PROVISION", "CORPORATE TAX", "FEDERAL TAX", "STATE TAX EXPENSE"]
NET_INCOME_KW = ["NET INCOME", "NET PROFIT", "NET LOSS", "NET EARNINGS", "BOTTOM LINE"]
OWNER_SALARY_KW = ["OWNER SALARY", "OWNER DRAW", "OFFICER SALARY", "OWNER COMPENSATION",
                   "MEMBER DISTRIBUTION", "SHAREHOLDER SALARY", "OWNER PAY", "MANAGING MEMBER"]
PERSONAL_KW = ["PERSONAL", "AUTO EXPENSE", "VEHICLE", "HEALTH INSURANCE", "LIFE INSURANCE",
               "TRAVEL ENTERTAINMENT", "MEALS ENTERTAIN", "CELL PHONE", "HOME OFFICE"]
NON_RECURRING_KW = ["LEGAL SETTLEMENT", "INSURANCE PROCEEDS", "PPP LOAN", "EIDL", "GRANT",
                    "ONE-TIME", "EXTRAORDINARY", "WRITE-OFF", "WRITE OFF", "ASSET DISPOSAL",
                    "GAIN ON SALE", "LOSS ON SALE", "LAWSUIT", "SETTLEMENT"]
CURRENT_ASSET_KW = ["CURRENT ASSET", "CASH", "ACCOUNTS RECEIVABLE", "INVENTORY",
                    "PREPAID", "SHORT-TERM", "A/R"]
CURRENT_LIAB_KW = ["CURRENT LIAB", "ACCOUNTS PAYABLE", "A/P", "ACCRUED", "SHORT-TERM DEBT",
                   "LINE OF CREDIT", "CREDIT CARD PAYABLE", "DEFERRED REVENUE"]


# ── Industry EBITDA multiple benchmarks ───────────────────────────────────────
INDUSTRY_MULTIPLES = {
    "default":        {"low": 2.5, "mid": 3.5, "high": 5.0, "rev_low": 0.4, "rev_high": 0.9},
    "retail":         {"low": 1.5, "mid": 2.5, "high": 3.5, "rev_low": 0.2, "rev_high": 0.5},
    "restaurant":     {"low": 1.5, "mid": 2.5, "high": 3.5, "rev_low": 0.3, "rev_high": 0.6},
    "saas":           {"low": 6.0, "mid": 9.0, "high": 14.0, "rev_low": 3.0, "rev_high": 8.0},
    "healthcare":     {"low": 6.0, "mid": 8.0, "high": 12.0, "rev_low": 1.0, "rev_high": 2.5},
    "manufacturing":  {"low": 3.5, "mid": 5.0, "high": 7.0, "rev_low": 0.5, "rev_high": 1.2},
    "real_estate":    {"low": 3.0, "mid": 4.5, "high": 6.0, "rev_low": 0.5, "rev_high": 1.5},
    "professional":   {"low": 3.0, "mid": 4.5, "high": 6.5, "rev_low": 0.5, "rev_high": 1.2},
    "ecommerce":      {"low": 2.0, "mid": 3.5, "high": 5.0, "rev_low": 0.5, "rev_high": 1.0},
}


def _detect_industry(pl_rows, transactions) -> str:
    all_text = " ".join([
        str(r.get("account", "")) + " " + str(r.get("description", ""))
        for r in (pl_rows or [])
    ] + [
        str(t.get("merchant", "")) for t in (transactions or [])
    ]).upper()

    if any(kw in all_text for kw in ["SAAS", "SUBSCRIPTION", "SOFTWARE", "API", "MONTHLY RECURRING"]):
        return "saas"
    if any(kw in all_text for kw in ["MEDICAL", "DENTAL", "PHARMACY", "CLINIC", "PATIENT", "HEALTHCARE"]):
        return "healthcare"
    if any(kw in all_text for kw in ["RESTAURANT", "FOOD SERVICE", "CATERING", "CAFE", "BAR & GRILL"]):
        return "restaurant"
    if any(kw in all_text for kw in ["RETAIL", "STORE", "MERCHANDISE", "INVENTORY SALES", "POINT OF SALE"]):
        return "retail"
    if any(kw in all_text for kw in ["MANUFACTURING", "FABRICATION", "PRODUCTION", "ASSEMBLY", "PLANT"]):
        return "manufacturing"
    if any(kw in all_text for kw in ["REAL ESTATE", "PROPERTY MANAGEMENT", "RENTAL INCOME", "TENANTS"]):
        return "real_estate"
    if any(kw in all_text for kw in ["CONSULTING", "PROFESSIONAL SERVICES", "LAW", "ACCOUNTING", "ADVISORY"]):
        return "professional"
    if any(kw in all_text for kw in ["SHOPIFY", "AMAZON", "EBAY", "ETSY", "ECOMMERCE", "E-COMMERCE"]):
        return "ecommerce"
    return "default"


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not pl_rows:
        return []

    results = []

    # ── Extract P&L components ─────────────────────────────────────────────────
    revenue = _sum_rows(pl_rows, REVENUE_KW)
    cogs = _sum_rows(pl_rows, COGS_KW)
    sga = _sum_rows(pl_rows, SGA_KW)
    depr = _sum_rows(pl_rows, DEPR_KW)
    interest = abs(_sum_rows(pl_rows, INTEREST_KW))
    taxes = abs(_sum_rows(pl_rows, TAX_KW))
    net_income = _sum_rows(pl_rows, NET_INCOME_KW)
    owner_salary = _sum_rows(pl_rows, OWNER_SALARY_KW)
    personal_expenses = abs(_sum_rows(pl_rows, PERSONAL_KW))
    non_recurring = abs(_sum_rows(pl_rows, NON_RECURRING_KW))
    current_assets = _sum_rows(pl_rows, CURRENT_ASSET_KW)
    current_liabilities = abs(_sum_rows(pl_rows, CURRENT_LIAB_KW))

    if revenue <= 0:
        return []

    gross_profit = revenue - abs(cogs)
    ebitda = net_income + interest + taxes + abs(depr)

    # ── SDE: Seller's Discretionary Earnings (SME buyer benchmark) ────────────
    # SDE adds back owner compensation to EBITDA — represents total benefit to owner-operator
    sde = ebitda + abs(owner_salary) + personal_expenses
    gross_margin = gross_profit / revenue if revenue > 0 else 0
    ebitda_margin = ebitda / revenue if revenue > 0 else 0

    # ── Industry detection ─────────────────────────────────────────────────────
    industry = _detect_industry(pl_rows, transactions)
    benchmarks = INDUSTRY_MULTIPLES.get(industry, INDUSTRY_MULTIPLES["default"])

    # ── Cash inflows as revenue quality proxy ─────────────────────────────────
    cash_inflows = sum(t["amount"] for t in transactions if t["amount"] > 0)
    revenue_quality_pct = min(cash_inflows / revenue, 2.0) if revenue > 0 else 0

    # ── Revenue quality score (0–100) ─────────────────────────────────────────
    quality_score = 100
    quality_notes = []

    if revenue_quality_pct < 0.70:
        penalty = int((0.70 - revenue_quality_pct) * 100)
        quality_score -= penalty
        quality_notes.append(
            f"Cash receipts (${cash_inflows:,.0f}) are only {revenue_quality_pct:.0%} of declared revenue — "
            "low cash backing reduces revenue quality. Verify collectability."
        )
    elif revenue_quality_pct > 1.30:
        quality_score -= 10
        quality_notes.append(
            f"Cash receipts ({revenue_quality_pct:.0%} of revenue) exceed declared revenue — "
            "may indicate pre-collected deposits or deferred revenue not yet recognised."
        )

    if non_recurring > revenue * 0.05:
        penalty = int(min((non_recurring / revenue) * 50, 25))
        quality_score -= penalty
        quality_notes.append(
            f"Non-recurring items (${non_recurring:,.0f} = {non_recurring/revenue:.0%} of revenue) — "
            "adjust EBITDA/SDE to exclude one-time items before applying multiples."
        )

    if gross_margin > 0.85:
        quality_score -= 10
        quality_notes.append(
            f"Gross margin {gross_margin:.0%} is unusually high — verify COGS are fully captured."
        )
    elif gross_margin < 0:
        quality_score -= 20
        quality_notes.append("Negative gross margin — COGS exceed revenue. Verify cost classification.")

    quality_score = max(0, min(100, quality_score))

    # ── Valuation range using benchmarks ─────────────────────────────────────
    findings = []
    ebitda_basis = ebitda if ebitda > 0 else None
    sde_basis = sde if sde > 0 else None

    # EBITDA-based range
    if ebitda_basis:
        val_low = ebitda_basis * benchmarks["low"]
        val_mid = ebitda_basis * benchmarks["mid"]
        val_high = ebitda_basis * benchmarks["high"]
        findings.append(
            f"EBITDA-based range ({industry} industry, {benchmarks['low']:.1f}–{benchmarks['high']:.1f}x): "
            f"${val_low:,.0f} – ${val_high:,.0f} (mid: ${val_mid:,.0f})"
        )

    # SDE-based range (SME owner-operator benchmark, typically 2–3x SDE)
    if sde_basis and sde_basis > ebitda_basis if ebitda_basis else sde_basis:
        sde_low = sde_basis * 2.0
        sde_high = sde_basis * 3.5
        findings.append(
            f"SDE-based range (owner-operator benchmark, 2.0–3.5x SDE ${sde_basis:,.0f}): "
            f"${sde_low:,.0f} – ${sde_high:,.0f}"
        )

    # Revenue multiple sanity check
    rev_val_low = revenue * benchmarks["rev_low"]
    rev_val_high = revenue * benchmarks["rev_high"]
    findings.append(
        f"Revenue multiple cross-check ({benchmarks['rev_low']:.1f}–{benchmarks['rev_high']:.1f}x revenue): "
        f"${rev_val_low:,.0f} – ${rev_val_high:,.0f}"
    )

    if not findings:
        return []

    # ── Working capital adequacy ───────────────────────────────────────────────
    wc_notes = []
    if current_assets > 0 and current_liabilities > 0:
        current_ratio = current_assets / current_liabilities
        working_capital = current_assets - current_liabilities
        if current_ratio < 1.0:
            wc_notes.append(
                f"WORKING CAPITAL DEFICIT: Current ratio {current_ratio:.2f} — "
                f"current assets (${current_assets:,.0f}) < current liabilities (${current_liabilities:,.0f}). "
                "Business cannot meet short-term obligations from current assets. "
                "Negotiate working capital peg in purchase agreement."
            )
        elif current_ratio < 1.2:
            wc_notes.append(
                f"Working capital thin: current ratio {current_ratio:.2f} (${working_capital:,.0f} surplus). "
                "Ensure purchase agreement includes adequate working capital target."
            )
        else:
            wc_notes.append(
                f"Working capital adequate: current ratio {current_ratio:.2f} (${working_capital:,.0f} surplus)."
            )

    # ── Addback summary ───────────────────────────────────────────────────────
    addback_items = []
    if abs(owner_salary) > 0:
        addback_items.append(f"Owner salary/draws: ${abs(owner_salary):,.0f}")
    if personal_expenses > 0:
        addback_items.append(f"Personal/discretionary expenses: ${personal_expenses:,.0f}")
    if abs(depr) > 0:
        addback_items.append(f"Depreciation & amortization: ${abs(depr):,.0f}")
    if interest > 0:
        addback_items.append(f"Interest expense: ${interest:,.0f}")
    if non_recurring > 0:
        addback_items.append(f"Non-recurring items: ${non_recurring:,.0f} (verify and exclude)")

    # ── Margin quality flags ───────────────────────────────────────────────────
    margin_notes = []
    if ebitda_margin > 0.35:
        margin_notes.append(
            f"EBITDA margin {ebitda_margin:.0%} is exceptionally high — verify all operating costs are captured. "
            "Margins above 35% are uncommon outside SaaS/software and may indicate missing expenses."
        )
    elif ebitda_margin < 0.05 and ebitda > 0:
        margin_notes.append(
            f"EBITDA margin {ebitda_margin:.0%} is very thin — limited operating leverage. "
            "Acquisition leaves little room for debt service, integration costs, or revenue decline."
        )
    elif ebitda < 0:
        margin_notes.append(
            f"Negative EBITDA (${ebitda:,.0f}) — business is not self-sustaining at operating level. "
            "Acquisition value must be predicated on turnaround thesis or asset value only."
        )

    # ── Construct output ───────────────────────────────────────────────────────
    severity = "amber"
    if quality_score < 60 or ebitda < 0:
        severity = "red"
    elif wc_notes and "DEFICIT" in (wc_notes[0] if wc_notes else ""):
        severity = "red"

    valuation_lines = " | ".join(findings)
    addback_text = "; ".join(addback_items) if addback_items else "None identified in P&L"
    wc_text = " ".join(wc_notes) if wc_notes else "Insufficient balance sheet data."
    quality_note_text = " ".join(quality_notes) if quality_notes else "Revenue quality appears consistent."
    margin_text = " ".join(margin_notes) if margin_notes else ""

    description_parts = [
        f"Revenue: ${revenue:,.0f} | Gross Margin: {gross_margin:.0%} | "
        f"EBITDA: ${ebitda:,.0f} ({ebitda_margin:.0%} margin) | SDE: ${sde:,.0f}",
        f"Valuation ranges ({industry}): {valuation_lines}",
        f"Revenue quality score: {quality_score}/100. {quality_note_text}",
        f"Addbacks identified: {addback_text}",
        f"Working capital: {wc_text}",
    ]
    if margin_text:
        description_parts.append(margin_text)

    description_parts.append(
        "IMPORTANT: These ranges are indicative only. Apply to 3-year trailing average EBITDA where available. "
        "Confirm with a certified business appraiser or M&A advisor before pricing an offer. "
        "Multiples source: IBBA Market Pulse, BizBuySell, Pepperdine Private Capital Markets 2023–2024."
    )

    results.append({
        "signal_type": "valuation",
        "severity": severity,
        "merchant": f"VALUATION INTELLIGENCE: EBITDA ${ebitda:,.0f} | SDE ${sde:,.0f} | Quality {quality_score}/100",
        "amount": ebitda,
        "transaction_date": "",
        "description": " ".join(description_parts)[:1500],
        "library_match": "VALUATION_INTELLIGENCE",
        "confidence_weight": 0.70,
    })

    return results
