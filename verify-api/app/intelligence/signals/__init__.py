"""
Verify signal runner and result builder.
Runs 8 signal libraries against bank transactions and maps
output to the 16-slot frontend SIGNALS array.

Interface notes (actual signatures discovered from source):
  - digital_asset.run(transactions, loader=None)       — OK
  - hidden_assets.run(transactions, pl_rows=None, loader=None) — OK
  - behavioural.run(transactions)                      — NO loader param
  - aml_structuring.run(transactions, pl_rows=None, loader=None) — OK
  - cash_flow.run(transactions, pl_rows)               — pl_rows is required positional
  - real_estate.run(transactions, loader=None)         — OK
  - owner_compensation.run(transactions, pl_rows=None, loader=None) — OK
  - liability.run(transactions)                        — NO loader param
"""
import calendar
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
    {"name": "Mortgage Servicer Activity",    "cat": "ASSET",                   "types": ["real_estate", "liability"], "keywords": ["MORTGAGE", "HOME LOAN", "WESTPAC HOME", "CBA HOME", "ANZ MORTGAGE"]},
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

    return results


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
    """Build one intel card for a detected signal slot."""
    tmpl = INTEL_TEMPLATES.get(slot_name, DEFAULT_INTEL)
    merchants = list({s.get("merchant", s.get("description", "")) for s in matching_signals})[:3]
    total = sum(abs(s.get("amount", 0)) for s in matching_signals)
    count = len(matching_signals)
    narrative = (
        f"Lexi identified {count} transaction{'s' if count != 1 else ''} "
        f"totalling ${total:,.0f} associated with {slot_name.lower()}. "
        f"Merchants detected: {', '.join(m for m in merchants if m)}."
    )
    path = tmpl["path_template"].format(
        exchanges=", ".join(m for m in merchants if m) or "identified exchanges",
        lender=merchants[0] if merchants else "identified lender",
        platforms=", ".join(m for m in merchants if m) or "identified platforms",
        operators=", ".join(m for m in merchants if m) or "identified operators",
    )
    return {
        "cat": slot_cat,
        "cat_cls": tmpl["cat_cls"],
        "title": slot_name,
        "narrative": narrative,
        "rec": tmpl["rec"],
        "rec_cls": tmpl["rec_cls"],
        "tier": tmpl["tier"],
        "path": path,
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
        "cash_summary": {
            "total_credits": round(total_credits),
            "total_debits": round(total_debits),
            "net_cash": round(total_credits - total_debits),
        },
    }
