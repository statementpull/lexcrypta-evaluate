"""
Verify+ — Trustee Intelligence Engine
======================================
Converts raw Verify signal output into a binary trustee verdict with
documented reasoning.  Used for quick matter screening before committing
full trustee resources.

Verdict logic
-------------
PROCEED         LAS >= 40  OR  red signals >= 2  OR  priority_signals >= 3
SEEK DISCLOSURE LAS >= 15  OR  any priority signal present
DECLINE         LAS < 15   AND no priority signals

Priority types (asset-recovery focus): digital_asset, real_estate,
hidden_assets, aml_structuring
"""

from .las_score import calculate_las

# ── Trustee signal reason templates ──────────────────────────────────────────

_REASON_MAP: dict = {
    "digital_asset": {
        "label": "Crypto Exchange Activity",
        "note": (
            "{count} transaction{pl} totalling ${amount:,.0f} to "
            "{merchants} — cryptocurrency holdings absent from petition. "
            "Exchange subpoena required."
        ),
        "priority": 1,
    },
    "real_estate": {
        "label": "Undisclosed Property Signal",
        "note": (
            "Mortgage or property-related payments of ${amount:,.0f} detected. "
            "Pattern consistent with undisclosed real estate — "
            "conduct land title search immediately."
        ),
        "priority": 2,
    },
    "hidden_assets": {
        "label": "Concealed Asset Activity",
        "note": (
            "${amount:,.0f} in luxury or physical asset purchases. "
            "Cross-reference against asset schedule in petition: {merchants}."
        ),
        "priority": 3,
    },
    "aml_structuring": {
        "label": "Structuring Behaviour",
        "note": (
            "{count} cash transaction{pl} showing sub-threshold pattern — "
            "consistent with deliberate asset-concealment strategy. "
            "Obtain ATM location records."
        ),
        "priority": 4,
    },
    "owner_compensation": {
        "label": "Undisclosed Related-Party Transfers",
        "note": (
            "Recurring payments totalling ${amount:,.0f} to {merchants} — "
            "entity absent from disclosed corporate structure. "
            "Obtain full intercompany reconciliation."
        ),
        "priority": 5,
    },
    "cash_flow": {
        "label": "Cash Flow Anomaly",
        "note": (
            "Income pattern inconsistent with declared financial position — "
            "${amount:,.0f} in unexplained flows. Verify revenue recognition."
        ),
        "priority": 6,
    },
    "behavioural": {
        "label": "Unusual High-Value Expenditure",
        "note": (
            "High-value irregular payments of ${amount:,.0f} to {merchants}. "
            "Obtain supporting documentation and verify beneficiary identity."
        ),
        "priority": 7,
    },
    "liability": {
        "label": "Undisclosed Financing Obligation",
        "note": (
            "Recurring payments consistent with undisclosed loan or lease — "
            "${amount:,.0f} total. Cross-reference against disclosed debt schedule."
        ),
        "priority": 8,
    },
    "p2p_transfer": {
        "label": "P2P / Mobile Payments to Named Individuals",
        "note": (
            "{count} payment{pl} totalling ${amount:,.0f} via mobile platforms to "
            "{merchants}. Recurring P2P payments from a business account indicate "
            "potential off-books payroll or related-party asset transfers — "
            "subpoena platform KYC records."
        ),
        "priority": 3,   # Treat similarly to hidden_assets — concrete, recoverable
    },
}

_PRIORITY_TYPES = {"digital_asset", "real_estate", "hidden_assets", "aml_structuring"}

# Minimum flagged value (absolute dollars) required before PROCEED fires.
# Below this, PROCEED downgrades to SEEK DISCLOSURE — signals may be real but
# the dollar volume does not justify trustee engagement costs.
# Trustee baseline cost in AU/US starts at ~$5K–$10K; set floor at $10K so
# estimated recovery ($10K × 0.35 = $3,500 low) still covers fees.
_MIN_PROCEED_FLAGGED: float = 10_000.0

_PADDING_REASONS = [
    {
        "label": "Extend Document Set",
        "description": "Upload additional months of bank statements for deeper pattern analysis — recommend 24-month minimum for trustee screening.",
        "severity": "green",
        "signal_type": "none",
        "amount": 0,
    },
    {
        "label": "Statement of Affairs Verification",
        "description": "Request formal Statement of Affairs and compare declared assets against detected transaction patterns.",
        "severity": "green",
        "signal_type": "none",
        "amount": 0,
    },
    {
        "label": "Baseline Assessment",
        "description": "No additional high-priority signals identified in current document set.",
        "severity": "green",
        "signal_type": "none",
        "amount": 0,
    },
]


def _build_reasons(raw_signals: list) -> list:
    """Extract up to 3 trustee reasons from detected signals, ranked by priority.

    Bleed signals (is_bleed=True from digital_asset.py DRAIN_001) are handled
    separately: excluded from transaction count and dollar total (to avoid
    double-counting with per-transaction signals), but their gap data is appended
    to the reason description so trustees see the net exposure clearly.
    """
    by_type: dict = {}
    for s in raw_signals:
        st = s.get("signal_type", s.get("type", ""))
        if st:
            by_type.setdefault(st, []).append(s)

    reasons = []
    type_order = sorted(
        by_type.keys(),
        key=lambda t: (
            _REASON_MAP.get(t, {}).get("priority", 99),
            -sum(abs(s.get("amount", 0)) for s in by_type[t]),
        ),
    )

    for st in type_order:
        if len(reasons) >= 3:
            break
        signals = by_type[st]
        rm = _REASON_MAP.get(st)
        if not rm:
            continue

        # Separate bleed aggregate signals from individual transaction signals
        bleed_signals   = [s for s in signals if s.get("is_bleed")]
        txn_signals     = [s for s in signals if not s.get("is_bleed")]

        # Count and total from transaction signals only (bleed amount=0, avoids double-count)
        count   = len(txn_signals)
        total   = sum(abs(s.get("amount", 0)) for s in txn_signals)

        merchants_raw = list({
            s.get("merchant", s.get("description", ""))
            for s in txn_signals
            if s.get("merchant") or s.get("description")
        })
        merchants = ", ".join(m for m in merchants_raw[:2] if m) or "unidentified entity"
        severity = max(
            (s.get("severity", "amber") for s in signals),
            key=lambda x: {"red": 2, "amber": 1, "green": 0}.get(x, 0),
        )

        # If no individual transaction signals but bleed exists, use bleed merchant
        if not count and bleed_signals:
            merchants = bleed_signals[0].get("merchant", "identified exchange(s)")
            count     = 0  # no discrete transactions to count

        try:
            description = rm["note"].format(
                count=count,
                pl="s" if count != 1 else "",
                amount=total,
                merchants=merchants,
            )
        except (KeyError, ValueError):
            description = rm["label"]

        # Append bleed note if present — surfaces the net gap for trustees
        if bleed_signals:
            bd = bleed_signals[0].get("bleed_data", {})
            net = bd.get("net_bleed_usd", 0)
            out = bd.get("total_outflows_usd", 0)
            back = bd.get("total_inflows_usd", 0)
            if net > 0:
                description += (
                    f" Net position: ${out:,.0f} sent to exchange(s), "
                    f"${back:,.0f} returned to bank account — "
                    f"${net:,.0f} gap indicates crypto withdrawn to external wallet "
                    f"or lost to theft. Subpoena exchange for balance and withdrawal history."
                )

        reasons.append({
            "label": rm["label"],
            "description": description,
            "severity": severity,
            "signal_type": st,
            "amount": round(total, 2),
        })

    # Pad to exactly 3
    for pad in _PADDING_REASONS:
        if len(reasons) >= 3:
            break
        reasons.append(pad)

    return reasons[:3]


def build_verifyplus_result(
    matter_id: int,
    raw_signals: list,
    transactions: list,
    jurisdiction: str = "AU",
) -> dict:
    """Produce a Verify+ trustee screening verdict from raw signal output."""
    total_vol = sum(abs(t.get("amount", 0)) for t in transactions)
    las_score = calculate_las(raw_signals, total_vol)

    priority_sigs = [
        s for s in raw_signals
        if s.get("signal_type", s.get("type", "")) in _PRIORITY_TYPES
    ]
    red_count = sum(1 for s in raw_signals if s.get("severity") == "red")

    # Financial gap — computed BEFORE verdict so the dollar floor can gate PROCEED
    total_credits = round(sum(t.get("amount", 0) for t in transactions if t.get("amount", 0) > 0), 2)
    total_debits = round(sum(abs(t.get("amount", 0)) for t in transactions if t.get("amount", 0) < 0), 2)
    flagged_total = round(sum(abs(s.get("amount", 0)) for s in raw_signals), 2)

    # Step 1 — signal-based verdict (LAS / priority / red count)
    if las_score >= 40 or red_count >= 2 or len(priority_sigs) >= 3:
        _signal_verdict = "PROCEED"
    elif las_score >= 15 or len(priority_sigs) >= 1:
        _signal_verdict = "SEEK DISCLOSURE"
    else:
        _signal_verdict = "DECLINE"

    # Step 2 — minimum viable recovery gate
    # PROCEED is only useful to a trustee when the dollar volume justifies their
    # engagement cost.  If flagged_total is below _MIN_PROCEED_FLAGGED, signals
    # are real but the matter is not commercially worth pursuing — downgrade to
    # SEEK DISCLOSURE so the trustee knows to get more evidence first.
    if _signal_verdict == "PROCEED" and flagged_total < _MIN_PROCEED_FLAGGED:
        verdict = "SEEK DISCLOSURE"
        verdict_cls = "mid"
        confidence = "MODERATE"
        recommended_action = (
            f"Signals detected, but total flagged value (${flagged_total:,.0f}) is below "
            f"the ${_MIN_PROCEED_FLAGGED:,.0f} minimum viable recovery threshold. "
            "Signals may reflect legitimate low-value activity rather than concealed assets. "
            "Obtain a full 24-month statement period and formal Statement of Affairs before "
            "committing trustee resources — recovery is unlikely to cover engagement costs "
            "at current volumes."
        )
    elif _signal_verdict == "PROCEED":
        verdict = "PROCEED"
        verdict_cls = "high"
        confidence = "HIGH"
        recommended_action = (
            "Proceed to full Verify analysis. Issue preservation letters to all identified "
            "institutions within 24 hours. Material assets are likely recoverable — prepare "
            "initial trustee report."
        )
    elif _signal_verdict == "SEEK DISCLOSURE":
        verdict = "SEEK DISCLOSURE"
        verdict_cls = "mid"
        confidence = "MODERATE"
        recommended_action = (
            "Request extended bank statement periods (24 months) and formal Statement of Affairs "
            "before committing trustee resources. Signals present but volume insufficient to "
            "establish recovery threshold."
        )
    else:
        verdict = "DECLINE"
        verdict_cls = "low"
        confidence = "HIGH"
        recommended_action = (
            "Statement pattern is consistent with genuine insolvency. Recovery unlikely to "
            "justify trustee costs based on available evidence. File as low-priority or "
            "seek further creditor instruction."
        )

    # Recovery estimate
    if verdict == "PROCEED" and flagged_total > 0:
        low = max(round(flagged_total * 0.35, -2), 0)
        high = max(round(flagged_total * 0.75, -2), 0)
        recovery_estimate = f"${low:,.0f}–${high:,.0f}"
    elif verdict == "PROCEED":
        recovery_estimate = "Signals detected — value under analysis"
    elif verdict == "SEEK DISCLOSURE":
        recovery_estimate = "Expand document set to quantify"
    else:
        recovery_estimate = "Below viable recovery threshold"

    signal_categories = len({s.get("signal_type", s.get("type", "")) for s in raw_signals if s.get("signal_type") or s.get("type")})
    confidence_note = (
        f"Based on {len(raw_signals)} signal{'s' if len(raw_signals) != 1 else ''} "
        f"across {signal_categories} categor{'ies' if signal_categories != 1 else 'y'}"
    )

    return {
        "available": True,
        "matter_id": matter_id,
        "verdict": verdict,
        "verdict_cls": verdict_cls,
        "confidence": confidence,
        "confidence_note": confidence_note,
        "reasons": _build_reasons(raw_signals),
        "financial_gap": {
            "total_credits": total_credits,
            "total_debits": total_debits,
            "flagged_total": flagged_total,
        },
        "recovery_estimate": recovery_estimate,
        "recommended_action": recommended_action,
        "preference_window": (
            "6 months (unsecured creditors) · "
            "4 years (uncommercial transactions) · "
            "No limit (fraudulent transfers)"
        ),
        "las_score": round(las_score, 1),
    }
