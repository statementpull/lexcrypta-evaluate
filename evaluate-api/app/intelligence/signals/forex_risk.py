"""Signal 29: Foreign Currency & International Trade Risk.

Businesses with significant international revenue or supplier relationships
face currency exposure that can materially affect earnings — yet SME financials
often present everything in USD, masking the underlying FX risk.

Key patterns:
  FX conversion fees: Indicate revenue or costs in foreign currency
  Trade finance: Letters of credit, import financing, trade credit lines
  International supplier payments: Currency mismatch between revenue and costs
  Unfavourable FX trend exposure: Emerging market currency risk

Acquisition risks:
  - EBITDA may be overstated in periods of favourable FX — not sustainable
  - Unhedged currency exposure can swing earnings by 10–30%+
  - International contracts may have change-of-control clauses
  - Importing businesses face tariff and trade policy risk
  - OFAC sanctions compliance required for all international payments

Sources:
- FASB ASC 830 — Foreign Currency Matters
- OFAC SDN List — sanctions compliance
- BIS Triennial Central Bank Survey — FX market structure

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

FX_KEYWORDS = [
    "FOREIGN EXCHANGE", "FX CONVERSION", "CURRENCY CONVERSION", "FOREX",
    "FX FEE", "CURRENCY FEE", "INTERNATIONAL TRANSFER FEE",
    "EXCHANGE RATE", "FX HEDG", "FORWARD CONTRACT",
]

IMPORT_KEYWORDS = [
    "CUSTOMS", "CBP ", "US CUSTOMS", "IMPORT DUTY", "CUSTOMS DUTY",
    "TARIFF", "CUSTOMS BROKER", "FREIGHT FORWARDER",
    "INCOTERMS", "CIF ", "FOB PAYMENT", "LETTER OF CREDIT",
    "L/C PAYMENT", "IMPORT FINANCE", "TRADE FINANCE",
]

INTERNATIONAL_PAYMENT_KEYWORDS = [
    "INTL WIRE", "INTERNATIONAL WIRE", "SWIFT PAYMENT", "SEPA",
    "FOREIGN WIRE", "OVERSEAS PAYMENT", "CROSS-BORDER",
]

CURRENCY_SIGNALS = [
    "GBP", "EUR", "AUD", "CAD", "CNY", "JPY", "INR", "MXN",
    "BRL", "KRW", "SGD", "HKD", "CHF", "SEK", "NOK", "DKK",
    "NZD", "ZAR", "AED", "THB", "PHP", "IDR", "MYR",
]

HIGH_RISK_COUNTRIES = [
    "CHINA", "RUSSIA", "IRAN", "NORTH KOREA", "VENEZUELA",
    "MYANMAR", "CUBA", "SYRIA", "BELARUS",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    fx_txns = []
    import_txns = []
    intl_txns = []
    high_risk_txns = []

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in FX_KEYWORDS):
            fx_txns.append(t)
        if any(kw in merchant for kw in IMPORT_KEYWORDS):
            import_txns.append(t)
        if any(kw in merchant for kw in INTERNATIONAL_PAYMENT_KEYWORDS):
            intl_txns.append(t)
        if any(currency in merchant for currency in CURRENCY_SIGNALS):
            fx_txns.append(t)
        if any(country in merchant for country in HIGH_RISK_COUNTRIES):
            high_risk_txns.append(t)

    if not any([fx_txns, import_txns, intl_txns, high_risk_txns]):
        return []

    # ── FX exposure ───────────────────────────────────────────────────────────
    if fx_txns:
        total_fx = sum(abs(t["amount"]) for t in fx_txns)
        results.append({
            "signal_type": "forex_risk",
            "severity": "amber",
            "merchant": f"FOREIGN CURRENCY EXPOSURE: {len(fx_txns)} FX transactions",
            "amount": total_fx,
            "transaction_date": fx_txns[0].get("transaction_date", ""),
            "description": (
                f"Foreign currency activity: {len(fx_txns)} transactions with FX indicators, "
                f"${total_fx:,.0f} total. "
                "Currency risk implications: "
                "(1) Revenue or costs denominated in foreign currency create P&L volatility "
                "not visible in USD-only financials — restate EBITDA in constant currency, "
                "(2) Unhedged positions: if no FX hedging instruments are in place, "
                "earnings could swing 10–30%+ with currency moves, "
                "(3) Verify whether customer contracts are USD-denominated or foreign currency — "
                "re-pricing risk if contracts roll at unfavourable rates. "
                "Request 3-year FX gain/loss schedule from seller."
            ),
            "library_match": "FOREX_EXPOSURE",
            "confidence_weight": 0.65,
        })

    # ── Import / customs ──────────────────────────────────────────────────────
    if import_txns:
        total_import = sum(abs(t["amount"]) for t in import_txns)
        results.append({
            "signal_type": "forex_risk",
            "severity": "amber",
            "merchant": f"IMPORT / CUSTOMS ACTIVITY: ${total_import:,.0f}",
            "amount": -total_import,
            "transaction_date": import_txns[0].get("transaction_date", ""),
            "description": (
                f"Import, customs, and trade finance activity: {len(import_txns)} transactions "
                f"totalling ${total_import:,.0f}. "
                "Importing businesses face: (1) tariff and trade policy risk — "
                "Section 301 tariffs on Chinese goods can be 25%+ and have not been fully resolved, "
                "(2) supply chain disruption risk if key suppliers are in geopolitically sensitive regions, "
                "(3) customs compliance — CBP violations create fines and import privilege suspension, "
                "(4) letter of credit facility at closing — confirm LOC is transferable or replaceable. "
                "Request full supplier list by country of origin and confirm tariff classification."
            ),
            "library_match": "FOREX_IMPORT_RISK",
            "confidence_weight": 0.65,
        })

    # ── High-risk country payments ────────────────────────────────────────────
    if high_risk_txns:
        total_hr = sum(abs(t["amount"]) for t in high_risk_txns)
        results.append({
            "signal_type": "forex_risk",
            "severity": "red",
            "merchant": f"HIGH-RISK COUNTRY PAYMENTS: {len(high_risk_txns)} transactions",
            "amount": -total_hr,
            "transaction_date": high_risk_txns[0].get("transaction_date", ""),
            "description": (
                f"Payments involving high-risk countries: {len(high_risk_txns)} transactions "
                f"totalling ${total_hr:,.0f}. "
                "OFAC sanctions compliance is a strict liability standard — "
                "there is no good-faith defense for transacting with sanctioned persons or entities. "
                "Successor liability: a buyer who acquires a business with OFAC violations "
                "can inherit civil and criminal penalties. "
                "Mandatory pre-close action: OFAC/sanctions counsel review of all international payments."
            ),
            "library_match": "FOREX_SANCTIONS_RISK",
            "confidence_weight": 0.85,
        })

    return results
