"""Signal 08: Deal Forensics — real estate acquisition, business sale, and M&A anomalies.

Language principle: Every flag surfaces an anomaly for the deal team to verify.
We do not conclude — we identify patterns that warrant investigation.
"""
from collections import defaultdict
import re

# ── Thresholds ────────────────────────────────────────────────────────────────
RAPID_RESALE_MONTHS = 12          # Property sold within N months = flag
VERY_RAPID_RESALE_MONTHS = 6     # Within N months = red
PRICE_SPIKE_PCT = 0.20            # >20% price increase in <12 months = flag
SINGLE_VENDOR_CONCENTRATION = 0.50  # One vendor >50% of outflows = flag
SINGLE_CUSTOMER_CONCENTRATION = 0.50  # One customer >50% of inflows = flag
PERIOD_END_WINDOW_DAYS = 3        # Transactions in last N days of month = timing flag
BELOW_THRESHOLD_BRACKET = 500     # Transactions within $500 of round $10K = structuring flag

# ── Keyword banks ─────────────────────────────────────────────────────────────
OFFSHORE_INDICATORS = [
    "CAYMAN", "BVI", "BRITISH VIRGIN", "BERMUDA", "PANAMA", "SEYCHELLES",
    "ISLE OF MAN", "JERSEY", "GUERNSEY", "VANUATU", "SAMOA", "MAURITIUS",
    "NEVIS", "BELIZE", "BAHAMAS", "LUXEMBOURG", "LIECHTENSTEIN",
]

PRIVACY_COIN_KEYWORDS = [
    "MONERO", "XMR", "ZCASH", "ZEC", "DASH", "GRIN", "BEAM",
    "PIRATE CHAIN", "ARRR", "HAVEN",
]

MIXING_KEYWORDS = [
    "TORNADO", "TORNADO CASH", "COINJOIN", "WASABI", "SAMURAI WALLET",
    "CHIPMIXER", "BLENDER", "SINBAD", "MIXER",
]

DEX_KEYWORDS = [
    "UNISWAP", "PANCAKESWAP", "SUSHISWAP", "CURVE", "BALANCER",
    "1INCH", "PARASWAP", "DYDX", "GMX", "RAYDIUM", "ORCA",
]

BRIDGE_KEYWORDS = [
    "WORMHOLE", "STARGATE", "SYNAPSE", "ACROSS", "HOP PROTOCOL",
    "MULTICHAIN", "ANYSWAP", "CBRIDGE", "POLYGON BRIDGE", "ARBITRUM BRIDGE",
]

SHELL_SUFFIXES = [
    "HOLDINGS LLC", "HOLDINGS INC", "HOLDINGS LTD",
    "VENTURES LLC", "VENTURES INC",
    "CAPITAL LLC", "CAPITAL INC",
    "INVESTMENTS LLC", "INVESTMENTS LTD",
    "PROPERTIES LLC", "REALTY LLC",
    "MANAGEMENT LLC", "SOLUTIONS LLC",
    "GROUP LLC", "GROUP INC",
    "ADVISORS LLC", "ADVISORY LLC",
    "CONSULTING LLC",
]


def run(transactions: list[dict], disclosed_entities: list[str] | None = None, loader=None) -> list[dict]:
    results = []
    disclosed_upper = {e.upper() for e in (disclosed_entities or [])}

    # Build merchant aggregates
    outflows: dict[str, float] = defaultdict(float)
    inflows: dict[str, float] = defaultdict(float)
    outflow_txns: dict[str, list] = defaultdict(list)
    inflow_txns: dict[str, list] = defaultdict(list)
    total_outflow = 0.0
    total_inflow = 0.0

    period_end_debits: list[dict] = []
    just_under_10k: list[dict] = []
    just_over_10k: list[dict] = []

    for t in transactions:
        merchant = t["merchant"].upper()
        amt = t["amount"]
        abs_amt = abs(amt)
        date_str = str(t.get("transaction_date", ""))

        if amt < 0:
            outflows[merchant] += abs_amt
            outflow_txns[merchant].append(t)
            total_outflow += abs_amt

            # Period-end timing check (last 3 days of month)
            try:
                day = int(date_str[8:10]) if len(date_str) >= 10 else 0
                if day >= 28 and abs_amt > 5000:
                    period_end_debits.append(t)
            except (ValueError, TypeError):
                pass

            # Structuring bracket: $9,500–$9,999 (just under $10K)
            if 9500 <= abs_amt < 10000:
                just_under_10k.append(t)
            # Just over: $10,001–$10,999 (showing awareness of threshold)
            elif 10001 <= abs_amt < 11000:
                just_over_10k.append(t)

        else:
            inflows[merchant] += abs_amt
            inflow_txns[merchant].append(t)
            total_inflow += abs_amt

        # ── Privacy coin detection ────────────────────────────────────────────
        if any(kw in merchant for kw in PRIVACY_COIN_KEYWORDS):
            results.append({
                "signal_type": "deal_forensics",
                "severity": "red",
                "merchant": merchant,
                "amount": amt,
                "transaction_date": date_str,
                "description": (
                    f"Privacy coin transaction: '{merchant}' — ${abs_amt:,.0f}. "
                    "Privacy coins (Monero, Zcash, Dash) are designed to obscure transaction trails. "
                    "Verify source of funds and obtain exchange KYC documentation."
                ),
                "library_match": None,
                "confidence_weight": 0.85,
            })

        # ── Crypto mixing / tumbling detection ───────────────────────────────
        if any(kw in merchant for kw in MIXING_KEYWORDS):
            results.append({
                "signal_type": "deal_forensics",
                "severity": "red",
                "merchant": merchant,
                "amount": amt,
                "transaction_date": date_str,
                "description": (
                    f"Crypto mixing service detected: '{merchant}' — ${abs_amt:,.0f}. "
                    "Mixing services are used to obscure the origin and destination of digital assets. "
                    "This pattern warrants enhanced due diligence on source of funds."
                ),
                "library_match": None,
                "confidence_weight": 0.90,
            })

        # ── DEX / bridge activity (chain hopping indicator) ──────────────────
        if any(kw in merchant for kw in DEX_KEYWORDS) or any(kw in merchant for kw in BRIDGE_KEYWORDS):
            results.append({
                "signal_type": "deal_forensics",
                "severity": "amber",
                "merchant": merchant,
                "amount": amt,
                "transaction_date": date_str,
                "description": (
                    f"Decentralised exchange or cross-chain bridge activity: '{merchant}' — ${abs_amt:,.0f}. "
                    "DEX and bridge usage can be used for chain-hopping to obscure asset origin. "
                    "Verify business rationale and trace funds to originating source."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

        # ── Offshore entity indicators ────────────────────────────────────────
        if any(kw in merchant for kw in OFFSHORE_INDICATORS) and abs_amt > 10000:
            is_disclosed = merchant in disclosed_upper
            results.append({
                "signal_type": "deal_forensics",
                "severity": "red" if not is_disclosed else "amber",
                "merchant": merchant,
                "amount": amt,
                "transaction_date": date_str,
                "description": (
                    f"{'Undisclosed offshore' if not is_disclosed else 'Offshore'} entity transaction: "
                    f"'{merchant}' — ${abs_amt:,.0f}. "
                    "Obtain beneficial ownership documentation and verify entity's commercial purpose."
                ),
                "library_match": None,
                "confidence_weight": 0.80 if not is_disclosed else 0.55,
            })

    # ── Vendor concentration ──────────────────────────────────────────────────
    if total_outflow > 0:
        for merchant, total in outflows.items():
            pct = total / total_outflow
            if pct >= SINGLE_VENDOR_CONCENTRATION and total > 10000:
                is_disclosed = merchant in disclosed_upper
                results.append({
                    "signal_type": "deal_forensics",
                    "severity": "amber",
                    "merchant": merchant,
                    "amount": -total,
                    "transaction_date": outflow_txns[merchant][0].get("transaction_date", ""),
                    "description": (
                        f"Vendor concentration: '{merchant}' represents {pct:.0%} of total outflows "
                        f"(${total:,.0f} of ${total_outflow:,.0f}). "
                        "Single-vendor dependency warrants verification of arm's-length terms "
                        "and review of the underlying contract."
                        + (" Entity not in disclosed structure." if not is_disclosed else "")
                    ),
                    "library_match": None,
                    "confidence_weight": 0.65,
                })

    # ── Customer concentration ────────────────────────────────────────────────
    if total_inflow > 0:
        for merchant, total in inflows.items():
            pct = total / total_inflow
            if pct >= SINGLE_CUSTOMER_CONCENTRATION and total > 10000:
                results.append({
                    "signal_type": "deal_forensics",
                    "severity": "amber",
                    "merchant": merchant,
                    "amount": total,
                    "transaction_date": inflow_txns[merchant][0].get("transaction_date", ""),
                    "description": (
                        f"Revenue concentration: '{merchant}' represents {pct:.0%} of total inflows "
                        f"(${total:,.0f} of ${total_inflow:,.0f}). "
                        "High customer concentration is a material deal risk — "
                        "verify contract terms, renewal status, and dependency risk."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.70,
                })

    # ── Period-end concentration ──────────────────────────────────────────────
    if len(period_end_debits) >= 3:
        total_period_end = sum(abs(t["amount"]) for t in period_end_debits)
        results.append({
            "signal_type": "deal_forensics",
            "severity": "amber",
            "merchant": "PERIOD-END PATTERN",
            "amount": -total_period_end,
            "transaction_date": period_end_debits[0].get("transaction_date", ""),
            "description": (
                f"Period-end payment concentration: {len(period_end_debits)} transactions "
                f"totalling ${total_period_end:,.0f} clustered in the last 3 days of the month. "
                "Period-end timing can indicate managed reporting — verify against underlying invoices "
                "and confirm transactions are not backdated."
            ),
            "library_match": None,
            "confidence_weight": 0.65,
        })

    # ── Structuring bracket — just under $10K ────────────────────────────────
    if len(just_under_10k) >= 3:
        total = sum(abs(t["amount"]) for t in just_under_10k)
        results.append({
            "signal_type": "deal_forensics",
            "severity": "red",
            "merchant": "STRUCTURING PATTERN",
            "amount": -total,
            "transaction_date": just_under_10k[0].get("transaction_date", ""),
            "description": (
                f"Structuring indicator: {len(just_under_10k)} transactions between $9,500–$9,999 "
                f"(total ${total:,.0f}). "
                "Transactions just below the $10,000 CTR threshold may indicate deliberate structuring. "
                "Verify business rationale for transaction sizing."
            ),
            "library_match": None,
            "confidence_weight": 0.80,
        })

    # ── Shell entity pattern ──────────────────────────────────────────────────
    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        abs_amt = abs(t["amount"])
        if abs_amt < 5000:
            continue
        if any(merchant.endswith(suffix) for suffix in SHELL_SUFFIXES):
            if merchant not in disclosed_upper:
                results.append({
                    "signal_type": "deal_forensics",
                    "severity": "red" if abs_amt >= 50000 else "amber",
                    "merchant": merchant,
                    "amount": t["amount"],
                    "transaction_date": t.get("transaction_date", ""),
                    "description": (
                        f"Payment of ${abs_amt:,.0f} to '{merchant}' — entity name suggests holding structure. "
                        "Entity not in disclosed corporate structure. "
                        "Obtain beneficial ownership documentation and verify commercial purpose."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.75,
                })

    return results
