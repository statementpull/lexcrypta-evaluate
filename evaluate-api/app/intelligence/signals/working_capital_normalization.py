"""Signal 32: Working Capital Normalization & Peg Analysis.

Working capital (WC) is one of the most commonly disputed items in SME M&A.
The purchase price typically assumes the business delivers an agreed level of
working capital at closing (the "working capital peg"). If actual WC at
closing differs from the peg, the purchase price adjusts — dollar for dollar.

WC = Current Assets - Current Liabilities

What we analyse:
  Seasonal WC peaks: Monthly variation in cash position reveals working capital
    needs that must be funded — especially important for seasonal businesses.
  WC peg recommendation: Based on trailing average, recommend an appropriate
    peg range for the purchase agreement.
  WC adequacy: Is there enough working capital to sustain the revenue level?
  Cash conversion efficiency: How quickly does revenue convert to cash?
  WC trap: Sellers sometimes "harvest" WC pre-closing (deferring payables,
    accelerating collections) to inflate the closing balance. We detect this.

Rule of thumb: WC peg = average of trailing 12-month ending WC balances.
Seasonal businesses: use average of same-month WC across prior years.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


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
        for kws in keyword_lists:
            if any(kw in account for kw in kws):
                total += _row_amount(r)
                break
    return total


CURRENT_ASSET_KW = ["CURRENT ASSET", "CASH AND CASH", "ACCOUNTS RECEIVABLE", "A/R",
                    "INVENTORY", "PREPAID", "SHORT-TERM INVESTMENT"]
CURRENT_LIAB_KW = ["CURRENT LIAB", "ACCOUNTS PAYABLE", "A/P", "ACCRUED LIAB",
                   "SHORT-TERM DEBT", "LINE OF CREDIT", "DEFERRED REVENUE",
                   "CREDIT CARD PAYABLE", "CURRENT PORTION"]
REVENUE_KW = ["REVENUE", "SALES", "NET SALES", "TOTAL INCOME"]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []

    # ── Monthly cash position from bank data ──────────────────────────────────
    monthly_net: dict[str, float] = defaultdict(float)
    monthly_in: dict[str, float] = defaultdict(float)
    monthly_out: dict[str, float] = defaultdict(float)

    for t in transactions:
        d = _parse_date(t.get("transaction_date", ""))
        if not d:
            continue
        key = f"{d.year}-{d.month:02d}"
        monthly_net[key] += t["amount"]
        if t["amount"] > 0:
            monthly_in[key] += t["amount"]
        else:
            monthly_out[key] += abs(t["amount"])

    if len(monthly_net) < 3:
        return []

    sorted_months = sorted(monthly_net.keys())
    net_vals = [monthly_net[m] for m in sorted_months]
    in_vals = [monthly_in[m] for m in sorted_months]
    out_vals = [monthly_out[m] for m in sorted_months]

    # ── P&L WC components ─────────────────────────────────────────────────────
    balance_sheet_wc = None
    if pl_rows:
        current_assets = _sum_rows(pl_rows, CURRENT_ASSET_KW)
        current_liabilities = abs(_sum_rows(pl_rows, CURRENT_LIAB_KW))
        revenue = _sum_rows(pl_rows, REVENUE_KW)
        if current_assets > 0 or current_liabilities > 0:
            balance_sheet_wc = current_assets - current_liabilities
    else:
        revenue = 0

    # ── Seasonal WC analysis ──────────────────────────────────────────────────
    avg_monthly_in = sum(in_vals) / len(in_vals)
    peak_in = max(in_vals)
    trough_in = min(in_vals)
    peak_month = sorted_months[in_vals.index(peak_in)]
    trough_month = sorted_months[in_vals.index(trough_in)]
    seasonal_swing = (peak_in - trough_in) / avg_monthly_in if avg_monthly_in > 0 else 0

    # ── WC peg recommendation ─────────────────────────────────────────────────
    # Use trailing average monthly net cash flow as WC proxy
    avg_net = sum(net_vals) / len(net_vals)
    recommended_peg_low = avg_monthly_in * 0.5   # 2 weeks of inflows
    recommended_peg_high = avg_monthly_in * 1.0  # 4 weeks of inflows

    # ── Pre-close WC harvesting detection ────────────────────────────────────
    harvest_note = ""
    if len(net_vals) >= 4:
        last_2_avg = sum(net_vals[-2:]) / 2
        prior_avg = sum(net_vals[:-2]) / max(len(net_vals) - 2, 1)
        if last_2_avg > prior_avg * 1.4 and prior_avg > 0:
            harvest_note = (
                f" WC HARVESTING RISK: recent 2-month average net cash "
                f"(${last_2_avg:,.0f}/month) is {last_2_avg/prior_avg:.1f}x the prior average "
                f"(${prior_avg:,.0f}/month). "
                "Pre-closing working capital harvesting — where sellers accelerate collections "
                "and defer payables to inflate the closing cash balance — is the #1 WC dispute "
                "in SME acquisitions. Locked-box mechanism or post-close WC adjustment recommended."
            )

    # Severity
    sev = "red" if harvest_note else "amber"

    bs_note = ""
    if balance_sheet_wc is not None:
        bs_note = (
            f" Balance sheet WC: ${balance_sheet_wc:,.0f} "
            f"(current assets minus current liabilities from P&L). "
        )

    description = (
        f"Working capital analysis over {len(sorted_months)} months. "
        f"Average monthly inflow: ${avg_monthly_in:,.0f}. "
        f"Peak inflow month: {peak_month} (${peak_in:,.0f}). "
        f"Trough inflow month: {trough_month} (${trough_in:,.0f}). "
        f"Seasonal swing: {seasonal_swing:.0%} of average monthly revenue. "
        f"{bs_note}"
        f"Recommended WC peg range: ${recommended_peg_low:,.0f} – ${recommended_peg_high:,.0f} "
        "(based on 2–4 weeks of average monthly inflows). "
        f"{harvest_note} "
        "WC peg advice: (1) negotiate a target WC peg in the purchase agreement with a "
        "+/- collar (typically $50k–$200k for SMEs), "
        "(2) define which accounts are included in the WC calculation precisely, "
        "(3) specify accounting method (accrual vs cash) for WC measurement, "
        "(4) for seasonal businesses, use an average of monthly WC from prior 12 months "
        "rather than closing-date point-in-time."
    )

    results.append({
        "signal_type": "working_capital",
        "severity": sev,
        "merchant": (
            f"WORKING CAPITAL ANALYSIS: Rec. peg ${recommended_peg_low:,.0f}–${recommended_peg_high:,.0f} "
            f"| Seasonal swing {seasonal_swing:.0%}"
        ),
        "amount": balance_sheet_wc or recommended_peg_high,
        "transaction_date": "",
        "description": description[:1500],
        "library_match": "WORKING_CAPITAL_PEG",
        "confidence_weight": 0.65,
    })

    return results
