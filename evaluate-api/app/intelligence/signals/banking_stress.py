"""Signal 22: Banking Stress & Cash Position Health.

Detects cash stress indicators hidden in bank transaction data that are
invisible in P&L financials. A business can appear profitable on paper
while experiencing chronic cash stress — these signals reveal the gap.

Patterns detected:
  NSF / Insufficient Funds: Non-sufficient fund fees signal the business
    regularly runs its account to zero — a severe cash management failure.
  Overdraft fees: Repeated overdraft usage indicates structural cash shortage.
  Returned ACH / bounced payments: Failed payments to vendors/suppliers
    signal inability to meet obligations — may have triggered supplier issues.
  Line of credit cycling: Constant draws and repayments on a LOC indicate
    the business relies on the credit line to fund operations — not earnings.
  Escalating bank fees: Increasing fee load signals growing service usage
    or penalty accumulation.
  Wire return fees: Returned incoming wires may indicate customer payment
    issues or fraud.

Why it matters for acquisitions:
  - Chronic cash stress means working capital requirements are higher than stated
  - NSF history may have damaged supplier relationships
  - LOC dependency means the buyer needs to assume or replace the credit facility
  - Banks may call LOCs or reduce limits on ownership change

Sources:
- FDIC Bank Examination Manual — liquidity stress indicators
- SBA SOP 50 10 7 — LOC treatment in acquisition lending
- FinCEN guidance on returned items as AML indicator

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict


NSF_KEYWORDS = [
    "NSF FEE", "INSUFFICIENT FUNDS", "NSF CHARGE", "RETURNED ITEM FEE",
    "RETURNED CHECK FEE", "NON-SUFFICIENT", "NSF", "OVERDRAFT FEE",
    "OVERDRAFT CHARGE", "OD FEE", "OVERDRAFT PROTECTION FEE",
    "EXTENDED OVERDRAFT", "CONTINUOUS OVERDRAFT",
]

RETURNED_PAYMENT_KEYWORDS = [
    "RETURNED ACH", "ACH RETURN", "RETURN ITEM", "RETURNED PAYMENT",
    "RETURNED CHECK", "BOUNCED CHECK", "RETURNED DEBIT", "RETURN FEE",
    "ACH REVERSAL", "REVERSAL FEE", "PAYMENT RETURNED", "CHECK RETURNED",
    "DEBIT RETURNED", "WIRE RETURN", "RETURNED WIRE",
]

LINE_OF_CREDIT_KEYWORDS = [
    "LINE OF CREDIT", "LOC DRAW", "LOC ADVANCE", "CREDIT LINE DRAW",
    "REVOLVING CREDIT", "LOC PAYMENT", "LINE PAYMENT", "CREDIT LINE PAYMENT",
    "BUSINESS LINE", "FLEX LINE", "HELOC",
]

BANK_FEE_KEYWORDS = [
    "MONTHLY SERVICE FEE", "ACCOUNT FEE", "MAINTENANCE FEE", "WIRE FEE",
    "TRANSFER FEE", "STOP PAYMENT FEE", "CASH HANDLING FEE",
    "ANALYSIS FEE", "TREASURY FEE", "SWEEP FEE",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []

    nsf_txns = []
    returned_txns = []
    loc_draws = []
    loc_payments = []
    bank_fee_txns = []

    for t in transactions:
        merchant = t["merchant"].upper()
        amt = t["amount"]

        if any(kw in merchant for kw in NSF_KEYWORDS):
            nsf_txns.append(t)
        if any(kw in merchant for kw in RETURNED_PAYMENT_KEYWORDS):
            returned_txns.append(t)
        if any(kw in merchant for kw in LINE_OF_CREDIT_KEYWORDS):
            if amt > 0:
                loc_draws.append(t)
            else:
                loc_payments.append(t)
        if any(kw in merchant for kw in BANK_FEE_KEYWORDS) and amt < 0:
            bank_fee_txns.append(t)

    # ── 1. NSF / Overdraft fees ───────────────────────────────────────────────
    if nsf_txns:
        total_nsf = sum(abs(t["amount"]) for t in nsf_txns)
        sev = "red" if len(nsf_txns) >= 4 else "amber"
        results.append({
            "signal_type": "banking_stress",
            "severity": sev,
            "merchant": f"NSF / OVERDRAFT FEES: {len(nsf_txns)} occurrences",
            "amount": -total_nsf,
            "transaction_date": nsf_txns[0].get("transaction_date", ""),
            "description": (
                f"Non-sufficient funds / overdraft fees: {len(nsf_txns)} occurrences "
                f"totalling ${total_nsf:,.0f} in charges. "
                f"{'CRITICAL: ' if len(nsf_txns) >= 4 else ''}"
                "NSF fees indicate the business regularly depletes its bank account to zero — "
                "a severe cash management failure that is invisible in P&L financials. "
                "Implications: (1) working capital requirements are higher than stated, "
                "(2) suppliers may have been notified of payment failures, damaging relationships, "
                "(3) the stated cash balance in financials may be overstated at period end. "
                "Banks may also reduce credit facilities or add conditions on ownership change "
                "if NSF history is present. Request full NSF history and current overdraft limit."
            ),
            "library_match": "BANKING_NSF",
            "confidence_weight": 0.85 if len(nsf_txns) >= 4 else 0.70,
        })

    # ── 2. Returned ACH / bounced payments ───────────────────────────────────
    if returned_txns:
        total_returned = sum(abs(t["amount"]) for t in returned_txns)
        results.append({
            "signal_type": "banking_stress",
            "severity": "amber",
            "merchant": f"RETURNED PAYMENTS: {len(returned_txns)} returned ACH / checks",
            "amount": -total_returned,
            "transaction_date": returned_txns[0].get("transaction_date", ""),
            "description": (
                f"Returned / bounced payment activity: {len(returned_txns)} returned transactions "
                f"totalling ${total_returned:,.0f}. "
                "Returned ACH debits indicate vendor payments failed — suppliers may have "
                "placed the business on prepayment terms or COD, changing the working capital "
                "profile. Returned ACH credits (incoming) may indicate customer payment failures. "
                "Verify: (1) which vendors were affected and current payment terms, "
                "(2) whether any supplier contracts were terminated or modified, "
                "(3) whether returned incoming ACH relates to customer payment issues."
            ),
            "library_match": "BANKING_RETURNED_PAYMENTS",
            "confidence_weight": 0.65,
        })

    # ── 3. Line of credit cycling ─────────────────────────────────────────────
    if loc_draws and loc_payments:
        total_draws = sum(t["amount"] for t in loc_draws)
        total_payments = sum(abs(t["amount"]) for t in loc_payments)
        loc_net = total_draws - total_payments
        cycle_count = min(len(loc_draws), len(loc_payments))
        sev = "red" if cycle_count >= 6 else "amber"
        results.append({
            "signal_type": "banking_stress",
            "severity": sev,
            "merchant": f"LINE OF CREDIT CYCLING: {len(loc_draws)} draws, {len(loc_payments)} payments",
            "amount": loc_net,
            "transaction_date": loc_draws[0].get("transaction_date", ""),
            "description": (
                f"Line of credit activity: {len(loc_draws)} draws (${total_draws:,.0f}) and "
                f"{len(loc_payments)} repayments (${total_payments:,.0f}). Net: ${loc_net:,.0f}. "
                f"{'HIGH-FREQUENCY ' if cycle_count >= 6 else ''}LOC cycling indicates the business "
                "relies on its credit facility to fund routine operations — earnings alone "
                "are insufficient to cover operating cash needs. "
                "Acquisition implications: (1) the LOC balance at closing must be settled or assumed, "
                "(2) lenders may call the LOC on ownership change — check facility agreement, "
                "(3) SBA acquisition loans typically require subordination or payoff of existing LOCs, "
                "(4) ongoing LOC availability is critical to the business's working capital model. "
                "Obtain full LOC agreement and current outstanding balance."
            ),
            "library_match": "BANKING_LOC_CYCLING",
            "confidence_weight": 0.75 if cycle_count >= 6 else 0.60,
        })
    elif loc_draws:
        total_draws = sum(t["amount"] for t in loc_draws)
        results.append({
            "signal_type": "banking_stress",
            "severity": "amber",
            "merchant": f"LINE OF CREDIT DRAWS: {len(loc_draws)} draws (${total_draws:,.0f})",
            "amount": total_draws,
            "transaction_date": loc_draws[0].get("transaction_date", ""),
            "description": (
                f"Line of credit draw activity: {len(loc_draws)} draws totalling ${total_draws:,.0f}. "
                "Verify current LOC balance, facility limit, and maturity date. "
                "Confirm the LOC is transferable to new ownership without triggering a payoff clause."
            ),
            "library_match": "BANKING_LOC",
            "confidence_weight": 0.55,
        })

    # ── 4. Bank fee escalation ────────────────────────────────────────────────
    if len(bank_fee_txns) >= 3:
        total_fees = sum(abs(t["amount"]) for t in bank_fee_txns)
        if total_fees > 500:
            results.append({
                "signal_type": "banking_stress",
                "severity": "amber",
                "merchant": f"BANK FEES: ${total_fees:,.0f} in account/service fees",
                "amount": -total_fees,
                "transaction_date": bank_fee_txns[0].get("transaction_date", ""),
                "description": (
                    f"Bank service and account fees: {len(bank_fee_txns)} charges totalling "
                    f"${total_fees:,.0f}. Elevated bank fees may indicate: "
                    "(1) the business is on a high-fee account tier (cash-heavy businesses), "
                    "(2) analysis fees on complex treasury services, "
                    "(3) penalty charges associated with covenant breaches or minimum balance failures. "
                    "Verify fee structure and whether fees can be reduced post-acquisition "
                    "by consolidating banking relationships."
                ),
                "library_match": None,
                "confidence_weight": 0.45,
            })

    return results
