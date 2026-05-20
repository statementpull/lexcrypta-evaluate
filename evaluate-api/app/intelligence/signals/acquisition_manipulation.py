"""Signal 10: Acquisition Manipulation — pre-sale financial engineering patterns.

Patterns drawn from SEC enforcement cases:
- Under Armour (UA): revenue pull-forward / channel stuffing, Q4 spike followed by Q1 reversal
- MiMedX (MDXG): consignment fraud, inflated AR, large credit memos after period close
- WorldCom: capitalizing operating expenses as CAPEX to hide losses
- Enron: off-balance-sheet SPE structures, related party complexity
- Cendant: fictitious membership revenue, period-end entries
- Real estate CAP rate manipulation: exclusion of recurring costs pre-sale

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict
import re

# ── Thresholds ────────────────────────────────────────────────────────────────
Q4_PULL_FORWARD_THRESHOLD = 0.35   # Q4 > 35% of annual revenue = flag
Q1_REVERSAL_THRESHOLD = 0.40       # Q1 credits > 40% of Q4 revenue = reversal flag
AR_SPIKE_THRESHOLD = 0.50          # AR days increase > 50% = stuffing indicator
CAPEX_REVENUE_SPIKE = 0.25         # CAPEX/Revenue > 25% sudden jump = capitalization flag
CREDIT_MEMO_THRESHOLD = 0.10       # Credits > 10% of gross revenue = returns/reversals
RELATED_PARTY_PCT = 0.20           # Related party revenue > 20% of total = concentration risk
PERIOD_END_DAYS = 3                # Last N days of period = timing flag window


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    results = []

    # Build time-based aggregates
    monthly_credits: dict[int, float] = defaultdict(float)
    monthly_debits: dict[int, float] = defaultdict(float)
    credit_memos: list[dict] = []
    period_end_large: list[dict] = []

    for t in transactions:
        date_str = str(t.get("transaction_date", ""))
        try:
            month = int(date_str[5:7]) if len(date_str) >= 7 else 0
            day = int(date_str[8:10]) if len(date_str) >= 10 else 0
        except (ValueError, TypeError):
            month, day = 0, 0

        if t["amount"] > 0 and month:
            monthly_credits[month] += t["amount"]
        elif t["amount"] < 0 and month:
            monthly_debits[month] += abs(t["amount"])

        # Credit memo / reversal detection: large credits in Jan/Apr/Jul/Oct (quarter-start)
        if t["amount"] > 5000 and month in (1, 4, 7, 10):
            merchant = t["merchant"].upper()
            if any(kw in merchant for kw in ["CREDIT", "REFUND", "RETURN", "REVERSAL", "ADJUSTMENT", "MEMO"]):
                credit_memos.append(t)

        # Period-end large revenue entries
        if t["amount"] > 10000 and day >= 28:
            period_end_large.append(t)

    total_credits = sum(monthly_credits.values())
    total_debits = sum(monthly_debits.values())

    # ── Q4 revenue pull-forward detection ─────────────────────────────────────
    q4_total = sum(monthly_credits.get(m, 0) for m in (10, 11, 12))
    q1_total = sum(monthly_credits.get(m, 0) for m in (1, 2, 3))

    if total_credits > 50000 and q4_total > 0:
        q4_pct = q4_total / total_credits
        if q4_pct > Q4_PULL_FORWARD_THRESHOLD:
            results.append({
                "signal_type": "acquisition_manipulation",
                "severity": "amber",
                "merchant": "Q4 REVENUE CONCENTRATION",
                "amount": q4_total,
                "transaction_date": "",
                "description": (
                    f"Q4 revenue concentration: {q4_pct:.0%} of annual inflows received Oct–Dec "
                    f"(${q4_total:,.0f} of ${total_credits:,.0f}). "
                    "Pattern consistent with period-end revenue acceleration — channel stuffing, "
                    "pull-forward arrangements, or bill-and-hold schemes. "
                    "Verify: (1) customer contracts for Q4 urgency, (2) Q1 credit memos or returns, "
                    "(3) whether customers have informal return rights."
                ),
                "library_match": None,
                "confidence_weight": 0.70,
            })

        # Q1 reversal following Q4 spike
        if q4_pct > Q4_PULL_FORWARD_THRESHOLD and q1_total < q4_total * 0.4:
            results.append({
                "signal_type": "acquisition_manipulation",
                "severity": "red",
                "merchant": "Q1 REVERSAL PATTERN",
                "amount": q4_total - q1_total,
                "transaction_date": "",
                "description": (
                    f"Q4 revenue spike (${q4_total:,.0f}) followed by Q1 drop (${q1_total:,.0f}) — "
                    f"Q1 is only {q1_total/q4_total:.0%} of Q4. "
                    "Pattern consistent with revenue pull-forward that reversed in Q1. "
                    "Under Armour (2021 SEC consent): incentivised customers to accept early delivery "
                    "in Q4, then issued promotional credits in Q1. "
                    "Obtain Q1 credit memos, promotional allowances, and AR aging schedule."
                ),
                "library_match": "PULL_FORWARD_PATTERN",
                "confidence_weight": 0.80,
            })

    # ── Quarter-start credit memo reversal ────────────────────────────────────
    if credit_memos:
        total_reversals = sum(t["amount"] for t in credit_memos)
        if total_credits > 0 and total_reversals / total_credits > CREDIT_MEMO_THRESHOLD:
            results.append({
                "signal_type": "acquisition_manipulation",
                "severity": "red",
                "merchant": "CREDIT MEMO REVERSALS",
                "amount": total_reversals,
                "transaction_date": credit_memos[0].get("transaction_date", ""),
                "description": (
                    f"{len(credit_memos)} credit memos/returns at quarter-start totalling ${total_reversals:,.0f} "
                    f"({total_reversals/total_credits:.0%} of gross inflows). "
                    "Large quarter-start credits often reverse inflated prior-quarter revenue. "
                    "MiMedX pattern: products shipped on consignment at period-end, returned in Q1, "
                    "but revenue was recognised at shipment. "
                    "Verify whether prior-period revenue is overstated by the reversal amount."
                ),
                "library_match": "CHANNEL_STUFFING_REVERSAL",
                "confidence_weight": 0.80,
            })

    # ── Period-end revenue concentration ──────────────────────────────────────
    if len(period_end_large) >= 3:
        total_period_end = sum(t["amount"] for t in period_end_large)
        if total_credits > 0 and total_period_end / total_credits > 0.20:
            results.append({
                "signal_type": "acquisition_manipulation",
                "severity": "amber",
                "merchant": "PERIOD-END REVENUE CONCENTRATION",
                "amount": total_period_end,
                "transaction_date": period_end_large[0].get("transaction_date", ""),
                "description": (
                    f"{len(period_end_large)} transactions totalling ${total_period_end:,.0f} "
                    f"({total_period_end/total_credits:.0%} of total) in the last 3 days of the period. "
                    "Cendant/Enron pattern: entries booked at period-close to meet targets. "
                    "Verify supporting documentation — confirm revenue is legitimate and earned, "
                    "not a journal entry to hit a target."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # ── Symmetrical debit/credit pairs (round-trip test) ──────────────────────
    credit_by_merchant: dict[str, float] = defaultdict(float)
    debit_by_merchant: dict[str, float] = defaultdict(float)

    for t in transactions:
        merchant = t["merchant"].upper()
        if t["amount"] > 0:
            credit_by_merchant[merchant] += t["amount"]
        else:
            debit_by_merchant[merchant] += abs(t["amount"])

    for merchant in credit_by_merchant:
        if merchant in debit_by_merchant:
            c = credit_by_merchant[merchant]
            d = debit_by_merchant[merchant]
            if c > 10000 and d > 10000 and abs(c - d) / max(c, d) < 0.10:
                results.append({
                    "signal_type": "acquisition_manipulation",
                    "severity": "red",
                    "merchant": merchant,
                    "amount": c,
                    "transaction_date": "",
                    "description": (
                        f"Symmetrical flow: ${c:,.0f} received from '{merchant}' and "
                        f"${d:,.0f} paid back ({abs(c-d)/max(c,d):.1%} difference). "
                        "Enron round-trip pattern: asset or revenue is created on paper by exchanging "
                        "funds with a related or cooperative party, inflating revenue with no economic substance. "
                        "Verify commercial purpose — obtain underlying contracts and confirm "
                        "the entity is arm's-length."
                    ),
                    "library_match": "ROUND_TRIP_TRANSACTION",
                    "confidence_weight": 0.85,
                })

    # ── Sudden revenue spike from new counterparty ────────────────────────────
    # New merchants in last 20% of period that represent >25% of revenue = late-period stuffing
    all_dates = sorted(set(
        str(t.get("transaction_date", ""))
        for t in transactions if t.get("transaction_date")
    ))

    if len(all_dates) >= 10:
        cutoff_idx = int(len(all_dates) * 0.80)
        early_dates = set(all_dates[:cutoff_idx])
        late_dates = set(all_dates[cutoff_idx:])

        early_merchants = set(
            t["merchant"].upper() for t in transactions
            if str(t.get("transaction_date", "")) in early_dates and t["amount"] > 0
        )

        new_late_revenue: dict[str, float] = defaultdict(float)
        for t in transactions:
            if (str(t.get("transaction_date", "")) in late_dates
                    and t["amount"] > 0
                    and t["merchant"].upper() not in early_merchants):
                new_late_revenue[t["merchant"].upper()] += t["amount"]

        for merchant, total in new_late_revenue.items():
            if total_credits > 0 and total / total_credits > 0.15 and total > 20000:
                results.append({
                    "signal_type": "acquisition_manipulation",
                    "severity": "amber",
                    "merchant": merchant,
                    "amount": total,
                    "transaction_date": "",
                    "description": (
                        f"New revenue source in final 20% of period: '{merchant}' contributed "
                        f"${total:,.0f} ({total/total_credits:.0%} of total) with no prior history. "
                        "New, large, late-period revenue from unknown counterparties warrants "
                        "verification of: (1) contract authenticity, (2) whether customer is related party, "
                        "(3) subsequent collection — unpaid post-period = revenue recognition risk."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.70,
                })

    return results
