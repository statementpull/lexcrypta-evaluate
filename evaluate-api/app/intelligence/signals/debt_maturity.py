"""Signal 33: Debt Maturity & Refinancing Risk.

Detects debt obligations that may come due soon after acquisition,
creating unexpected refinancing risk or cash calls for the buyer.

Key patterns:
  Balloon payments: Large lump-sum payments at end of loan term are common
    in commercial real estate and equipment loans. If a balloon is due
    within 12–24 months of acquisition, the buyer inherits that obligation.
  Short-term debt cycling: Repeated 6–12 month loans rolled over indicate
    a business reliant on short-term credit that may not be renewable at scale.
  Declining payment amounts: Interest-only payments followed by P&I
    suggest a recent debt restructuring or covenant waiver.
  Maturity cliff: Multiple debt instruments maturing in same period
    creates a refinancing cliff — especially risky in rising rate environment.
  LOC dependence at full utilisation: A line of credit that is never paid
    down suggests it is structurally part of the balance sheet, not
    a temporary liquidity tool.

Sources:
- SBA SOP 50 10 7 — existing debt treatment in acquisition loans
- FDIC Supervisory Guidance on commercial real estate concentration
- Moody's Analytics — refinancing risk in leveraged acquisitions

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime


LOAN_PAYMENT_KW = [
    "LOAN PAYMENT", "LOAN PMT", "NOTE PAYABLE", "DEBT SERVICE",
    "MORTGAGE PAYMENT", "COMMERCIAL MORTGAGE", "TERM LOAN",
    "SBA LOAN", "EIDL PAYMENT", "EQUIPMENT LOAN",
    "BALLOON PAYMENT", "PRINCIPAL PAYMENT",
]

LOC_KW = [
    "LINE OF CREDIT", "LOC DRAW", "LOC ADVANCE", "CREDIT LINE",
    "REVOLVING CREDIT", "BUSINESS LINE",
]


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []

    # Collect debt service payments by source
    debt_by_source: dict[str, list] = defaultdict(list)
    loc_txns = []

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in LOAN_PAYMENT_KW):
            key = merchant[:40].strip()
            debt_by_source[key].append(t)
        if any(kw in merchant for kw in LOC_KW):
            loc_txns.append(t)

    if not debt_by_source and not loc_txns:
        return []

    # ── Balloon payment detection ─────────────────────────────────────────────
    # A single payment >3x the average of prior payments from same source
    balloon_alerts = []
    for source, txns in debt_by_source.items():
        if len(txns) < 3:
            continue
        amounts = sorted([abs(t["amount"]) for t in txns], reverse=True)
        avg_excl_top = sum(amounts[1:]) / max(len(amounts) - 1, 1)
        if avg_excl_top > 0 and amounts[0] > avg_excl_top * 2.5:
            balloon_alerts.append({
                "source": source,
                "balloon_amt": amounts[0],
                "normal_amt": avg_excl_top,
                "date": max(t.get("transaction_date", "") for t in txns),
            })

    if balloon_alerts:
        for alert in balloon_alerts[:3]:
            results.append({
                "signal_type": "debt_maturity",
                "severity": "red",
                "merchant": f"BALLOON PAYMENT DETECTED: {alert['source'][:40]}",
                "amount": -alert["balloon_amt"],
                "transaction_date": alert["date"],
                "description": (
                    f"Balloon payment detected from '{alert['source'][:40]}': "
                    f"${alert['balloon_amt']:,.0f} vs normal payment of ${alert['normal_amt']:,.0f}. "
                    "Balloon payments indicate a loan that is coming due or has reached maturity. "
                    "Acquisition risk: (1) the balloon may require full payoff at acquisition "
                    "(change-of-control clause), (2) the balloon may have been funded by the "
                    "seller — the buyer must refinance into a new facility, "
                    "(3) in a rising rate environment, refinancing at current rates may "
                    "materially increase debt service. "
                    "Obtain full loan schedule: original principal, rate, maturity date, "
                    "balloon amount, and prepayment penalty."
                ),
                "library_match": "DEBT_BALLOON_PAYMENT",
                "confidence_weight": 0.80,
            })

    # ── Debt cycling / short-term rollover detection ──────────────────────────
    all_debt_txns = [t for txns in debt_by_source.values() for t in txns]
    if len(debt_by_source) >= 3:
        total_debt_service = sum(abs(t["amount"]) for t in all_debt_txns)
        results.append({
            "signal_type": "debt_maturity",
            "severity": "amber",
            "merchant": f"MULTIPLE DEBT FACILITIES: {len(debt_by_source)} separate obligations",
            "amount": -total_debt_service,
            "transaction_date": all_debt_txns[0].get("transaction_date", "") if all_debt_txns else "",
            "description": (
                f"{len(debt_by_source)} separate debt facilities identified, "
                f"total payments ${total_debt_service:,.0f}. "
                "Multiple debt facilities increase refinancing complexity at acquisition: "
                "each must be assessed for change-of-control provisions, "
                "payoff vs assumption, and SBA/lender subordination requirements. "
                "Sources: " + ", ".join(f"{s[:30]}" for s in list(debt_by_source.keys())[:5]) + ". "
                "Obtain payoff statements and full loan schedules for all facilities."
            ),
            "library_match": "DEBT_MULTIPLE_FACILITIES",
            "confidence_weight": 0.65,
        })

    # ── LOC at structural balance (never zeroed out) ──────────────────────────
    if loc_txns:
        loc_out = [t for t in loc_txns if t["amount"] < 0]  # repayments
        loc_in = [t for t in loc_txns if t["amount"] > 0]   # draws

        if loc_in and not loc_out:
            total_draws = sum(t["amount"] for t in loc_in)
            results.append({
                "signal_type": "debt_maturity",
                "severity": "amber",
                "merchant": f"LOC STRUCTURAL DEPENDENCY: ${total_draws:,.0f} drawn — no repayments",
                "amount": total_draws,
                "transaction_date": loc_in[0].get("transaction_date", ""),
                "description": (
                    f"Line of credit shows ${total_draws:,.0f} in draws with no repayments detected. "
                    "A LOC that is never repaid has effectively become term debt — "
                    "the business cannot operate without the credit line. "
                    "At acquisition: (1) lender may call the LOC on change of control — "
                    "confirm facility agreement, (2) buyer must either assume or replace "
                    "the credit facility on day one, (3) the LOC balance is a true liability "
                    "that reduces net acquisition value — include in debt-free/cash-free adjustment. "
                    "Obtain current LOC outstanding balance, facility limit, expiry date, and agreement."
                ),
                "library_match": "DEBT_LOC_STRUCTURAL",
                "confidence_weight": 0.70,
            })

    return results
