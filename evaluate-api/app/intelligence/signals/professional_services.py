"""Signal 47: Professional Services Billing Analysis.

Unusual professional services spend — legal, accounting, consulting, M&A
advisory — reveals critical deal context that sellers rarely disclose.

Key patterns:
  M&A advisory / sell-side process: Bankers, brokers, and investment advisors
    engaged before a sale run a formal process. Their fees appear as large
    wire transfers to entities with "Capital", "Advisors", "Partners" in the
    name. A business with $80k in M&A advisory fees is actively being sold
    via a structured process — this changes negotiating dynamics.
  Multiple law firms simultaneously: Engaging 2+ law firms at the same time
    signals complex litigation, regulatory investigations, employment disputes,
    or IP battles. Each firm has its own specialty — the combination tells
    the story.
  Spike in consulting pre-sale: Large consulting engagements (McKinsey,
    Deloitte, EY, boutiques) immediately before a sale are often:
    (a) legitimate operational improvement, or
    (b) window dressing — hiring consultants to produce reports that make
    the business look like it has a "transformation story."
  Accounting restatement signals: Repeated auditor/CPA payments within a
    single year, or a switch of audit firm, can signal restatement risk.
  Tax advisory spike: Significant payments to tax counsel before a deal
    often indicate the seller is restructuring for tax efficiency — the buyer
    should understand the resulting structure before closing.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime

LEGAL_KW = [
    "ATTORNEY", "LAW FIRM", "LAW OFFICE", "LEGAL COUNSEL", "SOLICITOR",
    "LLP", " PC ", "PLLC", "BARRISTERS", "CHAMBERS",
    "LITIGATION", "SETTLEMENT PAYMENT", "LEGAL RETAINER",
]

ACCOUNTING_KW = [
    "CPA", "ACCOUNTANT", "ACCOUNTING FIRM", "AUDIT FEE", "AUDITOR",
    "DELOITTE", "KPMG", "ERNST YOUNG", "PWC", "BDO ", "RSM ", "GRANT THORNTON",
    "TAX ADVISORY", "TAX COUNSEL", "FORENSIC ACCOUNTING",
]

CONSULTING_KW = [
    "MCKINSEY", "BAIN ", "BCG ", "ACCENTURE", "BOOZ", "OLIVER WYMAN",
    "MANAGEMENT CONSULTING", "STRATEGY CONSULTING", "BUSINESS CONSULTING",
    "CONSULTING GROUP", "CONSULTING LLC", "CONSULTING INC",
    "ADVISORY GROUP", "ADVISORY LLC", "ADVISORY SERVICES",
]

MA_ADVISORY_KW = [
    "INVESTMENT BANK", "M&A ADVISOR", "SELL-SIDE", "BUY-SIDE",
    "BUSINESS BROKER", "BUSINESS SALE", "DIVESTITURE",
    "MERGER ADVISORY", "ACQUISITION ADVISORY",
    "CAPITAL ADVISORS", "CAPITAL PARTNERS", "CAPITAL GROUP",
    "MIDPOINT CAPITAL", "STOUT RISIUS", "HOULIHAN LOKEY",
    "PIPER SANDLER", "LAZARD", "MOELIS", "JEFFERIES",
    "ENGEL & VOLKERS", "TRANSWORLD BUSINESS",
]

TAX_KW = [
    "TAX COUNSEL", "TAX ATTORNEY", "IRS PAYMENT", "TAX COURT",
    "OFFER IN COMPROMISE", "TAX LIEN", "BACK TAXES",
]


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
    legal_txns, acct_txns, consult_txns, ma_txns, tax_txns = [], [], [], [], []

    for t in transactions:
        if t["amount"] >= 0:
            continue
        m = t["merchant"].upper()
        if any(kw in m for kw in MA_ADVISORY_KW):
            ma_txns.append(t)
        elif any(kw in m for kw in LEGAL_KW):
            legal_txns.append(t)
        elif any(kw in m for kw in ACCOUNTING_KW):
            acct_txns.append(t)
        elif any(kw in m for kw in CONSULTING_KW):
            consult_txns.append(t)
        elif any(kw in m for kw in TAX_KW):
            tax_txns.append(t)

    # M&A advisory — highest signal: seller is running a formal process
    if ma_txns:
        total = sum(abs(t["amount"]) for t in ma_txns)
        advisors = list({t["merchant"] for t in ma_txns})
        results.append({
            "signal_type": "professional_services",
            "severity": "red",
            "merchant": f"M&A ADVISORY FEES: ${total:,.0f} — formal sale process detected",
            "amount": -total,
            "transaction_date": ma_txns[0].get("transaction_date", ""),
            "description": (
                f"M&A advisory / business broker payments: ${total:,.0f} to {len(advisors)} firm(s). "
                "This indicates the seller has engaged professional sell-side representation — "
                "a formal sale process is underway or has concluded. "
                "Implications: (1) multiple buyers may be in the process — time pressure may be real, "
                "(2) the advisor has prepared a Confidential Information Memorandum (CIM) — "
                "request the full CIM and all management presentations provided to other buyers, "
                "(3) the seller's expectations are likely anchored to the advisor's valuation — "
                "understand that anchor before submitting an LOI, "
                "(4) advisory fees are non-recurring — add back to EBITDA in normalisation. "
                "Confirm whether the seller's representation agreement creates exclusivity periods "
                "or tail provisions that could affect post-close advisory fee obligations."
            ),
            "library_match": "PROF_MA_ADVISORY",
            "confidence_weight": 0.85,
        })

    # Multiple law firms
    if legal_txns:
        total = sum(abs(t["amount"]) for t in legal_txns)
        law_firms = list({t["merchant"] for t in legal_txns})
        severity = "red" if len(law_firms) >= 3 else "amber"
        results.append({
            "signal_type": "professional_services",
            "severity": severity,
            "merchant": f"LEGAL FEES: ${total:,.0f} across {len(law_firms)} firm(s)",
            "amount": -total,
            "transaction_date": legal_txns[0].get("transaction_date", ""),
            "description": (
                f"Legal fees: ${total:,.0f} paid to {len(law_firms)} firm(s): "
                f"{', '.join(law_firms[:3])}{'...' if len(law_firms) > 3 else ''}. "
                f"{'Multiple law firms engaged simultaneously — each firm has a specialty: ' if len(law_firms) >= 3 else ''}"
                f"{'cross-reference with litigation search and confirm scope of each engagement. ' if len(law_firms) >= 3 else ''}"
                "Legal fees are generally non-recurring for normalised EBITDA, "
                "but the underlying issue generating the legal work may persist post-close. "
                "Request copies of all active engagement letters and confirm which matters "
                "are buyer-transferable (e.g., ongoing litigation defence). "
                "Ensure legal contingencies are disclosed and estimated in the reps & warranties."
            ),
            "library_match": "PROF_LEGAL_FEES",
            "confidence_weight": 0.70,
        })

    # Consulting spike — check if recent vs historical
    if consult_txns:
        total = sum(abs(t["amount"]) for t in consult_txns)
        monthly: dict[str, float] = defaultdict(float)
        for t in consult_txns:
            d = _parse_date(t.get("transaction_date", ""))
            if d:
                monthly[f"{d.year}-{d.month:02d}"] += abs(t["amount"])

        severity = "amber"
        spike_note = ""
        if len(monthly) >= 4:
            months = sorted(monthly.keys())
            vals = [monthly[m] for m in months]
            prior_avg = sum(vals[:-3]) / max(len(vals) - 3, 1)
            recent_avg = sum(vals[-3:]) / 3
            if prior_avg > 0 and recent_avg > prior_avg * 2.5:
                severity = "red"
                spike_note = (
                    f" SPIKE DETECTED: consulting spend in the last 3 months "
                    f"(${recent_avg:,.0f}/month) is {recent_avg/prior_avg:.1f}x the prior average "
                    f"(${prior_avg:,.0f}/month). Pre-sale consulting spike may be window dressing."
                )

        results.append({
            "signal_type": "professional_services",
            "severity": severity,
            "merchant": f"CONSULTING FEES: ${total:,.0f}",
            "amount": -total,
            "transaction_date": consult_txns[0].get("transaction_date", ""),
            "description": (
                f"Management/strategy consulting fees: ${total:,.0f}.{spike_note} "
                "Consulting fees are non-recurring and should be added back to normalised EBITDA. "
                "Verify: (1) whether the consulting engagement produced a deliverable that is "
                "still in use and generates ongoing value, (2) whether the seller is using "
                "consulting fees to justify a 'transformation story' in their CIM — "
                "if so, verify that the transformation is measurable in current-period results, "
                "(3) any consulting agreements with change-of-control clauses that could "
                "trigger payments at closing."
            ),
            "library_match": "PROF_CONSULTING",
            "confidence_weight": 0.60,
        })

    # Tax stress
    if tax_txns:
        total = sum(abs(t["amount"]) for t in tax_txns)
        results.append({
            "signal_type": "professional_services",
            "severity": "red",
            "merchant": f"TAX ADVISORY / IRS: ${total:,.0f}",
            "amount": -total,
            "transaction_date": tax_txns[0].get("transaction_date", ""),
            "description": (
                f"Tax advisory and IRS-related payments: ${total:,.0f}. "
                "Payments to tax counsel, IRS offer in compromise, or back tax payments "
                "indicate the business has unresolved tax obligations. "
                "CRITICAL: (1) request a current tax compliance certificate for all jurisdictions, "
                "(2) obtain copies of all IRS and state revenue correspondence, "
                "(3) confirm no outstanding tax liens that attach to assets being acquired, "
                "(4) if S-Corp or pass-through: confirm all personal tax obligations of the "
                "selling shareholder are current — back taxes on pass-through income can "
                "create successor liability risk in asset deals."
            ),
            "library_match": "PROF_TAX_STRESS",
            "confidence_weight": 0.80,
        })

    return results
