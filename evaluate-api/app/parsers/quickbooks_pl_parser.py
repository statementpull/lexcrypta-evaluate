"""QuickBooks Profit & Loss export parser.

QB P&L CSVs are not standard tabular CSV — they use a two-column format:
  Column 1: Account name (with leading spaces for indentation)
  Column 2: Amount (positive for income, negative or bracketed for expenses)

Detection: file contains "Profit & Loss" or "Profit and Loss" or "Net Income" in header rows.
Output: pl_rows compatible with MYOB format (account, ytd, source).
"""
import csv
import io
import re


def _clean_amount(s: str) -> float:
    s = s.strip().replace(",", "").replace("$", "").replace(" ", "")
    if not s or s == "-":
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
        return -v if negative else v
    except ValueError:
        return 0.0


_QB_SIGNALS = [
    "PROFIT & LOSS", "PROFIT AND LOSS", "INCOME STATEMENT",
    "NET INCOME", "GROSS PROFIT", "TOTAL INCOME",
]


def is_quickbooks_pl(text: str) -> bool:
    top = text[:1024].upper()
    return any(sig in top for sig in _QB_SIGNALS)


def parse_quickbooks_pl(text: str) -> list[dict]:
    """Return pl_rows from a QuickBooks P&L CSV export."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    for raw_row in reader:
        if not raw_row:
            continue
        # First non-empty cell is account name; last non-empty is amount
        cells = [c.strip() for c in raw_row]
        account = cells[0].strip()
        if not account:
            continue

        # Skip header/title rows
        upper_account = account.upper()
        if any(sig in upper_account for sig in [
            "PROFIT", "LOSS", "ACCRUAL", "CASH BASIS", "JANUARY", "FEBRUARY",
            "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
            "OCTOBER", "NOVEMBER", "DECEMBER", "PREPARED BY", "PERIOD",
        ]):
            continue

        # Find the rightmost non-empty numeric cell
        amount_str = ""
        for cell in reversed(cells[1:]):
            if cell and cell not in ("-", ""):
                amount_str = cell
                break

        amount = _clean_amount(amount_str)

        rows.append({
            "account": account,
            "description": account,
            "ytd": amount,
            "this_month": amount,
            "amount": amount,
            "source": "quickbooks_pl",
        })

    return [r for r in rows if r["account"] and abs(r["ytd"]) > 0]
