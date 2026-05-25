"""
P2P Transfer Signal Module
==========================
Detects Cash App, Venmo, Zelle, and PayPal payments to named individuals.
Recurring or large P2P transfers from a business account may indicate:
  - Off-books payroll (unreported employees)
  - Related-party transfers to undisclosed associates
  - Personal asset concealment routed through the business

Signal type : p2p_transfer
Severity
  RED   — ≥ 5 payments to same recipient  OR  total ≥ $5,000 to recipient
  AMBER — 2–4 payments to same recipient  OR  total $500–$4,999 to recipient
  GREEN — single low-value payment (logged for completeness)
"""

import re
from collections import defaultdict

# ── Platform detection patterns ────────────────────────────────────────────────
# Each entry: (compiled regex that captures the recipient in group 1, platform label)
# Patterns use re.search so they match anywhere in the merchant string (handles
# Chase "PAYMENT SENT 10/10 CASH APP*..." prefixes cleanly).

_P2P_PATTERNS: list[tuple] = [
    # Cash App:  CASH APP*FIRST LAST [CITY ST] [CARD XXXX]
    (re.compile(r"CASH\s*APP\*(.+)", re.IGNORECASE), "Cash App"),
    # Square Cash (legacy Cash App branding)
    (re.compile(r"SQUARE\s+CASH\*(.+)", re.IGNORECASE), "Cash App"),
    # Venmo:  VENMO*FIRST LAST  or  VENMO FIRST LAST
    (re.compile(r"VENMO[\*\s]+(.+)", re.IGNORECASE), "Venmo"),
    # Zelle outgoing:  ZELLE PAYMENT TO NAME  |  ZELLE TO NAME  |  ZELLE SENT TO NAME
    (re.compile(r"ZELLE\s+(?:PAYMENT\s+)?(?:SENT\s+)?TO\s+(.+)", re.IGNORECASE), "Zelle"),
    # PayPal transfer:  PAYPAL TRANSFER TO NAME  |  PAYPAL PAYMENT TO NAME  |  PAYPAL INST XFER NAME
    (re.compile(
        r"PAYPAL\s+(?:TRANSFER\s+TO|PAYMENT\s+TO|INST\s+XFER|TRANSFER|PAYMENT)\s+(.+)",
        re.IGNORECASE,
    ), "PayPal"),
]

# ── Severity thresholds ────────────────────────────────────────────────────────
_RED_COUNT    = 5       # ≥ 5 payments to same recipient
_RED_AMOUNT   = 5_000   # or total ≥ $5,000 to same recipient
_AMBER_COUNT  = 2       # ≥ 2 payments to same recipient
_AMBER_AMOUNT = 500     # or total ≥ $500 to same recipient


def _clean_recipient(raw: str) -> str:
    """
    Normalise a raw recipient string into a consistent name for grouping.

    Strips:
      • Trailing "CARD XXXX" and everything after  (Chase: NAME CITY ST CARD 7338)
      • 2-letter US state code at end (e.g. "CA", "TX")
      • Preceding city word when state was found (e.g. "OAKLAND" before "CA")
      • Asterisks and pipe characters
      • Excess whitespace

    Title-cases the result.

    Examples:
      "SAHARA HALL OAKLAND CA CARD 7338"  →  "Sahara Hall"
      "OLIVIA WILLIAM OAKLAND CA CARD 7338" → "Olivia William"
      "NEVAE H GREEN"                      → "Nevae H Green"  (no state found)
      "ALANNA GAILLIA"                     → "Alanna Gaillia"
    """
    s = raw.strip()
    # Remove "CARD XXXX" suffix and everything after
    s = re.sub(r"\s+CARD\s+\d{4}.*$", "", s, flags=re.IGNORECASE)
    # Remove standalone asterisks / pipe chars
    s = re.sub(r"[*|]+", "", s)
    # Collapse whitespace
    s = re.sub(r"\s{2,}", " ", s).strip()

    words = s.split()

    # Strip trailing state code (exactly 2 uppercase letters, e.g. "CA", "TX")
    # All words are already uppercase because merchant is upper()-ed by the parser.
    if words and len(words[-1]) == 2 and words[-1].isalpha():
        words = words[:-1]  # drop state
        # Also drop the preceding city word if the name still has ≥ 2 words
        if len(words) > 2:
            words = words[:-1]  # drop city

    # Cap at 4 words — covers First Last, First M Last, First Middle Last
    return " ".join(words[:4]).title() if words else ""


def _extract_p2p(txn: dict) -> tuple:
    """
    Return (platform, recipient) if this transaction is an outgoing P2P payment,
    or (None, None) otherwise.

    Only negative amounts are considered (outgoing).  Incoming Zelle/Venmo
    payments are legitimate business receipts and should not be flagged.
    """
    if txn.get("amount", 0) >= 0:
        return None, None

    merchant = txn.get("merchant", txn.get("description", ""))
    for pattern, platform in _P2P_PATTERNS:
        m = pattern.search(merchant)
        if m:
            recipient = _clean_recipient(m.group(1))
            if recipient and len(recipient) >= 2:
                return platform, recipient

    return None, None


def run(transactions: list) -> list:
    """
    Detect P2P transfer patterns across the transaction list.

    Groups payments by (platform, recipient) and emits one signal per group.
    Signals are sorted by total amount descending so the most material flows
    surface first in the frontend panel.

    Interface: run(transactions) — no loader, no pl_rows.
    """
    groups: dict[tuple, list] = defaultdict(list)

    for txn in transactions:
        platform, recipient = _extract_p2p(txn)
        if platform and recipient:
            groups[(platform, recipient)].append(txn)

    signals = []
    for (platform, recipient), txns in groups.items():
        count  = len(txns)
        total  = round(sum(abs(t.get("amount", 0)) for t in txns), 2)
        dates  = sorted({t.get("transaction_date", "") for t in txns if t.get("transaction_date")})

        # Severity
        if count >= _RED_COUNT or total >= _RED_AMOUNT:
            severity = "red"
            conf_weight = 0.8
        elif count >= _AMBER_COUNT or total >= _AMBER_AMOUNT:
            severity = "amber"
            conf_weight = 0.6
        else:
            severity = "green"
            conf_weight = 0.4

        signals.append({
            "signal_type":        "p2p_transfer",
            "severity":           severity,
            "merchant":           recipient,
            "description": (
                f"{platform} — {count} payment{'s' if count != 1 else ''} "
                f"to {recipient} (${total:,.2f} total)"
            ),
            "amount":             total,
            "count":              count,
            "platform":           platform,
            "confidence_weight":  conf_weight,
            "dates":              dates[:5],   # first 5 dates for audit trail
        })

    # Most material flows first
    signals.sort(key=lambda s: s["amount"], reverse=True)
    return signals
