"""Aged Debtors / Accounts Receivable Aging parser.

Handles MYOB, QuickBooks, and generic aged AR exports.
Returns one row per customer with aging buckets.

Standard MYOB columns: Customer, Current, 30 Days, 60 Days, 90 Days, 90+ Days, Total
Standard QB columns:   Customer, Current, 1-30, 31-60, 61-90, >90, Total
"""
import csv
import io
import re


def _clean(val: str) -> float:
    if not val:
        return 0.0
    val = str(val).strip()
    neg = val.startswith("(") and val.endswith(")")
    val = re.sub(r"[,$\s()%]", "", val)
    try:
        r = float(val)
        return -r if neg else r
    except (ValueError, TypeError):
        return 0.0


def _find_col(headers: list[str], keywords: list[str]) -> int:
    for i, h in enumerate(headers):
        h_lower = h.lower().strip()
        if any(kw in h_lower for kw in keywords):
            return i
    return -1


def parse_aged_debtors_csv(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    lines = list(reader)
    if not lines:
        return []

    # Find header row
    header_row = -1
    headers = []
    for i, line in enumerate(lines[:10]):
        joined = " ".join(line).lower()
        if "customer" in joined or "debtor" in joined or "client" in joined:
            header_row = i
            headers = [c.strip().strip('"').lower() for c in line]
            break

    if header_row < 0 or not headers:
        return []

    # Map column indices
    name_col = _find_col(headers, ["customer", "debtor", "client", "name"])
    current_col = _find_col(headers, ["current"])
    d30_col = _find_col(headers, ["30 day", "1-30", "30days", "30 days"])
    d60_col = _find_col(headers, ["60 day", "31-60", "60days", "60 days"])
    d90_col = _find_col(headers, ["90 day", "61-90", "90days", "90 days"])
    d90plus_col = _find_col(headers, ["90+", ">90", "over 90", "120", "older"])
    total_col = _find_col(headers, ["total", "balance", "amount owing"])

    if name_col < 0:
        return []

    rows = []
    for line in lines[header_row + 1:]:
        if not line or not line[name_col].strip():
            continue
        name = line[name_col].strip().strip('"')
        if not name or name.lower() in ("total", "totals", "grand total"):
            continue

        def _get(col):
            return _clean(line[col]) if 0 <= col < len(line) else 0.0

        current = _get(current_col)
        d30 = _get(d30_col)
        d60 = _get(d60_col)
        d90 = _get(d90_col)
        d90plus = _get(d90plus_col)
        total = _get(total_col) if total_col >= 0 else current + d30 + d60 + d90 + d90plus

        if total == 0 and current == 0:
            continue

        rows.append({
            "customer": name,
            "current": current,
            "days_30": d30,
            "days_60": d60,
            "days_90": d90,
            "days_90_plus": d90plus,
            "total": total if total != 0 else current + d30 + d60 + d90 + d90plus,
        })

    return rows
