"""Signal 43: Inventory Risk & Supply Chain Health.

Inventory is often the most misstated asset in SME acquisitions.
Common schemes: inflating inventory value, including obsolete or
non-existent stock, and understating inventory write-downs to boost
reported profit. A buyer who overpays for inventory pays twice.

Patterns detected:
  Inventory financing (floorplan lending): High-interest, asset-backed
    financing where inventory IS the collateral. If the business defaults,
    the lender takes the inventory — leaving buyer with empty shelves.
  COGS-to-bank ratio divergence: If COGS on P&L significantly exceeds
    cash payments to suppliers, inventory may be inflated.
  Write-down payments: Audit-driven or voluntary inventory write-downs
    indicate obsolete or damaged stock.
  Inventory build pre-sale: Sudden increase in supplier payments before
    sale date inflates the asset base and working capital peg.
  Just-in-time dependency: Very low supplier payment variability indicates
    JIT supply chain — vulnerable to disruption.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime

FLOORPLAN_KW = [
    "FLOORPLAN", "FLOOR PLAN", "INVENTORY FINANCE", "INVENTORY LOAN",
    "NEXTGEAR", "AFC ", "DEALERTRACK", "MANHEIM FINANCE",
    "CURTAILMENT", "FLOORPLAN PAYMENT",
]

INVENTORY_WRITE_KW = [
    "INVENTORY WRITE", "WRITE-DOWN", "INVENTORY ADJUSTMENT",
    "OBSOLETE INVENTORY", "SCRAP SALE", "LIQUIDATION",
]

LARGE_SUPPLIER_SPIKE_THRESHOLD = 3.0  # 3x average = spike


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


def _parse_date(d):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt)
        except:
            pass
    return None


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    floorplan_txns, writedown_txns = [], []

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in FLOORPLAN_KW):
            floorplan_txns.append(t)
        if any(kw in merchant for kw in INVENTORY_WRITE_KW):
            writedown_txns.append(t)

    if floorplan_txns:
        total = sum(abs(t["amount"]) for t in floorplan_txns)
        results.append({
            "signal_type": "inventory_risk",
            "severity": "red",
            "merchant": f"FLOORPLAN / INVENTORY FINANCING: ${total:,.0f}",
            "amount": -total,
            "transaction_date": floorplan_txns[0].get("transaction_date", ""),
            "description": (
                f"Inventory financing (floorplan lending): {len(floorplan_txns)} transactions "
                f"totalling ${total:,.0f}. "
                "Floorplan financing is common in auto dealerships, equipment dealers, and "
                "distributors — the lender holds a security interest in all financed inventory. "
                "CRITICAL: (1) Floorplan lender must be paid off at closing — "
                "failure to do so means they can repossess the inventory post-close, "
                "(2) Determine current outstanding floorplan balance (not just payments), "
                "(3) Curtailment schedules indicate aged inventory (units held past limit are curtailed), "
                "(4) Floorplan interest is a real operating cost — ensure it's in EBITDA, "
                "(5) Title search on all financed inventory to confirm clean chain of title. "
                "Obtain full floorplan agreement and current outstanding schedule."
            ),
            "library_match": "INVENTORY_FLOORPLAN",
            "confidence_weight": 0.85,
        })

    if writedown_txns:
        total = sum(abs(t["amount"]) for t in writedown_txns)
        results.append({
            "signal_type": "inventory_risk",
            "severity": "amber",
            "merchant": f"INVENTORY WRITE-DOWNS: ${total:,.0f}",
            "amount": -total,
            "transaction_date": writedown_txns[0].get("transaction_date", ""),
            "description": (
                f"Inventory write-down or liquidation activity: ${total:,.0f}. "
                "Write-downs indicate obsolete, damaged, or excess inventory. "
                "The remaining inventory value on the balance sheet must be independently verified — "
                "request a physical inventory count as a condition of closing. "
                "For manufacturing: verify BOM (Bill of Materials) accuracy and raw material valuations. "
                "For retail: verify all inventory is current, saleable, and free of supplier return rights."
            ),
            "library_match": "INVENTORY_WRITEDOWN",
            "confidence_weight": 0.70,
        })

    # Pre-sale inventory build: supplier spend spike in final months
    if pl_rows and transactions:
        monthly_supplier: dict[str, float] = defaultdict(float)
        for t in transactions:
            if t["amount"] >= 0:
                continue
            m = t["merchant"].upper()
            if any(kw in m for kw in ["SUPPLIER", "WHOLESALE", "DISTRIBUTOR", "INVENTORY", "MATERIAL"]):
                d = _parse_date(t.get("transaction_date", ""))
                if d:
                    monthly_supplier[f"{d.year}-{d.month:02d}"] += abs(t["amount"])

        if len(monthly_supplier) >= 4:
            months = sorted(monthly_supplier.keys())
            vals = [monthly_supplier[m] for m in months]
            avg = sum(vals[:-2]) / max(len(vals) - 2, 1)
            recent_avg = sum(vals[-2:]) / 2
            if avg > 0 and recent_avg > avg * 2.0:
                results.append({
                    "signal_type": "inventory_risk",
                    "severity": "amber",
                    "merchant": f"PRE-SALE INVENTORY BUILD: {recent_avg/avg:.1f}x normal supplier spend",
                    "amount": -(recent_avg - avg) * 2,
                    "transaction_date": months[-1],
                    "description": (
                        f"Supplier payments in the last 2 months (${recent_avg:,.0f}/month avg) "
                        f"are {recent_avg/avg:.1f}x the prior period average (${avg:,.0f}/month). "
                        "A pre-sale inventory build inflates current assets and the working capital peg. "
                        "If the buyer is paying for inflated inventory in the WC calculation, "
                        "they may be overpaying for stock that won't sell at the same rate post-close. "
                        "Request physical inventory count and aged inventory report as of closing date."
                    ),
                    "library_match": "INVENTORY_PRESALE_BUILD",
                    "confidence_weight": 0.65,
                })

    return results
