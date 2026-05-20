"""Signal 51: Geographic & Market Concentration Risk.

Geographic concentration affects regulatory complexity, market risk,
and post-acquisition scalability. Bank transactions reveal multi-state
operations, international exposure, and single-market dependency.

Key signals:
  State tax payment diversity: Sales tax, payroll tax, and income tax
    payments to multiple states confirm multi-state operations. Single-state
    operations have simpler compliance but higher concentration risk.
  Sales tax nexus: Businesses making sales tax payments to 5+ states have
    economic nexus in those states — the buyer inherits those obligations
    AND any historical under-collection exposure.
  Payroll multi-state: Employees in multiple states trigger unemployment
    insurance, workers' comp, and payroll tax obligations in each.
  International payments: Wire transfers to foreign jurisdictions, foreign
    bank fees, or SWIFT charges indicate international operations or suppliers.
  Local government revenue dependency: Grant payments from a single county
    or municipality — if the municipality changes priorities, this revenue
    disappears.
  Single-market risk: All revenue from one geographic area means any
    local economic shock (plant closure, natural disaster, employer exit)
    disproportionately affects the business.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict

US_STATES = {
    "ALABAMA", "ALASKA", "ARIZONA", "ARKANSAS", "CALIFORNIA", "COLORADO",
    "CONNECTICUT", "DELAWARE", "FLORIDA", "GEORGIA", "HAWAII", "IDAHO",
    "ILLINOIS", "INDIANA", "IOWA", "KANSAS", "KENTUCKY", "LOUISIANA",
    "MAINE", "MARYLAND", "MASSACHUSETTS", "MICHIGAN", "MINNESOTA",
    "MISSISSIPPI", "MISSOURI", "MONTANA", "NEBRASKA", "NEVADA",
    "NEW HAMPSHIRE", "NEW JERSEY", "NEW MEXICO", "NEW YORK",
    "NORTH CAROLINA", "NORTH DAKOTA", "OHIO", "OKLAHOMA", "OREGON",
    "PENNSYLVANIA", "RHODE ISLAND", "SOUTH CAROLINA", "SOUTH DAKOTA",
    "TENNESSEE", "TEXAS", "UTAH", "VERMONT", "VIRGINIA",
    "WASHINGTON", "WEST VIRGINIA", "WISCONSIN", "WYOMING",
    # Abbreviations
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

SALES_TAX_KW = [
    "SALES TAX", "STATE TAX", "USE TAX", "EXCISE TAX",
    "DEPT OF TREASURY", "DEPARTMENT OF REVENUE", "STATE REVENUE",
    "TAX REMITTANCE", "TAX PAYMENT STATE",
]

PAYROLL_TAX_KW = [
    "PAYROLL TAX", "STATE UNEMPLOYMENT", "SUI PAYMENT",
    "UNEMPLOYMENT INSURANCE", "WORKERS COMP PAYMENT",
    "STATE INCOME TAX WITHHOLDING",
]

FOREIGN_KW = [
    "INTERNATIONAL WIRE", "SWIFT", "FOREIGN WIRE", "SEPA",
    "FX PAYMENT", "FOREIGN TRANSFER", "OVERSEAS PAYMENT",
    "WIRE TO CANADA", "WIRE TO MEXICO", "WIRE TO UK",
    "WIRE TO CHINA", "WIRE TO INDIA", "WIRE TO GERMANY",
]

LOCAL_GOV_KW = [
    "COUNTY GRANT", "MUNICIPAL GRANT", "CITY GRANT",
    "LOCAL GOVERNMENT", "TOWNSHIP", "COUNTY PAYMENT",
    "COMMUNITY DEVELOPMENT", "CDBG", "LOCAL ECONOMIC",
]


def _extract_state_from_merchant(merchant: str) -> str | None:
    for state in US_STATES:
        if len(state) == 2:
            if f" {state} " in merchant or merchant.endswith(f" {state}") or f"-{state}-" in merchant:
                return state
        else:
            if state in merchant:
                return state
    return None


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    sales_tax_by_state: dict[str, float] = defaultdict(float)
    payroll_tax_by_state: dict[str, float] = defaultdict(float)
    foreign_txns, local_gov_txns = [], []
    sales_tax_txns, payroll_tax_txns = [], []

    for t in transactions:
        m = t["merchant"].upper()
        amt = t["amount"]
        if amt >= 0:
            # Check for local government inflows
            if any(kw in m for kw in LOCAL_GOV_KW):
                local_gov_txns.append(t)
            continue

        if any(kw in m for kw in FOREIGN_KW):
            foreign_txns.append(t)
        if any(kw in m for kw in SALES_TAX_KW):
            sales_tax_txns.append(t)
            state = _extract_state_from_merchant(m)
            if state:
                sales_tax_by_state[state] += abs(amt)
            else:
                sales_tax_by_state["UNKNOWN"] += abs(amt)
        if any(kw in m for kw in PAYROLL_TAX_KW):
            payroll_tax_txns.append(t)
            state = _extract_state_from_merchant(m)
            if state:
                payroll_tax_by_state[state] += abs(amt)
            else:
                payroll_tax_by_state["UNKNOWN"] += abs(amt)

    # ── Multi-state sales tax nexus ───────────────────────────────────────
    if len(sales_tax_by_state) >= 5:
        total_st = sum(sales_tax_by_state.values())
        state_list = sorted(sales_tax_by_state.keys())
        results.append({
            "signal_type": "geographic_concentration",
            "severity": "amber",
            "merchant": f"MULTI-STATE TAX NEXUS: {len(sales_tax_by_state)} states",
            "amount": -total_st,
            "transaction_date": sales_tax_txns[0].get("transaction_date", "") if sales_tax_txns else "",
            "description": (
                f"Sales tax payments to {len(sales_tax_by_state)} states: "
                f"{', '.join(state_list[:8])}{'...' if len(state_list) > 8 else ''}. "
                f"Total sales tax remitted: ${total_st:,.0f}. "
                "Multi-state sales tax nexus creates ongoing compliance obligations for the buyer: "
                "(1) each state requires separate registration and filing, "
                "(2) historical under-collection exposure in nexus states becomes buyer's liability "
                "in an asset purchase without proper reps & warranties, "
                "(3) economic nexus thresholds (post-Wayfair) may trigger obligations in "
                "additional states — request a nexus study if not already done, "
                "(4) sales tax exposure represents a contingent liability — request indemnification "
                "for pre-close periods from seller in the purchase agreement."
            ),
            "library_match": "GEO_SALES_TAX_NEXUS",
            "confidence_weight": 0.70,
        })
    elif len(sales_tax_by_state) == 1 and sales_tax_txns:
        # Single state — concentration risk
        state = list(sales_tax_by_state.keys())[0]
        total_st = sum(sales_tax_by_state.values())
        results.append({
            "signal_type": "geographic_concentration",
            "severity": "green",
            "merchant": f"SINGLE-STATE OPERATIONS: {state}",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"Sales tax payments to a single state ({state}) only. "
                "The business appears to operate in a single geographic market. "
                "Single-state concentration risk: (1) local economic conditions "
                "disproportionately affect revenue, (2) regulatory changes in one state "
                "affect the entire business, (3) natural disaster or major employer "
                "departure creates outsized revenue risk. "
                "Assess whether post-acquisition geographic expansion is feasible."
            ),
            "library_match": "GEO_SINGLE_STATE",
            "confidence_weight": 0.45,
        })

    # ── Multi-state payroll (employee geographic spread) ──────────────────
    if len(payroll_tax_by_state) >= 4:
        results.append({
            "signal_type": "geographic_concentration",
            "severity": "amber",
            "merchant": f"MULTI-STATE PAYROLL: employees in {len(payroll_tax_by_state)} states",
            "amount": 0,
            "transaction_date": payroll_tax_txns[0].get("transaction_date", "") if payroll_tax_txns else "",
            "description": (
                f"Payroll tax payments to {len(payroll_tax_by_state)} states: "
                f"{', '.join(sorted(payroll_tax_by_state.keys())[:8])}. "
                "Multi-state employees create compliance obligations in each state: "
                "(1) state income tax withholding registration in each state, "
                "(2) unemployment insurance (SUI) in each state, "
                "(3) workers' compensation coverage in each state — rates vary widely, "
                "(4) some states have additional taxes (SDI, transit, local income tax). "
                "Verify that the seller is current on all state payroll tax filings "
                "and obtain a compliance certificate from each state before closing."
            ),
            "library_match": "GEO_MULTI_STATE_PAYROLL",
            "confidence_weight": 0.65,
        })

    # ── International / foreign payments ─────────────────────────────────
    if foreign_txns:
        total_foreign = sum(abs(t["amount"]) for t in foreign_txns)
        results.append({
            "signal_type": "geographic_concentration",
            "severity": "amber",
            "merchant": f"INTERNATIONAL PAYMENTS: ${total_foreign:,.0f} foreign wires",
            "amount": -total_foreign,
            "transaction_date": foreign_txns[0].get("transaction_date", ""),
            "description": (
                f"International/foreign wire transfers: ${total_foreign:,.0f} "
                f"across {len(foreign_txns)} transactions. "
                "International operations create: "
                "(1) OFAC compliance obligations — verify no restricted party payments, "
                "(2) transfer pricing documentation requirements if related-party, "
                "(3) foreign bank account reporting (FBAR) obligations, "
                "(4) currency risk if not hedged — fluctuations affect USD-equivalent costs, "
                "(5) import duty and customs costs that may change with trade policy. "
                "Request a schedule of all foreign counterparties and confirm OFAC screening."
            ),
            "library_match": "GEO_FOREIGN_PAYMENTS",
            "confidence_weight": 0.70,
        })

    # ── Local government grant dependency ─────────────────────────────────
    if local_gov_txns:
        total_gov = sum(t["amount"] for t in local_gov_txns if t["amount"] > 0)
        results.append({
            "signal_type": "geographic_concentration",
            "severity": "amber",
            "merchant": f"LOCAL GOVERNMENT REVENUE: ${total_gov:,.0f}",
            "amount": total_gov,
            "transaction_date": local_gov_txns[0].get("transaction_date", ""),
            "description": (
                f"Local government / municipal grant or contract payments: ${total_gov:,.0f}. "
                "Revenue from local government sources is concentration risk: "
                "(1) grant funding is non-recurring — budget cycles and political priorities change, "
                "(2) municipal contracts often have change-of-control provisions requiring "
                "re-approval or recompetition at acquisition, "
                "(3) community development grants (CDBG) may have job-retention conditions "
                "that create obligations for the buyer, "
                "(4) local government relationships are often personal — tied to the seller, "
                "not the business entity. Verify transferability of each government relationship."
            ),
            "library_match": "GEO_GOV_DEPENDENCY",
            "confidence_weight": 0.65,
        })

    return results
