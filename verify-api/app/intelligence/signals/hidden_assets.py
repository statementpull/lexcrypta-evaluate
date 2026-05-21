"""Signal 09: Hidden Assets & Off-Balance-Sheet Intelligence.

Surfaces patterns consistent with concealed assets, suppressed liabilities,
phantom transactions, and pre-sale financial manipulation.

Language principle: We identify patterns for the deal team to investigate.
We do not conclude — we flag anomalies that warrant verification.

Sources: ACFE Fraud patterns, SEC enforcement actions, forensic accounting
principles (Schilit 'Financial Shenanigans'), FinCEN advisories.
"""
from collections import defaultdict
import re


_MONEY_RE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?")


def _parse_float(s: str) -> float:
    try:
        return float(re.sub(r"[,$\s]", "", str(s)))
    except (ValueError, TypeError):
        return 0.0


# ── Keyword banks ─────────────────────────────────────────────────────────────

DUPLICATE_WINDOW_DAYS = 3    # Same merchant + amount within N days = duplicate flag

GHOST_VENDOR_SIGNALS = [
    # Vendors with no clear business identity
    "CONSULTING", "ADVISORY", "SERVICES", "SOLUTIONS", "MANAGEMENT",
    "RESEARCH", "STRATEGY", "DEVELOPMENT", "MARKETING", "LOGISTICS",
]

RELATED_PARTY_NAME_WORDS = [
    # Generic words often used in related-party entity names
    "FAMILY", "TRUST", "ESTATE", "NOMINEE", "HOLDING",
]

ROUND_TRIP_THRESHOLD = 0.02   # Same amount in + out within 5 days = round-trip flag

# Expense suppression keywords — these categories suddenly dropping = pre-sale cleanup signal
MAINTENANCE_KEYWORDS = ["MAINTENANCE", "REPAIR", "SERVICE", "HVAC", "PLUMBING", "ELECTRICAL", "ROOFING"]
PAYROLL_KEYWORDS = ["PAYROLL", "ADP", "PAYCHEX", "GUSTO", "BAMBOOHR", "SALARY", "WAGES", "COMPENSATION"]
INSURANCE_KEYWORDS = ["INSURANCE", "INSUR", "COVERAGE", "PREMIUM", "POLICY"]
LEGAL_KEYWORDS = ["LAW FIRM", "ATTORNEY", "LEGAL", "COUNSEL", "LITIGATION", "BARRISTER", "SOLICITOR"]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    results = []

    # Index transactions
    by_merchant: dict[str, list] = defaultdict(list)
    by_date: dict[str, list] = defaultdict(list)
    outflows: list[dict] = []
    inflows: list[dict] = []

    for t in transactions:
        merchant = t["merchant"].upper()
        by_merchant[merchant].append(t)
        date_str = str(t.get("transaction_date", ""))
        by_date[date_str].append(t)
        if t["amount"] < 0:
            outflows.append(t)
        else:
            inflows.append(t)

    # ── Duplicate payment detection ───────────────────────────────────────────
    seen: dict[tuple, list] = defaultdict(list)
    for t in outflows:
        key = (t["merchant"].upper(), abs(t["amount"]))
        seen[key].append(t)

    for (merchant, amount), txns in seen.items():
        if len(txns) < 2 or amount < 500:
            continue
        # Check if any two are within DUPLICATE_WINDOW_DAYS
        dates = sorted(str(t.get("transaction_date", "")) for t in txns)
        for i in range(len(dates) - 1):
            if dates[i] == dates[i + 1]:
                results.append({
                    "signal_type": "hidden_assets",
                    "severity": "red",
                    "merchant": merchant,
                    "amount": -amount * 2,
                    "transaction_date": dates[i],
                    "description": (
                        f"Duplicate payment: identical amount ${amount:,.0f} paid to '{merchant}' "
                        f"on the same date ({dates[i]}). "
                        "Verify whether both payments are legitimate — duplicate payments may indicate "
                        "overpayment, ghost vendor, or payment fraud. Obtain supporting invoices for both."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.85,
                })

    # ── Round-trip transaction detection ─────────────────────────────────────
    # Same amount flowing out and back in within 5 days = potential round trip
    outflow_amounts: dict[float, list] = defaultdict(list)
    inflow_amounts: dict[float, list] = defaultdict(list)

    for t in outflows:
        amt = round(abs(t["amount"]), 2)
        if amt > 1000:
            outflow_amounts[amt].append(t)
    for t in inflows:
        amt = round(abs(t["amount"]), 2)
        if amt > 1000:
            inflow_amounts[amt].append(t)

    for amt, out_txns in outflow_amounts.items():
        if amt in inflow_amounts:
            results.append({
                "signal_type": "hidden_assets",
                "severity": "red",
                "merchant": f"ROUND-TRIP: {out_txns[0]['merchant']}",
                "amount": amt,
                "transaction_date": out_txns[0].get("transaction_date", ""),
                "description": (
                    f"Round-trip indicator: ${amt:,.0f} paid out to '{out_txns[0]['merchant']}' "
                    f"and identical amount received back. "
                    "Pattern consistent with artificial revenue inflation or asset cycling. "
                    "Verify commercial substance of both transactions — obtain contracts and invoices."
                ),
                "library_match": None,
                "confidence_weight": 0.75,
            })

    # ── Ghost vendor detection ────────────────────────────────────────────────
    for merchant, txns in by_merchant.items():
        if not txns or txns[0]["amount"] >= 0:
            continue
        total = sum(abs(t["amount"]) for t in txns)
        if total < 5000:
            continue
        # Generic name + large total = ghost vendor risk
        generic_score = sum(1 for kw in GHOST_VENDOR_SIGNALS if kw in merchant)
        if generic_score >= 2 and len(merchant.split()) <= 4:
            results.append({
                "signal_type": "hidden_assets",
                "severity": "amber",
                "merchant": merchant,
                "amount": -total,
                "transaction_date": txns[0].get("transaction_date", ""),
                "description": (
                    f"Potential ghost vendor: '{merchant}' — {len(txns)} payments totalling ${total:,.0f}. "
                    "Generic entity name with no specific business identifier. "
                    "Verify: (1) entity registration and ownership, (2) services actually received, "
                    "(3) relationship to directors or principals. Request supporting invoices and contracts."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # ── Expense suppression detection ─────────────────────────────────────────
    # Split transactions into first half and second half of the period
    all_dates = sorted(set(
        str(t.get("transaction_date", "")) for t in transactions
        if t.get("transaction_date")
    ))

    if len(all_dates) >= 6:
        mid_idx = len(all_dates) // 2
        early_dates = set(all_dates[:mid_idx])
        late_dates = set(all_dates[mid_idx:])

        for category_name, keywords in [
            ("Maintenance/Repair", MAINTENANCE_KEYWORDS),
            ("Payroll/Staffing", PAYROLL_KEYWORDS),
            ("Insurance", INSURANCE_KEYWORDS),
            ("Legal/Professional", LEGAL_KEYWORDS),
        ]:
            early_spend = sum(
                abs(t["amount"]) for t in outflows
                if str(t.get("transaction_date", "")) in early_dates
                and any(kw in t["merchant"].upper() for kw in keywords)
            )
            late_spend = sum(
                abs(t["amount"]) for t in outflows
                if str(t.get("transaction_date", "")) in late_dates
                and any(kw in t["merchant"].upper() for kw in keywords)
            )

            if early_spend > 2000 and late_spend < early_spend * 0.3:
                results.append({
                    "signal_type": "hidden_assets",
                    "severity": "amber",
                    "merchant": f"EXPENSE SUPPRESSION — {category_name.upper()}",
                    "amount": -(early_spend - late_spend),
                    "transaction_date": "",
                    "description": (
                        f"Expense suppression pattern — {category_name}: "
                        f"${early_spend:,.0f} in first half of period vs ${late_spend:,.0f} in second half "
                        f"(reduction of {(1 - late_spend/early_spend):.0%}). "
                        "Pre-sale expense suppression inflates reported earnings. "
                        "Verify whether these costs were deferred, reclassified, or genuinely reduced."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.70,
                })

    # ── Payroll irregularity ──────────────────────────────────────────────────
    payroll_txns = [
        t for t in outflows
        if any(kw in t["merchant"].upper() for kw in PAYROLL_KEYWORDS)
    ]
    if payroll_txns:
        payroll_amounts = [abs(t["amount"]) for t in payroll_txns]
        avg = sum(payroll_amounts) / len(payroll_amounts)
        for t in payroll_txns:
            amt = abs(t["amount"])
            if amt > avg * 1.5 and amt > 5000:
                results.append({
                    "signal_type": "hidden_assets",
                    "severity": "amber",
                    "merchant": t["merchant"].upper(),
                    "amount": t["amount"],
                    "transaction_date": t.get("transaction_date", ""),
                    "description": (
                        f"Payroll spike: ${amt:,.0f} vs average ${avg:,.0f} "
                        f"({amt/avg:.1f}x normal). "
                        "Unusual payroll disbursement may indicate bonus payments, additional headcount, "
                        "or fictitious employees. Verify headcount and pay schedule."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.60,
                })

    # ── Missing depreciation signal (via P&L rows if available) ──────────────
    if pl_rows:
        has_depreciation = any(
            "DEPRECIATION" in str(r.get("account", "")).upper() or
            "DEPRECIATION" in str(r.get("description", "")).upper()
            for r in pl_rows
        )
        total_assets_indicated = any(
            any(kw in str(r.get("account", "")).upper() for kw in ["PROPERTY", "EQUIPMENT", "ASSET", "BUILDING"])
            for r in pl_rows
        )
        if total_assets_indicated and not has_depreciation:
            results.append({
                "signal_type": "hidden_assets",
                "severity": "amber",
                "merchant": "MISSING DEPRECIATION",
                "amount": 0,
                "transaction_date": "",
                "description": (
                    "Fixed assets appear in the P&L but no depreciation expense is recorded. "
                    "Missing depreciation understates expenses and overstates net income. "
                    "Obtain fixed asset register and verify depreciation schedules match "
                    "the asset classes, acquisition dates, and useful life assumptions."
                ),
                "library_match": None,
                "confidence_weight": 0.70,
            })

    return results
