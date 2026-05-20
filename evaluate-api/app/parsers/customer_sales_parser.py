"""Customer / Sales by Customer parser.

Handles MYOB Sales by Customer, QuickBooks Sales by Customer Detail/Summary,
and generic customer revenue CSV exports.

MYOB columns:  Customer, Sale Amount, Cost of Sale, Gross Profit
QB columns:    Customer, Sales, COGS, Gross Profit, Gross Margin %
Generic:       Any CSV with customer/client + sales/revenue column
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


def parse_customer_sales_csv(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    lines = list(reader)
    if not lines:
        return []

    header_row = -1
    headers = []
    for i, line in enumerate(lines[:10]):
        joined = " ".join(line).lower()
        if any(kw in joined for kw in ["customer", "client", "account"]):
            if any(kw in joined for kw in ["sale", "revenue", "amount", "income"]):
                header_row = i
                headers = [c.strip().strip('"').lower() for c in line]
                break

    if header_row < 0:
        return []

    name_col = _find_col(headers, ["customer", "client", "account name", "name"])
    sales_col = _find_col(headers, ["sale amount", "sales", "revenue", "total sales",
                                     "amount", "income", "total amount"])
    cogs_col = _find_col(headers, ["cost of sale", "cogs", "cost of goods", "direct cost"])
    gp_col = _find_col(headers, ["gross profit", "gross margin", "profit"])
    txn_col = _find_col(headers, ["transactions", "invoices", "count", "number of"])

    if name_col < 0 or sales_col < 0:
        return []

    rows = []
    for line in lines[header_row + 1:]:
        if not line or not line[name_col].strip():
            continue

        def _get(col):
            return _clean(line[col]) if 0 <= col < len(line) else 0.0

        name = line[name_col].strip().strip('"')
        if not name or name.lower() in ("total", "totals", "grand total", "other"):
            continue

        sales = _get(sales_col)
        cogs = _get(cogs_col)
        gross_profit = _get(gp_col) if gp_col >= 0 else (sales - cogs if cogs else 0)
        txn_count = int(_get(txn_col)) if txn_col >= 0 else 0

        if sales == 0:
            continue

        rows.append({
            "customer": name,
            "sales": sales,
            "cogs": cogs,
            "gross_profit": gross_profit,
            "gp_margin": (gross_profit / sales) if sales else 0,
            "transaction_count": txn_count,
        })

    # Sort by sales descending
    rows.sort(key=lambda r: r["sales"], reverse=True)
    return rows
