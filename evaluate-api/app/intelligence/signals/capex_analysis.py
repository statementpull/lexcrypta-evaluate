"""Signal 45: Capital Expenditure Analysis.

CAPEX patterns reveal the true health of a business's physical infrastructure
and whether maintenance has been deferred to inflate reported earnings.

Two types of CAPEX:
  Growth CAPEX: New equipment, facilities, systems to expand capacity.
    Signals a business investing in its future — generally positive.
  Maintenance CAPEX: Replacement of worn-out assets to sustain current
    operations. REQUIRED for the business to continue functioning.

Why CAPEX matters in acquisitions:
  Deferred maintenance CAPEX: If the seller has deferred replacing aging
    equipment, the buyer faces an immediate capital call post-close.
    This is one of the most common forms of P&L inflation in asset-heavy businesses.
  CAPEX spike pre-sale: Sudden high CAPEX before sale can either be:
    (a) genuine growth investment (positive), or
    (b) an attempt to show the business is "investing" (cosmetic).
  Very low CAPEX: In a business with significant fixed assets, near-zero
    CAPEX is almost always deferred maintenance — a hidden liability.
  CAPEX vs Depreciation: If depreciation >> CAPEX for multiple years,
    the asset base is aging faster than it is being replaced.

Industry CAPEX/Revenue benchmarks (Damodaran NYU, 2024):
  Manufacturing: 4–8% of revenue
  Healthcare/Medical: 3–6%
  Retail: 2–4%
  Technology/Software: 1–3% (mostly maintenance)
  Restaurants: 3–6%
  Transportation/Logistics: 5–12%

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime

CAPEX_KEYWORDS = [
    "EQUIPMENT PURCHASE", "MACHINERY PURCHASE", "EQUIPMENT BUY",
    "CAPITAL EQUIPMENT", "VEHICLE PURCHASE", "TRUCK PURCHASE",
    "PROPERTY PURCHASE", "BUILDING PURCHASE", "LEASEHOLD IMPROVEMENT",
    "RENOVATION", "CONSTRUCTION PAYMENT", "CONTRACTOR PAYMENT",
    "CATERPILLAR", "JOHN DEERE", "KOMATSU", "CASE EQUIPMENT",
    "EQUIPMENT DEALER", "MACHINERY DEALER",
]

DEPR_KW = ["DEPRECIATION", "AMORTIZATION", "D&A"]
REVENUE_KW = ["REVENUE", "SALES", "NET SALES"]
ASSET_KW = ["TOTAL ASSETS", "FIXED ASSET", "PROPERTY", "EQUIPMENT", "PLANT"]


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


def _parse_date(d):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt)
        except:
            pass
    return None


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    results = []

    # P&L components
    depr = abs(_sum_rows(pl_rows, DEPR_KW)) if pl_rows else 0
    revenue = _sum_rows(pl_rows, REVENUE_KW) if pl_rows else 0
    total_assets = abs(_sum_rows(pl_rows, ASSET_KW)) if pl_rows else 0

    # CAPEX from bank transactions
    capex_txns = []
    for t in (transactions or []):
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in CAPEX_KEYWORDS):
            capex_txns.append(t)

    # Also detect large one-off outflows that may be CAPEX
    all_outflows = [t for t in (transactions or []) if t["amount"] < 0]
    if all_outflows:
        avg_outflow = sum(abs(t["amount"]) for t in all_outflows) / len(all_outflows)
        large_outflows = [t for t in all_outflows if abs(t["amount"]) > avg_outflow * 5
                          and abs(t["amount"]) > 10000]
        for t in large_outflows:
            if t not in capex_txns:
                capex_txns.append(t)

    bank_capex = sum(abs(t["amount"]) for t in capex_txns)

    # ── CAPEX vs Depreciation ratio ───────────────────────────────────────────
    if depr > 0 and bank_capex > 0:
        capex_to_depr = bank_capex / depr
        if capex_to_depr < 0.50:
            results.append({
                "signal_type": "capex_analysis",
                "severity": "red",
                "merchant": f"DEFERRED MAINTENANCE: CAPEX ${bank_capex:,.0f} = only {capex_to_depr:.0%} of depreciation",
                "amount": -bank_capex,
                "transaction_date": capex_txns[0].get("transaction_date", "") if capex_txns else "",
                "description": (
                    f"CAPEX (${bank_capex:,.0f}) is only {capex_to_depr:.0%} of depreciation (${depr:,.0f}). "
                    "A business spending less than 50% of its depreciation on replacement CAPEX "
                    "is consuming its asset base faster than it is renewing it — "
                    "a classic deferred maintenance scenario that inflates reported EBITDA. "
                    "The buyer faces immediate capital calls post-close to replace aging assets. "
                    "Required actions: (1) request an asset condition assessment from an "
                    "independent appraiser, (2) identify all assets within 1–3 years of "
                    "replacement — estimate replacement cost, (3) reduce EBITDA by normalised "
                    "maintenance CAPEX before applying acquisition multiples, "
                    "(4) negotiate purchase price reduction for deferred maintenance backlog."
                ),
                "library_match": "CAPEX_DEFERRED_MAINTENANCE",
                "confidence_weight": 0.80,
            })

    # ── CAPEX/Revenue ratio ────────────────────────────────────────────────────
    if revenue > 0 and bank_capex > 0:
        capex_pct = bank_capex / revenue
        if capex_pct > 0.10:
            results.append({
                "signal_type": "capex_analysis",
                "severity": "amber",
                "merchant": f"HIGH CAPEX INTENSITY: {capex_pct:.0%} of revenue",
                "amount": -bank_capex,
                "transaction_date": capex_txns[0].get("transaction_date", "") if capex_txns else "",
                "description": (
                    f"Capital expenditure ${bank_capex:,.0f} = {capex_pct:.0%} of revenue. "
                    "High CAPEX intensity reduces free cash flow available for debt service and distributions. "
                    "Verify: (1) whether CAPEX is growth or maintenance (different valuation treatment), "
                    "(2) whether the CAPEX spike is pre-sale (cosmetic investment vs real), "
                    "(3) whether the assets purchased generate incremental revenue "
                    "or simply maintain current capacity. "
                    "EBITDA-to-Free-Cash-Flow conversion ratio will be materially lower than EBITDA suggests."
                ),
                "library_match": "CAPEX_HIGH_INTENSITY",
                "confidence_weight": 0.60,
            })

    # ── Minimal CAPEX on asset-heavy business ────────────────────────────────
    if total_assets > 200000 and bank_capex < total_assets * 0.02 and depr > bank_capex:
        results.append({
            "signal_type": "capex_analysis",
            "severity": "amber",
            "merchant": f"MINIMAL CAPEX: ${bank_capex:,.0f} on ${total_assets:,.0f} asset base",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"Very low CAPEX (${bank_capex:,.0f}) relative to total assets (${total_assets:,.0f}). "
                "For asset-intensive businesses, near-zero CAPEX over a 12-month period "
                "almost always indicates deferred maintenance or an asset-light business model. "
                "If asset-light: verify revenue doesn't depend on aging infrastructure. "
                "If asset-heavy: commission an independent asset condition assessment. "
                "Determine remaining useful life of all major assets and build a "
                "5-year replacement schedule as part of the acquisition model."
            ),
            "library_match": "CAPEX_MINIMAL",
            "confidence_weight": 0.55,
        })

    return results
