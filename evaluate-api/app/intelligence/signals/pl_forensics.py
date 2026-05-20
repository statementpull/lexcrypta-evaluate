"""Signal 11: P&L Forensics — income statement manipulation patterns.

Patterns drawn from SEC enforcement cases and forensic accounting literature:
- WorldCom: capitalising operating expenses as CAPEX to hide losses
- Waste Management: extending useful lives of assets to reduce depreciation
- HealthSouth: fabricated journal entries at quarter-end to meet EPS targets
- Sunbeam / Symbol Technologies: cookie-jar reserves, bill-and-hold
- Xerox: accelerating lease revenue recognition
- General: gross margin implausibility, missing line items, ratio red flags

Source: Schilit 'Financial Shenanigans' (4th ed.), ACFE Fraud Examiners Manual,
SEC enforcement releases (1998–2023).

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict
import re


def _parse_float(s) -> float:
    try:
        return float(re.sub(r"[,$\s%]", "", str(s)))
    except (ValueError, TypeError):
        return 0.0


def _row_amount(r: dict) -> float:
    """Extract numeric amount from a P&L row regardless of source format.
    MYOB uses 'ytd'; generic rows use 'amount' or 'value'.
    Prefer YTD (full-period) over this_month.
    """
    for key in ("ytd", "amount", "value", "this_month", "balance"):
        v = r.get(key)
        if v is not None:
            parsed = _parse_float(v)
            if parsed != 0.0:
                return parsed
    return 0.0


def _match(text: str, keywords: list[str]) -> bool:
    t = str(text).upper()
    return any(kw in t for kw in keywords)


# ── Line item keyword banks ────────────────────────────────────────────────────

REVENUE_KW = ["REVENUE", "SALES", "INCOME", "RECEIPTS", "GROSS INCOME", "NET SALES"]
COGS_KW = ["COST OF GOODS", "COST OF SALES", "COGS", "COST OF REVENUE", "DIRECT COST"]
GROSS_PROFIT_KW = ["GROSS PROFIT", "GROSS MARGIN"]
OPEX_KW = ["OPERATING EXPENSE", "SGA", "G&A", "SELLING", "GENERAL", "ADMINISTRATIVE"]
EBITDA_KW = ["EBITDA", "OPERATING INCOME", "OPERATING PROFIT", "INCOME FROM OPERATIONS"]
DEPRECIATION_KW = ["DEPRECIATION", "AMORTISATION", "AMORTIZATION", "D&A", "DEPR"]
CAPEX_KW = ["CAPITAL EXPENDITURE", "CAPEX", "PROPERTY PLANT", "PP&E", "FIXED ASSET", "EQUIPMENT PURCHASE"]
INTEREST_KW = ["INTEREST EXPENSE", "INTEREST INCOME", "FINANCE COST"]
NET_INCOME_KW = ["NET INCOME", "NET PROFIT", "NET LOSS", "BOTTOM LINE", "NET EARNINGS"]
ASSET_KW = ["PROPERTY", "EQUIPMENT", "BUILDING", "ASSET", "MACHINERY", "VEHICLE", "PLANT"]
AR_KW = ["ACCOUNTS RECEIVABLE", "A/R", "TRADE RECEIVABLE", "DEBTORS"]
RESERVE_KW = ["RESERVE", "ALLOWANCE", "PROVISION", "CONTINGENCY"]
MANAGEMENT_FEE_KW = ["MANAGEMENT FEE", "MGMT FEE", "OWNER DRAW", "OWNER SALARY", "RELATED PARTY"]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not pl_rows:
        return []

    results = []

    # ── Index P&L rows by keyword category ────────────────────────────────────
    revenue_rows = [r for r in pl_rows if _match(r.get("account", ""), REVENUE_KW) or _match(r.get("description", ""), REVENUE_KW)]
    cogs_rows = [r for r in pl_rows if _match(r.get("account", ""), COGS_KW) or _match(r.get("description", ""), COGS_KW)]
    depr_rows = [r for r in pl_rows if _match(r.get("account", ""), DEPRECIATION_KW) or _match(r.get("description", ""), DEPRECIATION_KW)]
    capex_rows = [r for r in pl_rows if _match(r.get("account", ""), CAPEX_KW) or _match(r.get("description", ""), CAPEX_KW)]
    asset_rows = [r for r in pl_rows if _match(r.get("account", ""), ASSET_KW) or _match(r.get("description", ""), ASSET_KW)]
    ebitda_rows = [r for r in pl_rows if _match(r.get("account", ""), EBITDA_KW) or _match(r.get("description", ""), EBITDA_KW)]
    ar_rows = [r for r in pl_rows if _match(r.get("account", ""), AR_KW) or _match(r.get("description", ""), AR_KW)]
    net_income_rows = [r for r in pl_rows if _match(r.get("account", ""), NET_INCOME_KW) or _match(r.get("description", ""), NET_INCOME_KW)]
    reserve_rows = [r for r in pl_rows if _match(r.get("account", ""), RESERVE_KW) or _match(r.get("description", ""), RESERVE_KW)]
    mgmt_fee_rows = [r for r in pl_rows if _match(r.get("account", ""), MANAGEMENT_FEE_KW) or _match(r.get("description", ""), MANAGEMENT_FEE_KW)]

    # Extract scalar totals
    total_revenue = sum(_row_amount(r) for r in revenue_rows)
    total_cogs = sum(_row_amount(r) for r in cogs_rows)
    total_depr = sum(_row_amount(r) for r in depr_rows)
    total_capex = sum(_row_amount(r) for r in capex_rows)
    total_ar = sum(_row_amount(r) for r in ar_rows)
    total_net_income = sum(_row_amount(r) for r in net_income_rows)
    total_mgmt_fees = sum(_row_amount(r) for r in mgmt_fee_rows)

    # Gross profit
    if cogs_rows and revenue_rows:
        gross_profit = total_revenue - total_cogs
        gross_margin = gross_profit / total_revenue if total_revenue else 0
    else:
        gross_profit = 0.0
        gross_margin = 0.0

    # ── 1. CAPEX spike relative to revenue (WorldCom pattern) ─────────────────
    if total_revenue > 0 and total_capex > 0:
        capex_pct = total_capex / total_revenue
        if capex_pct > 0.25:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "red",
                "merchant": "CAPEX/REVENUE RATIO",
                "amount": total_capex,
                "transaction_date": "",
                "description": (
                    f"CAPEX is {capex_pct:.0%} of revenue (${total_capex:,.0f} of ${total_revenue:,.0f}). "
                    "WorldCom pattern: operating expenses reclassified as capital expenditure "
                    "to avoid recognising them as period costs, inflating EBITDA and net income. "
                    "Verify: (1) nature of capitalised items — are they truly long-lived assets? "
                    "(2) compare to prior periods — sudden CAPEX spike is a key red flag. "
                    "(3) inspect GL detail for 'system development', 'line costs', or 'maintenance' "
                    "reclassified as PP&E."
                ),
                "library_match": "WORLDCOM_CAPEX_MANIPULATION",
                "confidence_weight": 0.80,
            })

    # ── 2. Missing depreciation on an asset-heavy business ────────────────────
    if asset_rows and not depr_rows:
        results.append({
            "signal_type": "pl_forensics",
            "severity": "amber",
            "merchant": "MISSING DEPRECIATION",
            "amount": 0,
            "transaction_date": "",
            "description": (
                "Fixed assets appear on the P&L/balance sheet but no depreciation expense is recorded. "
                "Waste Management pattern: extending asset useful lives to reduce depreciation, "
                "inflating reported earnings. "
                "Obtain fixed asset register — verify asset classes, acquisition dates, "
                "useful life assumptions, and accumulated depreciation. "
                "Missing depreciation understates expenses and overstates net income."
            ),
            "library_match": "WASTE_MGMT_DEPR_EXTENSION",
            "confidence_weight": 0.75,
        })

    # ── 3. Depreciation implausibly low relative to CAPEX ─────────────────────
    if total_capex > 0 and total_depr > 0:
        depr_capex_ratio = total_depr / total_capex
        if depr_capex_ratio < 0.05:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "amber",
                "merchant": "LOW DEPRECIATION vs CAPEX",
                "amount": total_depr,
                "transaction_date": "",
                "description": (
                    f"Depreciation (${total_depr:,.0f}) is only {depr_capex_ratio:.1%} of CAPEX (${total_capex:,.0f}). "
                    "Abnormally low depreciation relative to capital base may indicate: "
                    "(1) recently extended useful-life assumptions (Waste Management pattern), "
                    "(2) assets classified as non-depreciable (land vs improvements), "
                    "(3) new CAPEX not yet placed in service. "
                    "Verify useful life schedules against industry norms."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # ── 4. Gross margin implausibility for business type ──────────────────────
    if total_revenue > 50000 and gross_margin > 0:
        if gross_margin > 0.85:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "amber",
                "merchant": "GROSS MARGIN ANOMALY",
                "amount": gross_profit,
                "transaction_date": "",
                "description": (
                    f"Gross margin is {gross_margin:.0%} (${gross_profit:,.0f} on ${total_revenue:,.0f} revenue). "
                    "Margins above 85% are unusual for most industries except pure SaaS and IP licensing. "
                    "Verify: (1) whether COGS are being suppressed or deferred to inflate margins, "
                    "(2) whether direct costs have been reclassified as overheads, "
                    "(3) compare to prior periods and industry benchmarks."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })
        elif cogs_rows and gross_margin < 0:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "red",
                "merchant": "NEGATIVE GROSS MARGIN",
                "amount": gross_profit,
                "transaction_date": "",
                "description": (
                    f"Negative gross margin: COGS (${total_cogs:,.0f}) exceeds revenue (${total_revenue:,.0f}). "
                    "Business is selling below cost — not sustainable without outside capital. "
                    "Verify whether all COGS are properly categorised and whether "
                    "any non-recurring costs have been included in cost of sales."
                ),
                "library_match": None,
                "confidence_weight": 0.85,
            })

    # ── 5. Revenue vs AR divergence (Xerox / accelerated recognition) ─────────
    if total_revenue > 0 and total_ar > 0:
        ar_days = (total_ar / total_revenue) * 365
        if ar_days > 90:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "amber",
                "merchant": "HIGH AR DAYS",
                "amount": total_ar,
                "transaction_date": "",
                "description": (
                    f"Accounts receivable days: {ar_days:.0f} days "
                    f"(${total_ar:,.0f} AR on ${total_revenue:,.0f} revenue). "
                    "Xerox pattern: recognising revenue from long-term leases/contracts upfront "
                    "while cash collection is deferred. High AR days can also indicate: "
                    "(1) revenue recognised before collection is probable, "
                    "(2) disputed invoices, (3) related-party receivables unlikely to be collected. "
                    "Obtain AR aging schedule and identify any receivables >90 days."
                ),
                "library_match": "XEROX_REVENUE_ACCELERATION",
                "confidence_weight": 0.70,
            })

    # ── 6. Revenue growing faster than cash (recognition timing risk) ─────────
    if transactions and total_revenue > 0:
        total_cash_inflows = sum(t["amount"] for t in transactions if t["amount"] > 0)
        if total_cash_inflows > 0:
            cash_revenue_ratio = total_cash_inflows / total_revenue
            if cash_revenue_ratio < 0.60:
                results.append({
                    "signal_type": "pl_forensics",
                    "severity": "amber",
                    "merchant": "REVENUE vs CASH DIVERGENCE",
                    "amount": total_revenue - total_cash_inflows,
                    "transaction_date": "",
                    "description": (
                        f"Reported revenue (${total_revenue:,.0f}) is {1/cash_revenue_ratio:.1f}x "
                        f"cash inflows (${total_cash_inflows:,.0f}). "
                        "Revenue significantly exceeding cash received may indicate: "
                        "(1) premature revenue recognition — income recognised before cash collected, "
                        "(2) high proportion of non-cash items (barter, related party), "
                        "(3) uncollectible receivables inflating income. "
                        "Cross-reference with cash flow statement and AR aging."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.70,
                })

    # ── 7. Cookie-jar reserves (Sunbeam pattern) ──────────────────────────────
    if reserve_rows:
        total_reserves = sum(_row_amount(r) for r in reserve_rows)
        if total_reserves > 0 and total_revenue > 0 and total_reserves / total_revenue > 0.05:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "amber",
                "merchant": "LARGE RESERVE BALANCES",
                "amount": total_reserves,
                "transaction_date": "",
                "description": (
                    f"Reserves/provisions: ${total_reserves:,.0f} ({total_reserves/total_revenue:.0%} of revenue). "
                    "Sunbeam 'cookie-jar' pattern: excessive reserves created in bad years, "
                    "then released in good years to smooth earnings. "
                    "Verify: (1) basis for each reserve — is it supported by a specific liability? "
                    "(2) whether reserves changed between periods — large releases can inflate income. "
                    "(3) review for 'big bath' charges preceding an acquisition."
                ),
                "library_match": "SUNBEAM_COOKIE_JAR",
                "confidence_weight": 0.65,
            })

    # ── 8. Management fee / owner draw concentration ──────────────────────────
    if total_revenue > 0 and total_mgmt_fees > 0:
        mgmt_pct = total_mgmt_fees / total_revenue
        if mgmt_pct > 0.15:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "amber",
                "merchant": "HIGH MANAGEMENT FEES",
                "amount": total_mgmt_fees,
                "transaction_date": "",
                "description": (
                    f"Management fees/owner compensation: ${total_mgmt_fees:,.0f} "
                    f"({mgmt_pct:.0%} of revenue). "
                    "Fees above 15% of revenue warrant scrutiny in an acquisition context: "
                    "(1) are fees paid to related parties at arm's-length rates? "
                    "(2) will fees continue post-acquisition or are they owner distributions? "
                    "(3) EBITDA should be normalised for owner compensation exceeding market rate. "
                    "Obtain management agreement and verify market-rate benchmarks."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # ── 9. Missing COGS for a product business ────────────────────────────────
    if revenue_rows and not cogs_rows and total_revenue > 100000:
        results.append({
            "signal_type": "pl_forensics",
            "severity": "amber",
            "merchant": "MISSING COGS LINE",
            "amount": total_revenue,
            "transaction_date": "",
            "description": (
                f"Revenue of ${total_revenue:,.0f} reported but no cost of goods/services line is present. "
                "For any business selling physical goods or direct services, missing COGS "
                "results in 100% gross margin — almost certainly an incomplete or manipulated P&L. "
                "Verify whether direct costs have been misclassified as operating expenses "
                "or omitted entirely. Request trial balance and full chart of accounts."
            ),
            "library_match": None,
            "confidence_weight": 0.70,
        })

    # ── 10. EBITDA margin implausibly high ────────────────────────────────────
    if ebitda_rows and total_revenue > 50000:
        total_ebitda = sum(_row_amount(r) for r in ebitda_rows)
        ebitda_margin = total_ebitda / total_revenue if total_revenue else 0
        if ebitda_margin > 0.50:
            results.append({
                "signal_type": "pl_forensics",
                "severity": "amber",
                "merchant": "HIGH EBITDA MARGIN",
                "amount": total_ebitda,
                "transaction_date": "",
                "description": (
                    f"EBITDA margin: {ebitda_margin:.0%} (${total_ebitda:,.0f} on ${total_revenue:,.0f}). "
                    "EBITDA above 50% is exceptional — most industries range 10–30%. "
                    "Verify: (1) are all operating expenses included? "
                    "(2) are any recurring costs being excluded from the EBITDA calculation? "
                    "(3) does the seller's EBITDA definition add back non-recurring items "
                    "that are actually recurring? "
                    "Request a full reconciliation from net income to reported EBITDA."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    return results
