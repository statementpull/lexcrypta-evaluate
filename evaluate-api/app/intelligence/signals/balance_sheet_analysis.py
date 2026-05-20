"""Signal 53: Balance Sheet Analysis — Leverage, Liquidity & Solvency.

The balance sheet is the single most important document in an acquisition.
It shows what the business owns, what it owes, and what the owners actually
have left after debts. A business that looks profitable on a P&L can be
technically insolvent on its balance sheet.

Key ratios:
  Current Ratio = Current Assets / Current Liabilities
    >2.0: Strong liquidity — business can meet short-term obligations comfortably
    1.0–2.0: Adequate — monitor closely
    <1.0: Current liabilities exceed current assets — WORKING CAPITAL CRISIS
    <0.5: Severe — business may be unable to meet payroll or supplier payments

  Quick Ratio = (Current Assets - Inventory) / Current Liabilities
    Strips inventory (least liquid) — better for asset-heavy businesses

  Debt-to-Equity = Total Liabilities / Total Equity
    >3.0: Highly leveraged — vulnerable to interest rate increases and revenue dips
    >5.0: Extreme leverage — typically only sustainable with strong recurring cash flow

  Net Worth (Book Value) vs Purchase Price:
    Purchase price > 5x book value: goodwill-heavy deal — value depends almost
    entirely on future earnings, not balance sheet assets. Risk increases.

  Negative equity: Total liabilities exceed total assets — the business is
    technically insolvent on book value. Seller may be motivated to sell at
    any price to exit the obligation.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""


def _sum_cat(rows: list[dict], *categories) -> float:
    return sum(r["amount"] for r in rows if r.get("category") in categories)


def run(transactions: list[dict], pl_rows: list[dict] | None = None,
        loader=None, supplementary: dict | None = None) -> list[dict]:
    bs_rows = (supplementary or {}).get("balance_sheet", [])
    if not bs_rows:
        return []

    results = []

    current_assets = abs(_sum_cat(bs_rows, "current_asset"))
    fixed_assets = abs(_sum_cat(bs_rows, "fixed_asset"))
    other_assets = abs(_sum_cat(bs_rows, "other_asset"))
    total_assets = current_assets + fixed_assets + other_assets

    current_liab = abs(_sum_cat(bs_rows, "current_liability"))
    longterm_liab = abs(_sum_cat(bs_rows, "long_term_liability"))
    total_liab = current_liab + longterm_liab

    equity = _sum_cat(bs_rows, "equity")

    if total_assets == 0:
        return []

    # Estimate inventory from rows
    inventory = sum(
        abs(r["amount"]) for r in bs_rows
        if any(kw in r.get("account", "").upper() for kw in
               ["INVENTORY", "STOCK ON HAND", "STOCK-IN-TRADE"])
    )

    # ── Current Ratio ─────────────────────────────────────────────────────
    if current_liab > 0:
        current_ratio = current_assets / current_liab
        quick_ratio = max(current_assets - inventory, 0) / current_liab

        if current_ratio < 1.0:
            results.append({
                "signal_type": "balance_sheet_analysis",
                "severity": "red",
                "merchant": f"LIQUIDITY CRISIS: Current Ratio {current_ratio:.2f}x — liabilities exceed liquid assets",
                "amount": current_assets - current_liab,
                "transaction_date": "",
                "description": (
                    f"Current ratio: {current_ratio:.2f}x "
                    f"(current assets ${current_assets:,.0f} vs current liabilities ${current_liab:,.0f}). "
                    f"Quick ratio: {quick_ratio:.2f}x. "
                    "A current ratio below 1.0 means the business cannot meet its short-term "
                    "obligations from liquid assets alone. "
                    "Buyer risk: (1) working capital injection may be required immediately at close, "
                    "(2) the business may be using revolving credit to fund day-to-day operations — "
                    "verify current LOC utilisation, "
                    "(3) vendor and payroll payment may already be delayed — "
                    "verify aged payables and payroll run history, "
                    "(4) negotiate a working capital peg in the purchase agreement that reflects "
                    "this normalised deficit, not a seller-inflated 'target'."
                ),
                "library_match": "BS_LIQUIDITY_CRISIS",
                "confidence_weight": 0.90,
            })
        elif current_ratio < 1.5:
            results.append({
                "signal_type": "balance_sheet_analysis",
                "severity": "amber",
                "merchant": f"TIGHT LIQUIDITY: Current Ratio {current_ratio:.2f}x",
                "amount": current_assets - current_liab,
                "transaction_date": "",
                "description": (
                    f"Current ratio: {current_ratio:.2f}x — below the 1.5x threshold "
                    f"considered healthy for most industries. "
                    f"Quick ratio: {quick_ratio:.2f}x. "
                    "Limited working capital buffer — any revenue disruption post-close "
                    "could strain cash flow quickly. "
                    "Ensure the working capital peg in the purchase agreement reflects "
                    "a normalised, adequate WC target — not the current stressed level."
                ),
                "library_match": "BS_TIGHT_LIQUIDITY",
                "confidence_weight": 0.75,
            })

    # ── Debt-to-Equity ────────────────────────────────────────────────────
    if equity > 0 and total_liab > 0:
        de_ratio = total_liab / equity
        if de_ratio > 5.0:
            results.append({
                "signal_type": "balance_sheet_analysis",
                "severity": "red",
                "merchant": f"EXTREME LEVERAGE: D/E ratio {de_ratio:.1f}x",
                "amount": -total_liab,
                "transaction_date": "",
                "description": (
                    f"Debt-to-equity ratio: {de_ratio:.1f}x "
                    f"(total liabilities ${total_liab:,.0f} / equity ${equity:,.0f}). "
                    "Extreme leverage means the business is almost entirely debt-funded. "
                    "In an acquisition with additional acquisition debt (SBA loan), "
                    "the combined leverage could be unsustainable. "
                    "Assess whether the business generates sufficient free cash flow "
                    "to service both the existing debt and proposed acquisition financing. "
                    "Model debt service at 1.25x DSCR minimum — if coverage falls below this, "
                    "the deal structure needs revision."
                ),
                "library_match": "BS_EXTREME_LEVERAGE",
                "confidence_weight": 0.85,
            })
        elif de_ratio > 3.0:
            results.append({
                "signal_type": "balance_sheet_analysis",
                "severity": "amber",
                "merchant": f"HIGH LEVERAGE: D/E ratio {de_ratio:.1f}x",
                "amount": -total_liab,
                "transaction_date": "",
                "description": (
                    f"Debt-to-equity ratio: {de_ratio:.1f}x — elevated. "
                    f"Total liabilities: ${total_liab:,.0f}. Equity: ${equity:,.0f}. "
                    "Verify that all liabilities are disclosed and that the balance sheet "
                    "date is recent (within 90 days of letter of intent). "
                    "Off-balance-sheet liabilities (operating leases, personal guarantees, "
                    "EIDL/PPP obligations) may not be fully reflected."
                ),
                "library_match": "BS_HIGH_LEVERAGE",
                "confidence_weight": 0.75,
            })

    elif equity < 0:
        results.append({
            "signal_type": "balance_sheet_analysis",
            "severity": "red",
            "merchant": f"NEGATIVE EQUITY: Book value ${equity:,.0f} — technically insolvent",
            "amount": equity,
            "transaction_date": "",
            "description": (
                f"Total equity: ${equity:,.0f} (negative). "
                f"Total assets: ${total_assets:,.0f}. Total liabilities: ${total_liab:,.0f}. "
                "The business is technically balance-sheet insolvent — liabilities exceed assets. "
                "This does NOT necessarily mean the business is failing: "
                "(1) if it generates strong cash flow, negative book value can be sustainable, "
                "(2) goodwill and intangibles from prior acquisitions may have been written down, "
                "(3) excessive owner distributions over time can erode equity. "
                "However: in an acquisition, you are buying assets net of liabilities — "
                "ensure the purchase price reflects this reality. "
                "Obtain a detailed breakdown of how equity became negative."
            ),
            "library_match": "BS_NEGATIVE_EQUITY",
            "confidence_weight": 0.90,
        })

    # ── Asset composition summary (informational) ─────────────────────────
    if total_assets > 0:
        fixed_pct = fixed_assets / total_assets
        if fixed_pct > 0.70:
            results.append({
                "signal_type": "balance_sheet_analysis",
                "severity": "amber",
                "merchant": f"ASSET-HEAVY: {fixed_pct:.0%} of assets are fixed — check maintenance",
                "amount": fixed_assets,
                "transaction_date": "",
                "description": (
                    f"Fixed assets (${fixed_assets:,.0f}) represent {fixed_pct:.0%} of "
                    f"total assets (${total_assets:,.0f}). "
                    "Asset-heavy businesses have high maintenance CAPEX requirements. "
                    "Verify: (1) asset condition assessment — when were major assets last replaced? "
                    "(2) accumulated depreciation vs gross asset value — if fully depreciated "
                    "assets are still in use, replacement cost is not on the balance sheet, "
                    "(3) compare annual CAPEX spend to annual depreciation — "
                    "if CAPEX < depreciation, the asset base is shrinking."
                ),
                "library_match": "BS_ASSET_HEAVY",
                "confidence_weight": 0.65,
            })

    return results
