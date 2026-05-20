"""Signal 48: Cash Flow Reconstruction & EBITDA Bridge.

Bank statement data is the ground truth of business cash flow. By
reconstructing operating, investing, and financing cash flows from
raw transactions, we can build an independent EBITDA bridge that
either corroborates or contradicts reported financials.

Why this matters:
  The EBITDA number on a P&L can be manipulated through revenue
  recognition timing, expense deferral, and fictitious entries.
  Bank cash flow cannot be easily fabricated — money either moved
  or it did not.

Key divergence patterns:
  EBITDA-to-Cash conversion < 60%: A business reporting $500k EBITDA
    but only generating $300k in operating cash is either:
    (a) growing accounts receivable (real growth), or
    (b) inflating revenue without collecting it (fraud signal).
  Negative operating cash flow with positive EBITDA: Only sustainable
    if the business is investing heavily in AR/inventory growth. If
    neither is true, this is a critical red flag.
  Financing inflows masking operating weakness: If total deposits are
    healthy but a significant portion are loan draws, line-of-credit
    advances, or capital injections, operating performance is worse
    than headline numbers suggest.
  Owner distributions exceeding net income: Consistent over-distribution
    depletes working capital and signals the owner treats the business
    as a personal cash machine rather than an operating entity.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict

FINANCING_INFLOW_KW = [
    "LOAN PROCEEDS", "LINE OF CREDIT", "LOC ADVANCE", "SBA LOAN",
    "CREDIT LINE ADVANCE", "TERM LOAN", "BANK LOAN", "EQUIPMENT LOAN",
    "INVESTOR CAPITAL", "CAPITAL CONTRIBUTION", "SHAREHOLDER LOAN",
    "OWNER CONTRIBUTION", "MEMBER CONTRIBUTION",
]

FINANCING_OUTFLOW_KW = [
    "LOAN PAYMENT", "PRINCIPAL PAYMENT", "LOC PAYMENT", "LINE OF CREDIT PAYMENT",
    "DEBT SERVICE", "MORTGAGE PAYMENT", "SBA PAYMENT",
    "OWNER DRAW", "OWNER DISTRIBUTION", "MEMBER DRAW", "SHAREHOLDER DISTRIBUTION",
    "DIVIDEND PAYMENT",
]

CAPEX_KW = [
    "EQUIPMENT PURCHASE", "MACHINERY", "VEHICLE PURCHASE", "PROPERTY PURCHASE",
    "LEASEHOLD IMPROVEMENT", "RENOVATION", "CONSTRUCTION PAYMENT",
    "CATERPILLAR", "JOHN DEERE", "KOMATSU",
]

COGS_KW = ["COST OF GOODS", "COGS", "DIRECT COST", "COST OF SALES"]
REVENUE_KW = ["REVENUE", "SALES", "NET SALES"]
EBITDA_KW = ["EBITDA", "OPERATING INCOME", "OPERATING PROFIT", "EBIT"]


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


def _sum_rows(pl_rows, *kw_lists) -> float:
    total = 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        for kws in kw_lists:
            if any(kw in acc for kw in kws):
                total += _row_amount(r)
                break
    return total


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []

    # Classify all transactions
    financing_inflows, financing_outflows, capex_txns = [], [], []
    owner_draws = []

    for t in transactions:
        m = t["merchant"].upper()
        amt = t["amount"]
        if amt > 0 and any(kw in m for kw in FINANCING_INFLOW_KW):
            financing_inflows.append(t)
        if amt < 0 and any(kw in m for kw in FINANCING_OUTFLOW_KW):
            if any(kw in m for kw in ["OWNER DRAW", "MEMBER DRAW", "SHAREHOLDER DISTRIB", "DISTRIBUTION", "DIVIDEND"]):
                owner_draws.append(t)
            else:
                financing_outflows.append(t)
        if amt < 0 and any(kw in m for kw in CAPEX_KW):
            capex_txns.append(t)

    # Totals from bank
    total_deposits = sum(t["amount"] for t in transactions if t["amount"] > 0)
    total_withdrawals = sum(abs(t["amount"]) for t in transactions if t["amount"] < 0)
    financing_in = sum(t["amount"] for t in financing_inflows)
    financing_out = sum(abs(t["amount"]) for t in financing_outflows)
    capex_out = sum(abs(t["amount"]) for t in capex_txns)
    draw_total = sum(abs(t["amount"]) for t in owner_draws)

    # Estimated operating cash flow (exclude financing and CAPEX)
    operating_inflow = total_deposits - financing_in
    operating_outflow = total_withdrawals - financing_out - capex_out - draw_total
    bank_operating_cf = operating_inflow - operating_outflow

    # P&L comparison
    reported_ebitda = 0.0
    if pl_rows:
        reported_ebitda = _sum_rows(pl_rows, EBITDA_KW)
        if reported_ebitda == 0:
            # Estimate from revenue - COGS - operating expenses
            revenue = _sum_rows(pl_rows, REVENUE_KW)
            cogs = abs(_sum_rows(pl_rows, COGS_KW))
            reported_ebitda = revenue - cogs  # gross profit as proxy

    # ── Financing inflows masking operating weakness ───────────────────────
    if financing_in > 0 and total_deposits > 0:
        financing_pct = financing_in / total_deposits
        if financing_pct > 0.25:
            results.append({
                "signal_type": "cashflow_reconstruction",
                "severity": "red",
                "merchant": f"LOAN PROCEEDS: {financing_pct:.0%} of total deposits are debt",
                "amount": financing_in,
                "transaction_date": financing_inflows[0].get("transaction_date", ""),
                "description": (
                    f"Financing inflows (loan draws, LOC advances, capital contributions): "
                    f"${financing_in:,.0f} = {financing_pct:.0%} of total deposits (${total_deposits:,.0f}). "
                    "When debt proceeds represent a significant share of total cash inflows, "
                    "the headline revenue and cash flow figures are inflated by non-operating sources. "
                    "TRUE OPERATING PERFORMANCE: strip financing inflows to isolate "
                    f"operating-only deposits of ~${operating_inflow:,.0f}. "
                    "Verify whether the business requires ongoing credit line draws to fund "
                    "day-to-day operations — a structural dependency on revolving debt is a "
                    "liquidity risk that must be factored into working capital requirements at close."
                ),
                "library_match": "CF_FINANCING_MASK",
                "confidence_weight": 0.80,
            })

    # ── EBITDA-to-Cash conversion gap ─────────────────────────────────────
    if reported_ebitda > 50000 and bank_operating_cf > 0:
        conversion = bank_operating_cf / reported_ebitda
        if conversion < 0.60:
            results.append({
                "signal_type": "cashflow_reconstruction",
                "severity": "amber",
                "merchant": f"EBITDA-CASH GAP: bank CF is {conversion:.0%} of reported EBITDA",
                "amount": bank_operating_cf - reported_ebitda,
                "transaction_date": "",
                "description": (
                    f"Reported EBITDA: ${reported_ebitda:,.0f}. "
                    f"Bank-derived operating cash flow: ${bank_operating_cf:,.0f} "
                    f"({conversion:.0%} conversion). "
                    "An EBITDA-to-cash conversion below 60% means the business is not "
                    "converting its reported earnings into actual cash at a healthy rate. "
                    "Possible explanations: (1) accounts receivable is growing faster than revenue "
                    "(real growth scenario — verify AR aging), (2) inventory is being built up "
                    "(verify inventory balance), (3) revenue is being recognised before cash is "
                    "collected (accrual timing mismatch), or (4) reported EBITDA is overstated. "
                    "Request a full cash flow statement and reconcile to bank records."
                ),
                "library_match": "CF_EBITDA_GAP",
                "confidence_weight": 0.65,
            })

    # ── Owner distributions exceeding reasonable compensation ─────────────
    if draw_total > 0 and total_deposits > 0:
        draw_pct = draw_total / total_deposits
        if draw_pct > 0.40:
            results.append({
                "signal_type": "cashflow_reconstruction",
                "severity": "amber",
                "merchant": f"OWNER DRAWS: ${draw_total:,.0f} = {draw_pct:.0%} of total deposits",
                "amount": -draw_total,
                "transaction_date": owner_draws[0].get("transaction_date", "") if owner_draws else "",
                "description": (
                    f"Owner draws and distributions: ${draw_total:,.0f} = {draw_pct:.0%} of total deposits. "
                    "Heavy owner distributions relative to revenue reduce the working capital "
                    "buffer available for operations and debt service. "
                    "For SDE normalisation: confirm whether draws represent compensation "
                    "(addback for new owner's income potential) or excess extraction above "
                    "reasonable compensation (signals the business was managed for personal "
                    "cash flow rather than operational health). "
                    "Assess post-acquisition working capital adequacy net of prior draw patterns."
                ),
                "library_match": "CF_OWNER_DRAWS",
                "confidence_weight": 0.60,
            })

    # ── Negative net cash position ─────────────────────────────────────────
    net_cash = total_deposits - total_withdrawals
    if net_cash < -50000:
        results.append({
            "signal_type": "cashflow_reconstruction",
            "severity": "red",
            "merchant": f"NET CASH OUTFLOW: ${abs(net_cash):,.0f} more out than in",
            "amount": net_cash,
            "transaction_date": "",
            "description": (
                f"Over the analysis period, total outflows (${total_withdrawals:,.0f}) "
                f"exceed total inflows (${total_deposits:,.0f}) by ${abs(net_cash):,.0f}. "
                "A business burning cash over the observation period is either: "
                "(1) funding growth with outside capital (verify capital sources), "
                "(2) in operational decline (revenue falling, costs fixed), or "
                "(3) was carrying a prior cash surplus that is being depleted. "
                "Confirm the business's current cash balance and runway. "
                "Identify whether the negative trend is reversing or accelerating."
            ),
            "library_match": "CF_NEGATIVE_NET",
            "confidence_weight": 0.75,
        })

    return results
