"""Signal 55: Inventory Valuation — Turnover, Obsolescence & Write-Down Risk.

Inventory sits on the balance sheet at cost, but its real value depends on
whether it can be sold. In an acquisition, overstated inventory is one of
the most common working capital traps:
  - Slow-moving stock may be unsaleable at any price
  - Obsolete inventory may need to be written down at close
  - Buyers who inherit inflated inventory overpay and are undercapitalised

Key metrics:
  Turnover ratio = Annual COGS / Average Inventory Value
    Industry benchmarks vary widely — the signal flags anomalies, not thresholds
    Very low turnover (<2x): slow-moving, high obsolescence risk
    Very high turnover (>20x): may indicate stockouts, lost sales risk

  Dead stock = items with zero or near-zero movement relative to value
    High-value items on the inventory report that generate no revenue
    signal either returns, write-offs not yet taken, or genuine obsolescence.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""


def run(transactions: list[dict], pl_rows: list[dict] | None = None,
        loader=None, supplementary: dict | None = None) -> list[dict]:
    inv_rows = (supplementary or {}).get("inventory", [])
    if not inv_rows:
        return []

    results = []

    total_value = sum(r.get("total_value", 0) for r in inv_rows)
    if total_value <= 0:
        return []

    total_items = len(inv_rows)

    # ── Obsolescence proxy: items with quantity > 0 but unit_cost = 0 ─────
    uncosted_items = [r for r in inv_rows if r.get("quantity", 0) > 0 and r.get("unit_cost", 0) == 0]
    if uncosted_items:
        results.append({
            "signal_type": "inventory_valuation",
            "severity": "amber",
            "merchant": f"UNCOSTED INVENTORY: {len(uncosted_items)} items have no unit cost recorded",
            "amount": len(uncosted_items),
            "transaction_date": "",
            "description": (
                f"{len(uncosted_items)} inventory items (of {total_items} total) show stock on hand "
                f"but no unit cost recorded. These items are either uncosted in the accounting system "
                f"or represent aged stock that was written to zero cost without being written off. "
                f"Buyers cannot value what they cannot cost — require the seller to provide "
                f"a costed inventory count before close. "
                f"Items affected: {', '.join(r['item'] for r in uncosted_items[:5])}"
                + (f" and {len(uncosted_items)-5} more" if len(uncosted_items) > 5 else "")
            ),
            "library_match": "INV_UNCOSTED_ITEMS",
            "confidence_weight": 0.70,
        })

    # ── Inventory turnover (if COGS available from P&L rows) ──────────────
    annual_cogs = 0.0
    if pl_rows:
        cogs_kw = ["COST OF", "COGS", "DIRECT COST", "COST OF SALES", "COST OF GOODS"]
        for r in pl_rows:
            acct = r.get("account", "").upper()
            if any(kw in acct for kw in cogs_kw):
                annual_cogs += abs(r.get("amount", 0))

    if annual_cogs > 0 and total_value > 0:
        turnover = annual_cogs / total_value

        if turnover < 2.0:
            results.append({
                "signal_type": "inventory_valuation",
                "severity": "red",
                "merchant": f"SLOW INVENTORY: Turnover {turnover:.1f}x — high obsolescence risk",
                "amount": -total_value,
                "transaction_date": "",
                "description": (
                    f"Inventory turnover: {turnover:.1f}x (COGS ${annual_cogs:,.0f} / "
                    f"inventory value ${total_value:,.0f}). "
                    f"A turnover ratio below 2.0x means the business holds more than 6 months "
                    f"of inventory relative to what it sells. "
                    "Buyer risk: (1) significant portion of inventory may be slow-moving or obsolete "
                    "and carried at cost on the balance sheet, "
                    "(2) require an independent physical count and condition assessment before close, "
                    "(3) negotiate a working capital adjustment that excludes or discounts "
                    "inventory aged beyond the business's normal selling cycle, "
                    "(4) verify the seller has not restated inventory values to inflate the balance sheet "
                    "in advance of the sale."
                ),
                "library_match": "INV_LOW_TURNOVER",
                "confidence_weight": 0.80,
            })
        elif turnover < 4.0:
            results.append({
                "signal_type": "inventory_valuation",
                "severity": "amber",
                "merchant": f"INVENTORY TURNOVER: {turnover:.1f}x — below typical healthy range",
                "amount": -total_value,
                "transaction_date": "",
                "description": (
                    f"Inventory turnover: {turnover:.1f}x (COGS ${annual_cogs:,.0f} / "
                    f"inventory ${total_value:,.0f}). "
                    "Below the 4x threshold common in product-based businesses. "
                    "Verify whether this reflects seasonality, a recent inventory build-up, "
                    "or genuine slow movement. "
                    "Request a breakdown of inventory by age bucket from the seller."
                ),
                "library_match": "INV_MODERATE_TURNOVER",
                "confidence_weight": 0.65,
            })

    # ── High-value concentration in few SKUs ──────────────────────────────
    if total_items >= 5:
        sorted_inv = sorted(inv_rows, key=lambda r: r.get("total_value", 0), reverse=True)
        top3_value = sum(r.get("total_value", 0) for r in sorted_inv[:3])
        top3_pct = top3_value / total_value if total_value else 0

        if top3_pct > 0.70:
            top3_names = [r["item"] for r in sorted_inv[:3]]
            results.append({
                "signal_type": "inventory_valuation",
                "severity": "amber",
                "merchant": f"INVENTORY CONCENTRATION: Top 3 SKUs = {top3_pct:.0%} of inventory value",
                "amount": top3_value,
                "transaction_date": "",
                "description": (
                    f"Top 3 inventory items account for {top3_pct:.0%} of total inventory value "
                    f"(${top3_value:,.0f} of ${total_value:,.0f}). "
                    f"Items: {', '.join(top3_names)}. "
                    "Concentrated inventory creates disposal risk if these specific items "
                    "become obsolete, are subject to supplier shortages, or cannot be moved post-close. "
                    "Verify the demand profile and shelf life for these specific items."
                ),
                "library_match": "INV_SKU_CONCENTRATION",
                "confidence_weight": 0.60,
            })

    # ── Zero-quantity items with residual value ────────────────────────────
    ghost_items = [r for r in inv_rows if r.get("quantity", 0) == 0 and r.get("total_value", 0) > 0]
    if ghost_items:
        ghost_value = sum(r.get("total_value", 0) for r in ghost_items)
        results.append({
            "signal_type": "inventory_valuation",
            "severity": "amber",
            "merchant": f"GHOST INVENTORY: {len(ghost_items)} items show $0 qty but non-zero value",
            "amount": ghost_value,
            "transaction_date": "",
            "description": (
                f"{len(ghost_items)} inventory items show zero quantity on hand but a total value "
                f"of ${ghost_value:,.0f} on the inventory report. "
                "These are likely accounting reconciliation errors — items that were physically depleted "
                "but not written off in the system, or items with incorrect unit of measure. "
                "These inflated values carry through to the balance sheet. "
                "Require the seller to reconcile and correct these items before close."
            ),
            "library_match": "INV_GHOST_ITEMS",
            "confidence_weight": 0.70,
        })

    return results
