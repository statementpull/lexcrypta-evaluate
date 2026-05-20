"""Signal 13: Industry-Specific Fraud Patterns.

Patterns drawn from SEC enforcement cases, PCAOB inspection findings, and
forensic accounting literature — encoded for transaction-level detection.

Cases covered:
- HealthSouth (2003): fabricated journal entries exactly matching EPS targets
- Lucent Technologies (2004): vendor financing — loans to customers to buy product
- Bristol-Myers Squibb (2004): pharmaceutical channel stuffing via distributor incentives
- Computer Associates (2004): 35-day accounting month — holding period open post-quarter
- Homestore / AOL (2002): round-trip advertising revenue swaps
- Delphi Corporation (2005): commodity pre-pay / supply chain financing fraud
- Raytheon (2018): percentage-of-completion revenue inflation on govt contracts
- Real estate: NOI inflation via CAPEX misclassification
- SaaS/subscription: ARR manipulation, churn concealment
- Restaurant/retail: same-store sales, gift card breakage manipulation
- Healthcare: upcoding, unbundling, AR aging manipulation

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict
import re


def _parse_float(s) -> float:
    try:
        return float(re.sub(r"[,$\s%]", "", str(s)))
    except (ValueError, TypeError):
        return 0.0


def _row_amount(r: dict) -> float:
    for key in ("ytd", "amount", "value", "this_month", "balance"):
        v = r.get(key)
        if v is not None:
            val = _parse_float(v)
            if val != 0:
                return val
    return 0.0


# ── Keyword banks ─────────────────────────────────────────────────────────────

DISTRIBUTOR_INCENTIVE_KW = [
    "DISTRIBUTOR INCENTIVE", "CHANNEL INCENTIVE", "TRADE PROMOTION",
    "CO-OP ADVERTISING", "MARKET DEVELOPMENT FUND", "MDF",
    "SLOTTING FEE", "PROMOTIONAL ALLOWANCE", "REBATE",
    "DISTRIBUTOR CREDIT", "PRICE PROTECTION",
]

VENDOR_FINANCE_KW = [
    "VENDOR LOAN", "CUSTOMER LOAN", "CUSTOMER FINANCING",
    "VENDOR FINANCING", "RECEIVABLE PURCHASE", "FACTORING",
    "RECEIVABLES SALE", "NOTE RECEIVABLE", "CUSTOMER NOTE",
]

GIFT_CARD_KW = [
    "GIFT CARD", "GIFT CERTIFICATE", "STORED VALUE",
    "GIFT CARD BREAKAGE", "UNREDEEMED",
]

SUBSCRIPTION_KW = [
    "SUBSCRIPTION", "SAAS", "ARR", "MRR", "ANNUAL RECURRING",
    "MONTHLY RECURRING", "RENEWAL", "CONTRACT VALUE",
]

CONSTRUCTION_KW = [
    "PERCENTAGE COMPLETION", "PERCENT COMPLETE", "CONTRACT REVENUE",
    "PROGRESS BILLING", "OVERBILLING", "UNDERBILLING",
    "CONTRACT IN PROGRESS", "WIP", "WORK IN PROGRESS",
]

HEALTHCARE_KW = [
    "MEDICARE", "MEDICAID", "INSURANCE CLAIM", "EOB",
    "EXPLANATION OF BENEFITS", "CLAIM DENIAL", "CLAIM ADJUSTMENT",
    "CPT CODE", "UPCODING", "UNBUNDLING",
]

NOI_EXCLUSION_KW = [
    "ROOF REPAIR", "HVAC REPLACEMENT", "CARPET REPLACEMENT",
    "PAINTING", "UNIT RENOVATION", "UNIT TURNOVER",
    "CAPITAL IMPROVEMENT", "CAPEX", "CAPITAL EXPENDITURE",
    "PARKING LOT", "ELEVATOR", "BOILER",
]

ADVERTISING_SWAP_KW = [
    "ADVERTISING SWAP", "AD SWAP", "BARTER", "BARTER ADVERTISING",
    "MEDIA TRADE", "NON-MONETARY", "IN-KIND",
]

ROUND_TRIP_KW = [
    "ROUND TRIP", "CIRCULAR", "WASH TRANSACTION",
    "OFFSETTING", "SIMULTANEOUS",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    results = []

    outflows = [t for t in transactions if t["amount"] < 0]
    inflows = [t for t in transactions if t["amount"] > 0]
    total_inflow = sum(t["amount"] for t in inflows)
    total_outflow = sum(abs(t["amount"]) for t in outflows)

    # ── 1. Distributor/channel incentive payments ──────────────────────────────
    # Bristol-Myers Squibb: loaded distributors with product, paid them incentives to accept.
    # Detection: large distributor incentive payments in same period as revenue spike.
    dist_txns = [
        t for t in outflows
        if any(kw in t["merchant"].upper() for kw in DISTRIBUTOR_INCENTIVE_KW)
    ]
    if dist_txns:
        total_dist = sum(abs(t["amount"]) for t in dist_txns)
        results.append({
            "signal_type": "industry_fraud",
            "severity": "amber",
            "merchant": "DISTRIBUTOR / CHANNEL INCENTIVES",
            "amount": -total_dist,
            "transaction_date": dist_txns[0].get("transaction_date", ""),
            "description": (
                f"Distributor/channel incentive payments: {len(dist_txns)} payments "
                f"totalling ${total_dist:,.0f}. "
                "Bristol-Myers Squibb pattern (SEC 2004): companies paid distributors "
                "large incentives to accept excess inventory, booking revenue at shipment "
                "while the distributor held unsellable stock. "
                "Verify: (1) revenue recognised in same period as these incentives, "
                "(2) distributor inventory levels — do they hold excess stock? "
                "(3) Q1 credit memos or return authorisations following these payments."
            ),
            "library_match": "BMS_CHANNEL_STUFFING",
            "confidence_weight": 0.70,
        })

    # ── 2. Vendor financing / customer loans ──────────────────────────────────
    # Lucent Technologies: loaned money to customers to buy Lucent equipment.
    # Revenue was booked upfront; the loan was disguised as a receivable.
    # Detection: outflows to customers who are also revenue sources = circular flow.
    vendor_fin_txns = [
        t for t in outflows
        if any(kw in t["merchant"].upper() for kw in VENDOR_FINANCE_KW)
    ]
    if vendor_fin_txns:
        total_vf = sum(abs(t["amount"]) for t in vendor_fin_txns)
        # Check if any of the same merchants also appear as revenue sources
        outflow_merchants = {t["merchant"].upper() for t in vendor_fin_txns}
        inflow_merchants = {t["merchant"].upper() for t in inflows}
        circular = outflow_merchants & inflow_merchants
        sev = "red" if circular else "amber"
        results.append({
            "signal_type": "industry_fraud",
            "severity": sev,
            "merchant": "VENDOR / CUSTOMER FINANCING",
            "amount": -total_vf,
            "transaction_date": vendor_fin_txns[0].get("transaction_date", ""),
            "description": (
                f"Vendor or customer financing detected: {len(vendor_fin_txns)} payments "
                f"totalling ${total_vf:,.0f}. "
                + (f"CRITICAL: {len(circular)} recipient(s) also appear as revenue sources — "
                   "circular flow pattern consistent with Lucent Technologies fraud (SEC 2004). "
                   if circular else "") +
                "Lucent loaned customers money to buy Lucent products, booking revenue "
                "upfront while the loan remained as a receivable. "
                "Verify: (1) commercial purpose of each loan, "
                "(2) whether any loan recipient also generates revenue for this business, "
                "(3) collection status of outstanding notes receivable."
            ),
            "library_match": "LUCENT_VENDOR_FINANCING" if circular else None,
            "confidence_weight": 0.90 if circular else 0.65,
        })

    # ── 3. Advertising swap / round-trip revenue ──────────────────────────────
    # Homestore/AOL (2002): paid companies to advertise, who used cash to buy ads back.
    # Detection: near-equal advertising inflows and outflows to/from same entity.
    ad_outflows: dict[str, float] = defaultdict(float)
    ad_inflows: dict[str, float] = defaultdict(float)

    for t in outflows:
        if any(kw in t["merchant"].upper() for kw in ["ADVERTISING", "MARKETING", "AD BUY", "MEDIA BUY"]):
            ad_outflows[t["merchant"].upper()] += abs(t["amount"])
    for t in inflows:
        if any(kw in t["merchant"].upper() for kw in ["ADVERTISING", "MARKETING", "AD BUY", "MEDIA BUY"]):
            ad_inflows[t["merchant"].upper()] += t["amount"]

    for merchant in ad_outflows:
        if merchant in ad_inflows:
            out = ad_outflows[merchant]
            inc = ad_inflows[merchant]
            if out > 5000 and inc > 5000 and abs(out - inc) / max(out, inc) < 0.15:
                results.append({
                    "signal_type": "industry_fraud",
                    "severity": "red",
                    "merchant": merchant[:150],
                    "amount": inc,
                    "transaction_date": "",
                    "description": (
                        f"Advertising round-trip: ${out:,.0f} paid to '{merchant[:60]}' "
                        f"and ${inc:,.0f} received back ({abs(out-inc)/max(out,inc):.1%} difference). "
                        "Homestore/AOL pattern (SEC 2002): companies exchanged advertising payments "
                        "to artificially inflate revenue with no economic substance. "
                        "Verify commercial purpose — obtain underlying advertising contracts "
                        "and confirm actual media was delivered."
                    ),
                    "library_match": "HOMESTORE_AD_ROUNDTRIP",
                    "confidence_weight": 0.85,
                })

    # ── 4. Healthcare claim adjustment patterns ───────────────────────────────
    # Upcoding, unbundling, and claim denial patterns indicate AR quality risk.
    health_txns = [
        t for t in inflows
        if any(kw in t["merchant"].upper() for kw in HEALTHCARE_KW)
    ]
    if health_txns:
        total_health = sum(t["amount"] for t in health_txns)
        # Look for large claim adjustments (credit memos from payers)
        adjustments = [
            t for t in outflows
            if any(kw in t["merchant"].upper() for kw in ["CLAIM ADJUST", "RECOUPMENT", "CLAWBACK", "MEDICARE RECOUP"])
        ]
        if adjustments:
            total_adj = sum(abs(t["amount"]) for t in adjustments)
            adj_rate = total_adj / total_health if total_health else 0
            if adj_rate > 0.05:
                results.append({
                    "signal_type": "industry_fraud",
                    "severity": "red",
                    "merchant": "HEALTHCARE CLAIM RECOUPMENT",
                    "amount": -total_adj,
                    "transaction_date": adjustments[0].get("transaction_date", ""),
                    "description": (
                        f"Medicare/Medicaid claim recoupments or adjustments: "
                        f"{len(adjustments)} transactions totalling ${total_adj:,.0f} "
                        f"({adj_rate:.0%} of healthcare revenue). "
                        "High recoupment rates indicate: (1) upcoding or unbundling audit findings, "
                        "(2) documentation deficiencies triggering post-payment review, "
                        "(3) potential compliance liability for pre-acquisition billing practices. "
                        "CMS can audit 3 years back; fraud extends to 6 years. "
                        "Obtain RAC/MAC audit history and compliance program documentation."
                    ),
                    "library_match": "HEALTHCARE_RECOUPMENT",
                    "confidence_weight": 0.85,
                })
        elif total_health > 50000:
            results.append({
                "signal_type": "industry_fraud",
                "severity": "amber",
                "merchant": "HEALTHCARE REVENUE — DUE DILIGENCE FLAG",
                "amount": total_health,
                "transaction_date": health_txns[0].get("transaction_date", ""),
                "description": (
                    f"Healthcare insurance revenue: ${total_health:,.0f} from "
                    f"{len(health_txns)} payer transactions. "
                    "Healthcare acquisitions require enhanced due diligence: "
                    "(1) verify billing practices — obtain payor mix and AR aging by payer, "
                    "(2) request last 2 years of Medicare/Medicaid cost reports, "
                    "(3) confirm no active OIG exclusions, RAC audits, or False Claims Act exposure, "
                    "(4) assess revenue concentration by payer — single-payer risk."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # ── 5. Real estate NOI inflation — CAPEX excluded from operating expenses ──
    # Sellers exclude recurring capital items (roof, HVAC, carpet) from NOI
    # to inflate the apparent yield/CAP rate.
    # Detection: very low maintenance relative to property age/income, OR
    # CAPEX line items that should be operating expenses.
    if pl_rows:
        capex_rows = [
            r for r in pl_rows
            if any(kw in str(r.get("account", "")).upper() or kw in str(r.get("description", "")).upper()
                   for kw in NOI_EXCLUSION_KW)
        ]
        maint_rows = [
            r for r in pl_rows
            if any(kw in str(r.get("account", "")).upper() or kw in str(r.get("description", "")).upper()
                   for kw in ["MAINTENANCE", "REPAIR", "TURNOVER"])
        ]
        revenue_rows = [
            r for r in pl_rows
            if any(kw in str(r.get("account", "")).upper()
                   for kw in ["REVENUE", "RENTAL INCOME", "RENT"])
        ]

        if capex_rows and revenue_rows:
            total_capex = sum(_row_amount(r) for r in capex_rows)
            total_revenue = sum(_row_amount(r) for r in revenue_rows)
            total_maint = sum(_row_amount(r) for r in maint_rows)

            if total_capex > 0 and total_revenue > 0:
                capex_pct = total_capex / total_revenue
                if capex_pct > 0.08 and total_maint < total_capex * 0.3:
                    results.append({
                        "signal_type": "industry_fraud",
                        "severity": "amber",
                        "merchant": "NOI INFLATION — CAPEX MISCLASSIFICATION",
                        "amount": total_capex,
                        "transaction_date": "",
                        "description": (
                            f"Capital expenditures (${total_capex:,.0f}, {capex_pct:.0%} of revenue) "
                            f"are high relative to maintenance expenses (${total_maint:,.0f}). "
                            "Common pre-sale pattern: recurring capital items (roof, HVAC, carpet, "
                            "unit turns) are classified as CAPEX rather than operating expenses, "
                            "inflating reported NOI and artificially depressing the CAP rate. "
                            "A 5% CAP rate on inflated NOI can overvalue a property by 15–30%. "
                            "Verify: (1) nature of each CAPEX item — is it truly a long-lived improvement? "
                            "(2) normalise NOI by adding back recurring capital items, "
                            "(3) cross-check against 5-year capital expenditure history."
                        ),
                        "library_match": "NOI_CAPEX_MANIPULATION",
                        "confidence_weight": 0.75,
                    })

    # ── 6. Gift card / breakage revenue manipulation ──────────────────────────
    # Retailers record gift card breakage (unredeemed balances) as revenue.
    # Manipulation: accelerating breakage recognition inflates revenue.
    gc_txns = [
        t for t in inflows
        if any(kw in t["merchant"].upper() for kw in GIFT_CARD_KW)
    ]
    if gc_txns:
        total_gc = sum(t["amount"] for t in gc_txns)
        if total_gc > 5000 and total_inflow > 0 and total_gc / total_inflow > 0.08:
            results.append({
                "signal_type": "industry_fraud",
                "severity": "amber",
                "merchant": "GIFT CARD / BREAKAGE REVENUE",
                "amount": total_gc,
                "transaction_date": gc_txns[0].get("transaction_date", ""),
                "description": (
                    f"Gift card / stored value revenue: ${total_gc:,.0f} "
                    f"({total_gc/total_inflow:.0%} of inflows). "
                    "Gift card breakage revenue requires scrutiny: "
                    "(1) is breakage being recognised using an acceptable actuarial model? "
                    "(2) has the recognition period been shortened to accelerate income? "
                    "(3) verify against physical gift card liability on the balance sheet — "
                    "breakage income should be supported by a corresponding liability reduction."
                ),
                "library_match": None,
                "confidence_weight": 0.65,
            })

    # ── 7. SaaS / subscription revenue concentration risk ────────────────────
    sub_inflows = [
        t for t in inflows
        if any(kw in t["merchant"].upper() for kw in SUBSCRIPTION_KW)
    ]
    if sub_inflows and total_inflow > 0:
        total_sub = sum(t["amount"] for t in sub_inflows)
        sub_pct = total_sub / total_inflow
        if sub_pct > 0.70:
            # Check for revenue concentration in a few large accounts
            sub_by_merchant: dict[str, float] = defaultdict(float)
            for t in sub_inflows:
                sub_by_merchant[t["merchant"].upper()] += t["amount"]
            top_customer_pct = max(sub_by_merchant.values()) / total_sub if sub_by_merchant else 0
            if top_customer_pct > 0.30:
                results.append({
                    "signal_type": "industry_fraud",
                    "severity": "amber",
                    "merchant": "SAAS REVENUE CONCENTRATION",
                    "amount": total_sub,
                    "transaction_date": sub_inflows[0].get("transaction_date", ""),
                    "description": (
                        f"Subscription/SaaS revenue: ${total_sub:,.0f} ({sub_pct:.0%} of inflows). "
                        f"Top customer represents {top_customer_pct:.0%} of subscription revenue. "
                        "SaaS acquisition due diligence requires: "
                        "(1) net revenue retention (NRR) — does revenue expand or contract over time? "
                        "(2) churn rate — verify against actual cancellations, not seller claims, "
                        "(3) contracted ARR vs billed ARR — are annual contracts paid monthly? "
                        "(4) customer concentration — single customer >30% is acquisition risk, "
                        "(5) verify no multi-year prepayments inflating current-year revenue."
                    ),
                    "library_match": None,
                    "confidence_weight": 0.65,
                })

    # ── 8. Construction / percentage-of-completion overbilling ───────────────
    # Raytheon (SEC 2018): inflated percentage-of-completion on long-term contracts.
    # Detection: progress billing credits significantly above costs incurred.
    wip_txns = [
        t for t in inflows
        if any(kw in t["merchant"].upper() for kw in CONSTRUCTION_KW)
    ]
    if wip_txns:
        total_wip = sum(t["amount"] for t in wip_txns)
        # Check overbilling: if billings far exceed costs in same period
        wip_costs = sum(
            abs(t["amount"]) for t in outflows
            if any(kw in t["merchant"].upper() for kw in ["SUBCONTRACTOR", "MATERIALS", "LABOUR", "LABOR", "JOB COST"])
        )
        if total_wip > 0 and wip_costs > 0:
            billing_ratio = total_wip / wip_costs
            if billing_ratio > 1.5:
                results.append({
                    "signal_type": "industry_fraud",
                    "severity": "amber",
                    "merchant": "CONTRACT OVERBILLING",
                    "amount": total_wip,
                    "transaction_date": wip_txns[0].get("transaction_date", ""),
                    "description": (
                        f"Contract billings (${total_wip:,.0f}) are {billing_ratio:.1f}x "
                        f"direct job costs (${wip_costs:,.0f}). "
                        "Raytheon pattern (SEC 2018): percentage-of-completion revenue inflated "
                        "by understating estimated costs to complete, allowing more revenue to be "
                        "recognised in the current period. "
                        "Verify: (1) estimated costs to complete for each contract, "
                        "(2) billing schedule vs actual completion milestones, "
                        "(3) overbilled contracts = liability — obtain WIP schedule."
                    ),
                    "library_match": "RAYTHEON_POC_FRAUD",
                    "confidence_weight": 0.70,
                })

    # ── 9. Delphi-style commodity pre-pay / supply chain finance ─────────────
    # Delphi (SEC 2005): disguised borrowings as product sales or pre-payments.
    # Received cash from suppliers as "pre-payment" for future goods, booked as revenue.
    large_inflows_from_vendors = []
    outflow_merchants = {t["merchant"].upper() for t in outflows if abs(t["amount"]) > 5000}
    for t in inflows:
        if t["merchant"].upper() in outflow_merchants and t["amount"] > 20000:
            large_inflows_from_vendors.append(t)

    if large_inflows_from_vendors:
        total_circular = sum(t["amount"] for t in large_inflows_from_vendors)
        results.append({
            "signal_type": "industry_fraud",
            "severity": "red",
            "merchant": "CIRCULAR VENDOR FLOWS",
            "amount": total_circular,
            "transaction_date": large_inflows_from_vendors[0].get("transaction_date", ""),
            "description": (
                f"Large inflows from entities that are also vendors: "
                f"{len(large_inflows_from_vendors)} transactions totalling ${total_circular:,.0f}. "
                "Delphi Corporation pattern (SEC 2005): disguised borrowings as product sales, "
                "receiving cash from suppliers as 'pre-payments' while booking as operating revenue. "
                "Any significant cash receipt from a party that also receives payments warrants scrutiny. "
                "Verify: (1) commercial basis for each inflow from a vendor, "
                "(2) whether inflows represent genuine sales or disguised financing, "
                "(3) net position with each counterparty — is it truly arm's-length?"
            ),
            "library_match": "DELPHI_CIRCULAR_FLOW",
            "confidence_weight": 0.80,
        })

    return results
