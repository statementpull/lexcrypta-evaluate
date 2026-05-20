import csv
import io

from app.parsers.bank_parser import _parse_float


def parse_myob_gl(csv_text: str) -> list[dict]:
    """General Ledger — each row is a posted transaction."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for row in reader:
        keys = {k.lower().strip(): v.strip() for k, v in row.items()}
        memo = keys.get("memo", keys.get("description", keys.get("details", "")))
        debit = keys.get("debit", "")
        credit = keys.get("credit", "")
        date = keys.get("date", "")
        account = keys.get("account", "")
        if not memo and not account:
            continue
        d = _parse_float(debit)
        c = _parse_float(credit)
        amount = -abs(d) if d else (abs(c) if c else 0.0)
        rows.append({
            "transaction_date": date,
            "merchant": (memo or account).upper(),
            "amount": amount,
            "account": account,
            "source": "myob_gl",
            "raw": dict(row),
        })
    return rows


def parse_myob_pl(csv_text: str) -> list[dict]:
    """P&L summary — account-level totals, no individual transactions."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for row in reader:
        keys = {k.lower().strip(): v.strip() for k, v in row.items()}
        account = keys.get("account", "")
        if not account:
            continue
        rows.append({
            "account": account,
            "this_month": _parse_float(keys.get("this month", "0")),
            "ytd": _parse_float(keys.get("ytd", "0")),
            "budget": _parse_float(keys.get("budget", "0")),
            "source": "myob_pl",
        })
    return rows


def parse_myob_aged_creditors(csv_text: str) -> list[dict]:
    """Aged Creditors — supplier balances outstanding."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for row in reader:
        keys = {k.lower().strip(): v.strip() for k, v in row.items()}
        supplier = keys.get("supplier", keys.get("name", ""))
        total = _parse_float(keys.get("total", keys.get("balance", "0")))
        if not supplier:
            continue
        rows.append({
            "supplier": supplier.upper(),
            "total_outstanding": total,
            "source": "myob_aged_creditors",
        })
    return rows
