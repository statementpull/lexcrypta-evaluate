"""
Three-Document Cross-Reference Engine
======================================
Triangulates bank statement data against tax return declarations and
bankruptcy petition disclosures to identify material discrepancies.

This is the core of Verify+ three-document mode.  Each discrepancy becomes
a CrossRefSignal — a structured finding with severity, dollar gap, and
recommended action.

Signal types
------------
  INCOME_GAP          Bank deposits far exceed declared income (tax + petition)
  UNDISCLOSED_CRYPTO  Bank shows exchange transactions; petition/1040 deny crypto
  UNDISCLOSED_PROPERTY Bank shows mortgage payments; petition has no real estate
  ASSET_UNDERSTATEMENT Total bank activity implies assets well above petition total
  INCOME_MISMATCH     Bank deposits vs Schedule I monthly income (annualised)
  SOFA_OMISSION       SOFA income history is blank despite tax return showing income
  INSIDER_CONCEALMENT SOFA has no insider payments despite bank showing P2P transfers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Data structure ────────────────────────────────────────────────────────────

@dataclass
class CrossRefSignal:
    signal_id: str           # e.g. "INCOME_GAP"
    severity: str            # "red" | "amber" | "green"
    label: str               # short human label
    description: str         # detailed finding
    gap_usd: float           # dollar gap (0 if not monetary)
    sources: list[str]       # which documents contributed ("bank", "tax", "petition")
    recommended_action: str  # trustee next step
    confidence: float        # 0.0–1.0


# ── Thresholds ────────────────────────────────────────────────────────────────

_INCOME_GAP_RED_RATIO   = 2.0   # deposits ≥ 2× declared income → red
_INCOME_GAP_AMBER_RATIO = 1.3   # deposits ≥ 1.3× declared income → amber
_MIN_GAP_AMOUNT         = 5_000 # ignore tiny gaps below this floor
_CRYPTO_MIN_AMOUNT      = 500   # minimum bank crypto activity to trigger flag
_MORTGAGE_MIN_AMOUNT    = 3_000 # minimum cumulative mortgage-like payments


# ── Main engine ───────────────────────────────────────────────────────────────

def run_crossref(
    bank_summary: dict,        # from main.py: {total_credits, total_debits, signals}
    bank_signals: list[dict],  # raw signals from run_signals()
    tax: Optional[dict],       # output of parse_tax_return(), or None
    petition: Optional[dict],  # output of parse_bankruptcy_petition(), or None
) -> list[CrossRefSignal]:
    """
    Run all cross-reference checks and return a list of CrossRefSignal findings.
    Only checks that can be run with available data will fire.
    """
    signals: list[CrossRefSignal] = []

    total_credits = bank_summary.get("total_credits", 0) or 0
    total_debits  = bank_summary.get("total_debits", 0) or 0

    # ── 1. INCOME GAP ─────────────────────────────────────────────────────────
    # Compare bank deposits against the best available income declaration.
    declared_income = _best_declared_income(tax, petition)
    if declared_income is not None and total_credits > _MIN_GAP_AMOUNT:
        gap = total_credits - declared_income
        if declared_income > 0:
            ratio = total_credits / declared_income
        else:
            ratio = 99.0  # zero declared income with any deposits = red flag

        if ratio >= _INCOME_GAP_RED_RATIO and gap >= _MIN_GAP_AMOUNT:
            severity = "red"
            conf = 0.85
        elif ratio >= _INCOME_GAP_AMBER_RATIO and gap >= _MIN_GAP_AMOUNT:
            severity = "amber"
            conf = 0.70
        else:
            severity = None

        if severity:
            sources = ["bank"]
            if tax and tax.get("agi") is not None:
                sources.append("tax")
            if petition and petition.get("monthly_income") is not None:
                sources.append("petition")

            signals.append(CrossRefSignal(
                signal_id="INCOME_GAP",
                severity=severity,
                label="Income Gap — Deposits Exceed Declared Income",
                description=(
                    f"Bank deposits total ${total_credits:,.0f} against declared income of "
                    f"${declared_income:,.0f} — a gap of ${gap:,.0f} ({ratio:.1f}× ratio). "
                    "Deposits significantly exceed what any filed document accounts for. "
                    "Possible sources: unreported income, undisclosed business activity, "
                    "or transfers from concealed accounts."
                ),
                gap_usd=round(gap, 2),
                sources=sources,
                recommended_action=(
                    "Obtain complete bank records for all accounts. "
                    "Cross-reference payroll stubs and Schedule C against deposit pattern. "
                    "Issue Schedule K-1 request if business interest suspected."
                ),
                confidence=conf,
            ))

    # ── 2. UNDISCLOSED CRYPTO ─────────────────────────────────────────────────
    # Bank shows exchange transactions → check if petition and/or 1040 disclose it
    crypto_in_bank = _bank_crypto_amount(bank_signals)
    if crypto_in_bank >= _CRYPTO_MIN_AMOUNT:
        declared_in_tax = (tax or {}).get("digital_assets_declared")    # True / False / None
        declared_in_petition = (petition or {}).get("crypto_declared")   # True / False / None
        concealed = False
        gap_note = ""

        if declared_in_tax is False and declared_in_petition is False:
            concealed = True
            severity = "red"
            gap_note = "Both 1040 and petition deny digital asset holdings."
        elif declared_in_tax is False:
            concealed = True
            severity = "red"
            gap_note = "1040 digital assets checkbox answered No."
        elif declared_in_petition is False:
            concealed = True
            severity = "amber"
            gap_note = "Crypto absent from Schedule A/B."
        elif declared_in_tax is None and declared_in_petition is None:
            # Neither document available — still an amber flag
            concealed = True
            severity = "amber"
            gap_note = "No tax return or petition available to verify disclosure."

        if concealed:
            signals.append(CrossRefSignal(
                signal_id="UNDISCLOSED_CRYPTO",
                severity=severity,
                label="Undisclosed Cryptocurrency Position",
                description=(
                    f"${crypto_in_bank:,.0f} in crypto exchange transactions detected in bank records. "
                    f"{gap_note} "
                    "Digital assets must be disclosed as property of the estate at filing date value."
                ),
                gap_usd=round(crypto_in_bank, 2),
                sources=_available_sources(tax, petition) + ["bank"],
                recommended_action=(
                    "Subpoena exchange(s) for account balance as of filing date, "
                    "full transaction history, and all linked wallet addresses. "
                    "If exchange confirms zero balance, require debtor to disclose "
                    "destination wallets and current custody status."
                ),
                confidence=0.85 if severity == "red" else 0.65,
            ))

    # ── 3. UNDISCLOSED REAL ESTATE ────────────────────────────────────────────
    # Bank shows mortgage servicer payments → check if petition lists real property
    mortgage_amount = _bank_mortgage_amount(bank_signals)
    if mortgage_amount >= _MORTGAGE_MIN_AMOUNT and petition is not None:
        prop_value = (petition or {}).get("real_property_value")
        if prop_value is None or prop_value == 0:
            signals.append(CrossRefSignal(
                signal_id="UNDISCLOSED_PROPERTY",
                severity="red",
                label="Undisclosed Real Property",
                description=(
                    f"${mortgage_amount:,.0f} in mortgage servicer payments detected in bank records "
                    f"but Schedule A/B lists no real property "
                    f"(declared value: {'$0' if prop_value == 0 else 'not stated'}). "
                    "Pattern consistent with ownership of undisclosed real estate."
                ),
                gap_usd=round(mortgage_amount, 2),
                sources=["bank", "petition"],
                recommended_action=(
                    "Conduct land title search in all jurisdictions where debtor has resided "
                    "in the last 5 years. Subpoena mortgage servicer for property address, "
                    "loan balance, and registered borrower name."
                ),
                confidence=0.80,
            ))

    # ── 4. ASSET UNDERSTATEMENT ───────────────────────────────────────────────
    # Estimate minimum asset base from bank activity; compare to petition total
    if petition is not None and total_credits > 0:
        total_declared = (petition or {}).get("total_assets_declared") or 0
        # Conservative estimate: liquid assets implied by bank credit volume (3-month window)
        # Divide annual deposits by 4 to get rough quarterly holding estimate
        implied_liquid = total_credits / 4
        gap = implied_liquid - total_declared
        if total_declared < implied_liquid * 0.25 and gap >= _MIN_GAP_AMOUNT:
            signals.append(CrossRefSignal(
                signal_id="ASSET_UNDERSTATEMENT",
                severity="amber",
                label="Asset Declaration May Be Materially Incomplete",
                description=(
                    f"Petition declares total assets of ${total_declared:,.0f}. "
                    f"Bank statement shows ${total_credits:,.0f} in deposits — "
                    f"implied liquid asset base (${implied_liquid:,.0f}) is "
                    f"{implied_liquid / max(total_declared, 1):.1f}× declared total. "
                    "Either additional asset accounts exist outside this statement period "
                    "or significant assets have been omitted from Schedule A/B."
                ),
                gap_usd=round(gap, 2),
                sources=["bank", "petition"],
                recommended_action=(
                    "Request complete list of all financial accounts held in last 2 years. "
                    "Issue section 341 examination subpoena for supporting documentation "
                    "of all Schedule A/B entries."
                ),
                confidence=0.60,
            ))

    # ── 5. SCHEDULE I INCOME MISMATCH ────────────────────────────────────────
    # Annualise Schedule I monthly income; compare to actual bank deposits
    if petition is not None and total_credits > 0:
        monthly_income = (petition or {}).get("monthly_income")
        if monthly_income is not None and monthly_income > 0:
            annualised = monthly_income * 12
            gap = total_credits - annualised
            ratio = total_credits / annualised if annualised > 0 else 99
            if ratio >= 1.4 and gap >= _MIN_GAP_AMOUNT:
                signals.append(CrossRefSignal(
                    signal_id="INCOME_MISMATCH",
                    severity="amber" if ratio < 2.0 else "red",
                    label="Schedule I Income Understates Bank Deposits",
                    description=(
                        f"Schedule I declares ${monthly_income:,.0f}/month (${annualised:,.0f}/year). "
                        f"Bank deposits total ${total_credits:,.0f} — "
                        f"${gap:,.0f} ({ratio:.1f}×) above the declared level. "
                        "Income in Schedule I may be understated."
                    ),
                    gap_usd=round(gap, 2),
                    sources=["bank", "petition"],
                    recommended_action=(
                        "Request payroll records, 1099s, and business bank statements "
                        "for the 12 months prior to filing. Compare all income sources "
                        "against Schedule I line items."
                    ),
                    confidence=0.70,
                ))

    # ── 6. SOFA INCOME OMISSION ───────────────────────────────────────────────
    # SOFA should declare prior-year income; if blank but tax return shows income
    if tax is not None and petition is not None:
        tax_income = tax.get("agi") or tax.get("total_income")
        sofa_yr1   = petition.get("sofa_income_yr1")
        if tax_income and tax_income > 0 and sofa_yr1 is None:
            signals.append(CrossRefSignal(
                signal_id="SOFA_OMISSION",
                severity="amber",
                label="SOFA Income History Blank — Tax Return Shows Income",
                description=(
                    f"Tax return shows AGI of ${tax_income:,.0f} "
                    f"(year {tax.get('tax_year', 'unknown')}). "
                    "Statement of Financial Affairs shows no prior-year income declared. "
                    "SOFA Part 2 is required — omission may be intentional concealment."
                ),
                gap_usd=round(tax_income, 2),
                sources=["tax", "petition"],
                recommended_action=(
                    "Require amended SOFA with complete income history for last 2 years. "
                    "Cross-reference against all W-2s, 1099s, and K-1s."
                ),
                confidence=0.75,
            ))

    # ── 7. INSIDER PAYMENT CONCEALMENT ───────────────────────────────────────
    # Bank shows P2P transfers; SOFA has no insider payments
    p2p_amount = _bank_p2p_amount(bank_signals)
    if p2p_amount > _MIN_GAP_AMOUNT and petition is not None:
        insider = petition.get("insider_payments")
        if insider is None or insider == 0:
            signals.append(CrossRefSignal(
                signal_id="INSIDER_CONCEALMENT",
                severity="amber",
                label="P2P Payments Not Disclosed as Insider Transfers",
                description=(
                    f"${p2p_amount:,.0f} in P2P / mobile payments detected in bank records. "
                    "SOFA shows no insider payments. If any recipients are related parties, "
                    "family members, or business associates, these must be disclosed "
                    "and may be recoverable as preference or fraudulent transfers."
                ),
                gap_usd=round(p2p_amount, 2),
                sources=["bank", "petition"],
                recommended_action=(
                    "Subpoena Venmo, Cash App, PayPal, and Zelle for recipient KYC records. "
                    "Identify all recipients. Cross-reference against family members, "
                    "employees, and business associates for insider relationship."
                ),
                confidence=0.65,
            ))

    return signals


# ── Supporting functions ──────────────────────────────────────────────────────

def _best_declared_income(tax: Optional[dict], petition: Optional[dict]) -> Optional[float]:
    """Return the best available declared annual income figure."""
    candidates = []
    if tax:
        for field in ("agi", "total_income", "wages"):
            v = tax.get(field)
            if v and v > 0:
                candidates.append(v)
    if petition:
        mi = petition.get("monthly_income")
        if mi and mi > 0:
            candidates.append(mi * 12)
    return max(candidates) if candidates else None


def _bank_crypto_amount(signals: list[dict]) -> float:
    total = 0.0
    for s in signals:
        if s.get("signal_type") == "digital_asset" and not s.get("is_bleed"):
            total += abs(s.get("amount", 0))
    return round(total, 2)


def _bank_mortgage_amount(signals: list[dict]) -> float:
    total = 0.0
    for s in signals:
        if s.get("signal_type") == "real_estate":
            total += abs(s.get("amount", 0))
    return round(total, 2)


def _bank_p2p_amount(signals: list[dict]) -> float:
    total = 0.0
    for s in signals:
        if s.get("signal_type") == "p2p_transfer":
            total += abs(s.get("amount", 0))
    return round(total, 2)


def _available_sources(tax: Optional[dict], petition: Optional[dict]) -> list[str]:
    sources = []
    if tax and tax.get("is_tax_return"):
        sources.append("tax")
    if petition and petition.get("is_petition"):
        sources.append("petition")
    return sources


# ── Summary builder ───────────────────────────────────────────────────────────

def build_crossref_summary(signals: list[CrossRefSignal]) -> dict:
    """
    Produce a summary dict suitable for inclusion in the Verify+ result.
    """
    if not signals:
        return {
            "available": True,
            "signal_count": 0,
            "red_count": 0,
            "amber_count": 0,
            "total_gap_usd": 0,
            "signals": [],
            "headline": "No cross-document discrepancies detected.",
        }

    red   = [s for s in signals if s.severity == "red"]
    amber = [s for s in signals if s.severity == "amber"]
    total_gap = sum(s.gap_usd for s in signals)

    if red:
        headline = (
            f"{len(red)} critical discrepanc{'y' if len(red)==1 else 'ies'} — "
            f"${total_gap:,.0f} gap between documents"
        )
    elif amber:
        headline = (
            f"{len(amber)} discrepanc{'y' if len(amber)==1 else 'ies'} requiring investigation"
        )
    else:
        headline = "Minor discrepancies only."

    return {
        "available": True,
        "signal_count": len(signals),
        "red_count": len(red),
        "amber_count": len(amber),
        "total_gap_usd": round(total_gap, 2),
        "headline": headline,
        "signals": [
            {
                "signal_id": s.signal_id,
                "severity": s.severity,
                "label": s.label,
                "description": s.description,
                "gap_usd": s.gap_usd,
                "sources": s.sources,
                "recommended_action": s.recommended_action,
                "confidence": s.confidence,
            }
            for s in sorted(signals, key=lambda x: {"red": 0, "amber": 1, "green": 2}[x.severity])
        ],
    }
