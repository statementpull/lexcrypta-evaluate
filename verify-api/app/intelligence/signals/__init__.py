"""
Verify signal runner and result builder.
Runs 8 signal libraries against bank transactions and maps
output to the 17-slot frontend SIGNALS array.
"""
from collections import defaultdict
from datetime import datetime, timezone

from ..las_score import calculate_las

from . import (
    digital_asset, hidden_assets, behavioural, aml_structuring,
    cash_flow, real_estate, owner_compensation, liability,
)

# Fixed 16-slot mapping — order must match frontend SIGNALS array exactly
SIGNAL_SLOTS = [
    {"name": "Crypto Exchange Activity",      "cat": "ASSET · CONVERSION",     "types": ["digital_asset"],      "keywords": ["COINBASE", "KRAKEN", "BINANCE", "GEMINI", "BITCOIN", "CRYPTO", "ETHEREUM", "BTC", "ETH"]},
    {"name": "Crypto Payment Gateways",       "cat": "CONVERSION",              "types": ["digital_asset"],      "keywords": ["BITPAY", "COINPAYMENTS", "NOWPAYMENTS", "CRYPTOMUS", "BITREFILL"]},
    {"name": "Mortgage Servicer Activity",    "cat": "ASSET",                   "types": ["real_estate", "liability"], "keywords": [
        "MORTGAGE", "HOME LOAN",
        # US servicers
        "LAKEVIEW", "NATIONSTAR", "MR COOPER", "MRCOOPER", "FREEDOM MORTGAGE",
        "ROUNDPOINT", "PHH MORTGAGE", "SHELLPOINT", "CARRINGTON", "PENNYMAC",
        "LOANDEPOT", "LOAN DEPOT", "NEWREZ", "CALIBER HOME", "BSI FINANCIAL",
        "OCWEN", "HOME POINT", "PLANET HOME", "RUSHMORE LOAN", "FLAGSTAR",
        "CHASE MORTGAGE", "CHASE HOME", "WELLS FARGO MORTGAGE", "WELLS HOME",
        "QUICKEN LOAN", "ROCKET MORTGAGE", "BANK OF AMERICA MORTGAGE",
        "US BANK HOME", "USBANK HOME", "SUNTRUST MORTGAGE", "REGIONS MORTGAGE",
        "MICHIGAN FIRST MORTGAGE", "MICHIGAN FIRST ACH",
        # AU/UK (kept for international matters)
        "WESTPAC HOME", "CBA HOME", "ANZ MORTGAGE",
    ]},
    {"name": "Gambling Platforms",            "cat": "CONVERSION · OBFUSCATION","types": ["behavioural"],        "keywords": ["SPORTSBET", "LADBROKES", "BETFAIR", "NEDS", "POINTSBET", "TABCORP", "CASINO", "POKIES", "BET365"]},
    {"name": "Luxury Asset Merchants",        "cat": "ASSET",                   "types": ["hidden_assets"],      "keywords": ["ROLEX", "LOUIS VUITTON", "GUCCI", "CARTIER", "TIFFANY", "PORSCHE", "FERRARI", "MASERATI", "YACHT"]},
    {"name": "Travel Vendors",                "cat": "FLOW · LIFESTYLE",        "types": ["behavioural", "cash_flow"], "keywords": ["QANTAS", "VIRGIN", "JETSTAR", "AIRBNB", "BOOKING.COM", "EXPEDIA", "HOTELS.COM", "CRUISE"]},
    {"name": "Property Tax Authorities",      "cat": "ASSET",                   "types": ["real_estate"],        "keywords": ["COUNCIL RATES", "LAND TAX", "STAMP DUTY", "STATE REVENUE", "WATER RATES"]},
    {"name": "Storage Facilities",            "cat": "ASSET",                   "types": ["hidden_assets"],      "keywords": ["STORAGE KING", "KENNARDS", "NATIONAL STORAGE", "SAFESTORE", "SELF STORAGE"]},
    {"name": "Second Household Indicators",   "cat": "ASSET",                   "types": ["real_estate"],        "keywords": ["SECOND MORTGAGE", "INVESTMENT LOAN", "RENTAL BOND", "STRATA LEVY", "BODY CORP"]},
    {"name": "Structuring / Cash Behaviour",  "cat": "OBFUSCATION",             "types": ["aml_structuring"],    "keywords": []},
    {"name": "Recurring Unnamed Transfers",   "cat": "FLOW · OBFUSCATION",      "types": ["owner_compensation"], "keywords": ["TRANSFER TO", "PAYMENT TO", "FUNDS TRANSFER", "BPAY"]},
    {"name": "DeFi Platforms",                "cat": "ASSET · CONVERSION",      "types": ["digital_asset"],      "keywords": ["UNISWAP", "AAVE", "COMPOUND", "DEFI", "PANCAKESWAP", "CURVE", "YEARN", "SUSHISWAP"]},
    {"name": "Rental Platforms",              "cat": "ASSET · FLOW",            "types": ["real_estate"],        "keywords": ["AIRBNB", "STAYZ", "VRBO", "HOMEAWAY", "REALESTATE.COM.AU RENTAL"]},
    {"name": "Gold & Precious Metals",        "cat": "ASSET",                   "types": ["hidden_assets"],      "keywords": ["ABC BULLION", "PERTH MINT", "GOLD BULLION", "SILVER BULLION", "PRECIOUS METALS"]},
    {"name": "Gift Card / Prepaid",           "cat": "OBFUSCATION",             "types": ["aml_structuring"],    "keywords": ["GIFT CARD", "PREPAID VISA", "PREPAID MASTERCARD", "VANILLA VISA", "EFTPOS GIFT"]},
    {"name": "Cross-Border Value Transfer",   "cat": "FLOW",                    "types": ["cash_flow"],          "keywords": ["WESTERN UNION", "MONEYGRAM", "WISE", "TRANSFERWISE", "REMITLY", "WORLDREMIT", "OFX"]},
    {"name": "Political Entity Payments",     "cat": "DISTRIBUTION · ASSET",    "types": ["hidden_assets"],      "keywords": [
        # Known PACs and political committees (US)
        "ACTBLUE", "ACT BLUE", "WINRED", "WIN RED",
        "PROTECT OUR FUTURE", "HOUSE MAJORITY PAC", "SENATE MAJORITY PAC",
        "ONE NATION", "AMERICAN ACTION NETWORK", "SENATE LEADERSHIP FUND",
        "TEAM MCCONNELL", "EMILYS LIST", "EMILY S LIST",
        "NRSC", "NRCC", "DCCC", "DSCC",
        # Generic political identifiers
        "FOR CONGRESS", "FOR SENATE", "FOR GOVERNOR", "FOR PRESIDENT",
        "POLITICAL ACTION COMMITTEE", "LEADERSHIP PAC", "CAMPAIGN COMMITTEE",
        "ELECTION FUND", "CAMPAIGN FUND", "POLITICAL FUND",
        # Federal identifiers
        "FEDERAL ELECTION", "FEC FILING",
    ]},
    {"name": "PDF Document Integrity",        "cat": "DOCUMENT · INTEGRITY",    "types": ["document_integrity"], "keywords": []},
]

INTEL_TEMPLATES = {
    "Crypto Exchange Activity": {
        "cat_cls": "asset",
        "rec": "High Recovery",
        "rec_cls": "high",
        "tier": "Tier 1 — Global Leaders",
        "path_template": "File preservation letter to {exchanges} immediately. US-regulated exchanges (Coinbase, Kraken) respond to 18 U.S.C. § 2703 subpoena — expect KYC records, transaction history, wallet addresses within 30–60 days.",
    },
    "Mortgage Servicer Activity": {
        "cat_cls": "asset",
        "rec": "High Recovery",
        "rec_cls": "high",
        "tier": "Domestic Asset",
        "path_template": "Immediate land title search. Cross-reference state revenue office for stamp duty. Subpoena {lender} for loan details, security address, and account holder.",
    },
    "Gambling Platforms": {
        "cat_cls": "obfuscation",
        "rec": "Medium Recovery",
        "rec_cls": "medium",
        "tier": "Licensed Operators",
        "path_template": "Formal request to {platforms} for full account history, KYC records, current balance, and withdrawal destination accounts.",
    },
    "Structuring / Cash Behaviour": {
        "cat_cls": "obfuscation",
        "rec": "Low Recovery",
        "rec_cls": "low",
        "tier": "Behavioural Signal",
        "path_template": "Document for trustee report. Subpoena ATM records to establish withdrawal locations — may identify secondary addresses or associates.",
    },
    "Luxury Asset Merchants": {
        "cat_cls": "asset",
        "rec": "Medium Recovery",
        "rec_cls": "medium",
        "tier": "Luxury Retail",
        "path_template": "Obtain itemised receipts. Cross-reference asset register and bankruptcy petition. Issue preservation notice to retailers — many retain purchase records for warranty/insurance.",
    },
    "Cross-Border Value Transfer": {
        "cat_cls": "flow",
        "rec": "Medium Recovery",
        "rec_cls": "medium",
        "tier": "Remittance Operators",
        "path_template": "Subpoena {operators} for recipient details, destination country, and beneficiary identity. Cross-border transfers may indicate undisclosed foreign assets.",
    },
    "Political Entity Payments": {
        "cat_cls": "obfuscation",
        "rec": "High Recovery",
        "rec_cls": "high",
        "tier": "Wealth Extraction — Political",
        "path_template": "Political donations are a documented mechanism for extracting funds from a business or estate. Identify the receiving PAC or committee and cross-reference with FEC filings at fec.gov to confirm amounts, donor identity, and source entity. In bankruptcy and divorce proceedings, political contributions made within the preference period may be recoverable. Subpoena the donor's brokerage and payroll records to establish whether the source was business operating funds or personal wealth.",
    },
    "PDF Document Integrity": {
        "cat_cls": "obfuscation",
        "rec": "Immediate Review",
        "rec_cls": "high",
        "tier": "Document Authenticity",
        "path_template": "Statement metadata indicates post-issuance modification. Obtain certified copy directly from issuing bank. Do not rely on provided document without independent verification.",
    },
}

DEFAULT_INTEL = {
    "cat_cls": "flow",
    "rec": "Investigate",
    "rec_cls": "medium",
    "tier": "Further Investigation Required",
    "path_template": "Review transactions manually and obtain supporting documentation.",
}


def _build_top_counterparties(transactions: list, n: int = 10) -> list:
    """Return top N counterparties by total absolute volume."""
    totals: dict = defaultdict(lambda: {"sent": 0.0, "received": 0.0, "count": 0})
    for t in transactions:
        merchant = (t.get("merchant") or t.get("description", "")).strip()
        if not merchant or merchant in ("UNKNOWN", "N/A", ""):
            continue
        amt = t.get("amount", 0)
        totals[merchant]["count"] += 1
        if amt < 0:
            totals[merchant]["sent"] += abs(amt)
        else:
            totals[merchant]["received"] += amt
    result = []
    for merchant, d in totals.items():
        total = d["sent"] + d["received"]
        result.append({
            "merchant": merchant,
            "total": round(total),
            "sent": round(d["sent"]),
            "received": round(d["received"]),
            "count": d["count"],
        })
    return sorted(result, key=lambda x: x["total"], reverse=True)[:n]


def _build_monthly_breakdown(transactions: list) -> list:
    """Return month-by-month credit/debit totals from M/D dates."""
    monthly: dict = defaultdict(lambda: {"credits": 0.0, "debits": 0.0})
    for t in transactions:
        raw = (t.get("transaction_date") or "").strip()
        parts = raw.split("/")
        if not parts:
            continue
        try:
            month = int(parts[0])
        except ValueError:
            continue
        if not 1 <= month <= 12:
            continue
        amt = t.get("amount", 0)
        if amt > 0:
            monthly[month]["credits"] += amt
        elif amt < 0:
            monthly[month]["debits"] += abs(amt)
    if not monthly:
        return []
    return [
        {
            "month": _MONTH_ABBR[m],
            "credits": round(monthly[m]["credits"]),
            "debits": round(monthly[m]["debits"]),
            "net": round(monthly[m]["credits"] - monthly[m]["debits"]),
        }
        for m in sorted(monthly)
    ]


def _detect_pass_through(transactions: list) -> list:
    """Flag rapid-cycling: credit ≥$2000 followed by similar debit within 2 days.

    FinCEN layering typology — account used as conduit rather than genuine holder.
    Threshold is tight ($2000 min, 80% match, 2-day window) to reduce false positives.
    """
    # Parse dates into (month, day) tuples
    parsed = []
    for t in transactions:
        raw = (t.get("transaction_date") or "").strip()
        parts = raw.split("/")
        if len(parts) >= 2:
            try:
                parsed.append({**t, "_m": int(parts[0]), "_d": int(parts[1])})
            except ValueError:
                pass

    flagged, seen = [], set()
    credits = [t for t in parsed if t.get("amount", 0) >= 2000]
    debits  = [t for t in parsed if t.get("amount", 0) <= -2000]

    for c in credits:
        c_amt = c["amount"]
        for d in debits:
            if c["_m"] != d["_m"]:
                continue
            days_apart = d["_d"] - c["_d"]
            if not 0 <= days_apart <= 2:
                continue
            ratio = abs(d["amount"]) / c_amt
            if not 0.80 <= ratio <= 1.20:
                continue
            key = (c.get("transaction_date"), c.get("merchant"), d.get("transaction_date"))
            if key in seen:
                continue
            seen.add(key)
            c_name = (c.get("merchant") or "")[:35]
            d_name = (d.get("merchant") or "")[:35]
            flagged.append({
                "signal_type": "aml_structuring",
                "merchant": f"PASS-THROUGH: {c_name} → {d_name}",
                "amount": c_amt,
                "transaction_date": c.get("transaction_date", ""),
                "severity": "amber",
                "confidence_weight": 0.6,
            })
        if len(flagged) >= 5:
            break
    return flagged


def _keyword_scan(transactions: list) -> list:
    """Keyword-first detection: create signals for transactions matching slot keywords.

    The signal modules catch broad behavioural patterns; this catches specific known
    entities (US mortgage servicers, crypto exchanges, remittance operators, etc.)
    that the modules may miss. Deduplication in run_signals prevents double-counting.
    """
    results = []
    for t in transactions:
        merchant_upper = (t.get("merchant") or t.get("description", "")).upper()
        if not merchant_upper:
            continue
        for slot in SIGNAL_SLOTS:
            if not slot["keywords"]:
                continue
            if any(kw in merchant_upper for kw in slot["keywords"]):
                results.append({
                    "signal_type": slot["types"][0],
                    "merchant": t.get("merchant") or t.get("description", ""),
                    "amount": t.get("amount", 0),
                    "transaction_date": t.get("transaction_date", ""),
                    "severity": "amber",
                    "confidence_weight": 0.5,
                    "source": "keyword_scan",
                })
                break  # one signal per transaction
    return results


def _dedup_signals(signals: list) -> list:
    """Remove duplicate signals by (type, date, amount) — prevents keyword_scan
    and module signals double-counting the same transaction."""
    seen, out = set(), []
    for s in signals:
        key = (
            s.get("signal_type", s.get("type", "")),
            s.get("transaction_date", ""),
            round(s.get("amount", 0), 2),
        )
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def run_signals(transactions: list, loader=None) -> list:
    """Run all 8 signal libraries and return combined raw signal list.

    Each module has a slightly different signature — handled per-module:
      - behavioural and liability: run(transactions) — no loader param
      - cash_flow: run(transactions, pl_rows) — pl_rows required positional, pass []
      - all others: run(transactions, loader=None) or run(transactions, pl_rows=None, loader=None)
    """
    results = []

    # Modules that accept loader=None
    for module in [digital_asset, real_estate]:
        try:
            results.extend(module.run(transactions, loader=loader))
        except Exception:
            pass

    # Modules that accept pl_rows and loader as keyword args
    for module in [hidden_assets, aml_structuring, owner_compensation]:
        try:
            results.extend(module.run(transactions, pl_rows=None, loader=loader))
        except Exception:
            pass

    # cash_flow requires pl_rows as positional — pass empty list
    try:
        results.extend(cash_flow.run(transactions, []))
    except Exception:
        pass

    # behavioural and liability accept only transactions
    for module in [behavioural, liability]:
        try:
            results.extend(module.run(transactions))
        except Exception:
            pass

    # Pass-through/layering detection (built-in, no external module)
    try:
        results.extend(_detect_pass_through(transactions))
    except Exception:
        pass

    # Keyword-first scan — catches known entities the modules miss
    try:
        results.extend(_keyword_scan(transactions))
    except Exception:
        pass

    return _dedup_signals(results)


def _slot_status(slot: dict, raw_signals: list) -> tuple:
    """Return (status, count, total_amount) for a signal slot."""
    matching = []
    for sig in raw_signals:
        sig_type = sig.get("signal_type", sig.get("type", ""))
        if sig_type not in slot["types"]:
            continue
        merchant_upper = sig.get("merchant", sig.get("description", "")).upper()
        if slot["keywords"]:
            if any(kw in merchant_upper for kw in slot["keywords"]):
                matching.append(sig)
        else:
            matching.append(sig)

    if not matching:
        return "none", 0, 0.0
    total = sum(abs(s.get("amount", 0)) for s in matching)
    severity = max((s.get("severity", "amber") for s in matching),
                   key=lambda x: {"red": 2, "amber": 1, "green": 0}.get(x, 0))
    status = "detected" if severity in ("red", "amber") else "possible"
    return status, len(matching), total


def _build_intel_card(slot_name: str, slot_cat: str, matching_signals: list) -> dict:
    """Build one intel card with transaction-level narrative."""
    tmpl = INTEL_TEMPLATES.get(slot_name, DEFAULT_INTEL)

    # Sort by absolute amount — largest transaction leads the narrative
    sorted_sigs = sorted(matching_signals, key=lambda s: abs(s.get("amount", 0)), reverse=True)
    top = sorted_sigs[0] if sorted_sigs else {}

    # Deduplicated merchant list, largest-amount first
    seen, merchants = set(), []
    for s in sorted_sigs:
        m = (s.get("merchant") or s.get("description", "")).strip()
        if m and m not in seen:
            seen.add(m)
            merchants.append(m)
        if len(merchants) == 3:
            break

    total = sum(abs(s.get("amount", 0)) for s in matching_signals)
    count = len(matching_signals)
    top_merchant = (top.get("merchant") or top.get("description", "")).strip()
    top_amount   = abs(top.get("amount", 0))
    top_date     = top.get("transaction_date", "")

    # Opening — lead with the single most significant transaction
    if top_date and top_merchant and top_amount > 0:
        narrative = (
            f"On {top_date}, ${top_amount:,.2f} was directed to {top_merchant} — "
            f"the largest single transaction detected in this category. "
        )
    else:
        narrative = ""

    # Volume summary
    if count == 1:
        narrative += f"One transaction of ${total:,.0f} was identified."
    else:
        narrative += (
            f"Across {count} transactions totalling ${total:,.0f}, "
            f"activity consistent with {slot_name.lower()} was identified in the uploaded statements."
        )

    # Entity list (skip if already named as top_merchant and only one entity)
    other_merchants = [m for m in merchants if m != top_merchant]
    if other_merchants:
        narrative += f" Additional entities detected: {', '.join(other_merchants)}."

    # Pattern note
    if count >= 8:
        narrative += (
            " The volume and frequency of these transactions indicates a sustained pattern, "
            "not isolated activity — consistent with deliberate asset movement."
        )
    elif count >= 3:
        narrative += " Multiple occurrences establish a pattern warranting formal investigation and record preservation."

    path = tmpl["path_template"].format(
        exchanges =", ".join(merchants) or "identified exchanges",
        lender    =merchants[0] if merchants else "identified lender",
        platforms =", ".join(merchants) or "identified platforms",
        operators =", ".join(merchants) or "identified operators",
    )
    return {
        "cat":     slot_cat,
        "cat_cls": tmpl["cat_cls"],
        "title":   slot_name,
        "narrative": narrative,
        "rec":     tmpl["rec"],
        "rec_cls": tmpl["rec_cls"],
        "tier":    tmpl["tier"],
        "path":    path,
        "top_transaction": {"date": top_date, "merchant": top_merchant, "amount": top_amount}
                           if top_merchant else None,
    }


def _exposure_from_score(score: float) -> tuple:
    """Return (exposure, att_colour, att_flag)."""
    if score >= 60:
        return "HIGH", "red", "High Exposure"
    if score >= 30:
        return "MEDIUM", "amber", "Review Required"
    return "LOW", "green", ""


_MONTH_ABBR = [None, "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def _compute_date_range(transactions: list) -> dict | None:
    """Return {from_label, to_label} from M/D transaction dates, or None."""
    months = []
    current_year = datetime.now(timezone.utc).year
    for t in transactions:
        raw = (t.get("transaction_date") or "").strip()
        if not raw:
            continue
        parts = raw.split("/")
        if len(parts) >= 1:
            try:
                months.append(int(parts[0]))
            except ValueError:
                pass
    if not months:
        return None
    lo, hi = min(months), max(months)
    # If range wraps year (e.g. Nov → Feb), infer prior year for start
    if lo > hi:
        from_year, to_year = current_year - 1, current_year
    else:
        from_year = to_year = current_year
    return {
        "from_label": f"{_MONTH_ABBR[lo]} {from_year}",
        "to_label": f"{_MONTH_ABBR[hi]} {to_year}",
    }


def build_verify_result(
    matter_id: int,
    raw_signals: list,
    transactions: list,
    exposure: str = "PENDING",
) -> dict:
    """Map raw engine output to frontend result object."""
    date_range = _compute_date_range(transactions)
    total_credits = sum(
        t.get("credit", 0) or max(t.get("amount", 0), 0)
        for t in transactions
    )
    total_debits = sum(
        t.get("debit", 0) or abs(min(t.get("amount", 0), 0))
        for t in transactions
    )
    total_vol = sum(abs(t.get("amount", 0)) for t in transactions)
    try:
        las_score = calculate_las(raw_signals, total_vol)
    except Exception:
        las_score = 0.0
    computed_exposure, att, att_flag = _exposure_from_score(las_score)

    if exposure == "PENDING":
        exposure = computed_exposure

    if las_score >= 80:
        verdict, verdict_cls = "DO FIRST", "high"
    elif las_score >= 40:
        verdict, verdict_cls = "REVIEW NEXT", "mid"
    else:
        verdict, verdict_cls = "LOW URGENCY", "low"

    severity_val = min(sum(
        {"red": 8.0, "amber": 4.0, "green": 1.0}.get(s.get("severity", ""), 0)
        * s.get("confidence_weight", 0.5) for s in raw_signals
    ), 40.0)
    cash_count = sum(1 for s in raw_signals if s.get("signal_type", s.get("type", "")) == "cash_flow")
    timing_val = min(cash_count * 5.0, 25.0)
    flagged_total = sum(abs(s.get("amount", 0)) for s in raw_signals)
    fin_val = min((flagged_total / total_vol * 40) if total_vol > 0 else 0, 20.0)
    crypto_count = sum(1 for s in raw_signals if "digital" in s.get("signal_type", s.get("type", "")).lower())
    rec_val = min(crypto_count * 5.0, 15.0)

    parts = []
    if crypto_count:
        parts.append(f"Crypto activity detected ({crypto_count} transactions)")
    if cash_count:
        parts.append("Cash flow anomalies present")
    if fin_val > 10:
        parts.append(f"Financial gap: ${flagged_total:,.0f}")
    reason = " · ".join(parts) if parts else "Analysis complete — review matter for full detail."

    signals_out = []
    intel_out = []
    for slot in SIGNAL_SLOTS:
        status, count, total = _slot_status(slot, raw_signals)
        signals_out.append({
            "name": slot["name"],
            "cat": slot["cat"],
            "status": status,
            "count": count if status != "none" else 0,
            "amount": f"${total:,.0f}" if total > 0 else None,
        })
        if status == "detected":
            matching = [
                s for s in raw_signals
                if s.get("signal_type", s.get("type", "")) in slot["types"]
                and (not slot["keywords"] or any(
                    kw in s.get("merchant", s.get("description", "")).upper()
                    for kw in slot["keywords"]
                ))
            ]
            intel_out.append(_build_intel_card(slot["name"], slot["cat"], matching))

    verify_plus = {"available": False}
    if exposure == "HIGH":
        verify_plus = {
            "available": True,
            "verdict": "YES — PURSUE",
            "recovery_estimate": f"${flagged_total:,.0f} identified",
            "priority_action": "File preservation letter · Obtain subpoenas for identified institutions",
        }

    top_counterparties = _build_top_counterparties(transactions)
    monthly_breakdown  = _build_monthly_breakdown(transactions)

    return {
        "matter_id": matter_id,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "exposure": exposure,
        "att": att,
        "att_flag": att_flag,
        "las": {
            "score": round(las_score),
            "verdict": verdict,
            "verdict_cls": verdict_cls,
            "reason": reason,
            "components": [
                {"label": "Signal Severity",    "val": round(severity_val), "max": 40},
                {"label": "Timing / Urgency",   "val": round(timing_val),   "max": 25},
                {"label": "Financial Gap",      "val": round(fin_val),      "max": 20},
                {"label": "Recovery Potential", "val": round(rec_val),      "max": 15},
            ],
        },
        "signals": signals_out,
        "intel": intel_out,
        "verify_plus_teaser": verify_plus,
        "date_range": date_range,
        "flagged_transactions": [
            {
                "date": s.get("transaction_date", ""),
                "merchant": (s.get("merchant") or s.get("description", ""))[:60],
                "amount": s.get("amount", 0),
                "signal_type": s.get("signal_type", s.get("type", "")),
            }
            for s in sorted(
                [s for s in raw_signals if abs(s.get("amount", 0)) > 0],
                key=lambda s: abs(s.get("amount", 0)),
                reverse=True,
            )[:20]
        ],
        "cash_summary": {
            "total_credits": round(total_credits),
            "total_debits": round(total_debits),
            "net_cash": round(total_credits - total_debits),
        },
        "top_counterparties": top_counterparties,
        "monthly_breakdown": monthly_breakdown,
    }
