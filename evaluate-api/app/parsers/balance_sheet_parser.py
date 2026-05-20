"""Balance Sheet parser — MYOB, QuickBooks, and generic CSV formats.

Extracts structured balance sheet rows from accounting software exports.
Returns list of dicts with keys: account, category, amount.

Categories detected: current_asset, fixed_asset, other_asset,
current_liability, long_term_liability, equity.
"""
import re
import csv
import io

ASSET_KW = ["TOTAL ASSETS", "CURRENT ASSETS", "FIXED ASSETS", "NON-CURRENT ASSETS",
            "TOTAL FIXED", "PROPERTY PLANT", "OTHER ASSETS"]
CURRENT_ASSET_KW = ["CASH", "ACCOUNTS RECEIVABLE", "RECEIVABLE", "INVENTORY",
                    "PREPAID", "CURRENT ASSETS", "STOCK ON HAND", "DEBTORS"]
FIXED_ASSET_KW = ["PROPERTY", "PLANT", "EQUIPMENT", "VEHICLE", "BUILDING",
                  "FIXED ASSET", "LEASEHOLD", "MACHINERY", "FURNITURE"]
CURRENT_LIAB_KW = ["ACCOUNTS PAYABLE", "CREDITORS", "CURRENT LIABIL", "SHORT-TERM",
                   "PAYROLL LIAB", "ACCRUED LIAB", "OVERDRAFT", "LINE OF CREDIT",
                   "CREDIT CARD", "CURRENT PORTION", "SALES TAX PAYABLE"]
LONGTERM_LIAB_KW = ["LONG-TERM", "LONG TERM", "MORTGAGE", "TERM LOAN", "SBA LOAN",
                    "NOTES PAYABLE", "DEFERRED TAX", "NON-CURRENT LIAB"]
EQUITY_KW = ["EQUITY", "RETAINED EARNINGS", "RETAINED EARNING", "OWNER", "CAPITAL",
             "COMMON STOCK", "SHAREHOLDER", "MEMBER EQUITY", "NET ASSETS"]


def _clean_amount(val: str) -> float:
    if not val:
        return 0.0
    val = str(val).strip()
    negative = val.startswith("(") and val.endswith(")")
    val = re.sub(r"[,$\s()%]", "", val)
    try:
        result = float(val)
        return -result if negative else result
    except (ValueError, TypeError):
        return 0.0


def _categorise(account: str) -> str:
    acc = account.upper()
    if any(kw in acc for kw in CURRENT_ASSET_KW):
        return "current_asset"
    if any(kw in acc for kw in FIXED_ASSET_KW):
        return "fixed_asset"
    if any(kw in acc for kw in CURRENT_LIAB_KW):
        return "current_liability"
    if any(kw in acc for kw in LONGTERM_LIAB_KW):
        return "long_term_liability"
    if any(kw in acc for kw in EQUITY_KW):
        return "equity"
    if any(kw in acc for kw in ASSET_KW):
        return "other_asset"
    return "other"


def parse_balance_sheet_csv(text: str) -> list[dict]:
    rows = []
    reader = csv.reader(io.StringIO(text))
    lines = list(reader)

    # Find the amount column — look for a header row with Balance/Amount/Total
    amount_col = 1
    for i, line in enumerate(lines[:10]):
        cols = [c.strip().lower() for c in line]
        for j, c in enumerate(cols):
            if c in ("balance", "amount", "total", "ytd", "this year"):
                amount_col = j
                break

    for line in lines:
        if not line or not line[0].strip():
            continue
        account = line[0].strip().strip('"')
        if not account or account.lower() in ("account", "description", "name"):
            continue
        # Skip section headers that have no amount
        amount_str = line[amount_col].strip() if len(line) > amount_col else ""
        amount = _clean_amount(amount_str)
        if amount == 0 and not any(kw in account.upper() for kw in
                                   ["TOTAL", "NET", "EQUITY", "ASSETS", "LIAB"]):
            continue
        rows.append({
            "account": account,
            "category": _categorise(account),
            "amount": amount,
        })

    return rows


def parse_balance_sheet_text(text: str) -> list[dict]:
    """Handle both CSV and tab-delimited exports."""
    if "," in text[:500]:
        return parse_balance_sheet_csv(text)
    # Tab-delimited fallback
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        account = parts[0].strip()
        if not account:
            continue
        amount = _clean_amount(parts[-1].strip())
        if amount == 0:
            continue
        rows.append({
            "account": account,
            "category": _categorise(account),
            "amount": amount,
        })
    return rows
