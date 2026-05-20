"""Inventory / Stock on Hand parser.

Handles MYOB Stock on Hand, QuickBooks Inventory Valuation Summary,
and generic inventory CSV exports.

MYOB columns:  Item Number, Item Name, On Hand, Average Cost, Total Value
QB columns:    Item, Description, Qty on Hand, Avg Cost, Asset Value
Generic:       Any CSV with item/product/sku + quantity + cost/value columns
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


def parse_inventory_csv(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    lines = list(reader)
    if not lines:
        return []

    header_row = -1
    headers = []
    for i, line in enumerate(lines[:10]):
        joined = " ".join(line).lower()
        if any(kw in joined for kw in ["item", "product", "sku", "stock", "inventory"]):
            if any(kw in joined for kw in ["qty", "quantity", "on hand", "units", "value", "cost"]):
                header_row = i
                headers = [c.strip().strip('"').lower() for c in line]
                break

    if header_row < 0:
        return []

    name_col = _find_col(headers, ["item name", "description", "product name", "item", "name", "stock"])
    code_col = _find_col(headers, ["item number", "item code", "sku", "code", "number"])
    qty_col = _find_col(headers, ["on hand", "quantity", "qty", "units", "stock qty"])
    cost_col = _find_col(headers, ["average cost", "avg cost", "unit cost", "cost price", "cost"])
    value_col = _find_col(headers, ["total value", "asset value", "total cost", "value", "amount"])

    if name_col < 0 and code_col < 0:
        return []

    rows = []
    for line in lines[header_row + 1:]:
        if not line:
            continue

        def _get(col):
            return _clean(line[col]) if 0 <= col < len(line) else 0.0

        name = ""
        if name_col >= 0 and name_col < len(line):
            name = line[name_col].strip().strip('"')
        code = ""
        if code_col >= 0 and code_col < len(line):
            code = line[code_col].strip().strip('"')

        display_name = name or code
        if not display_name or display_name.lower() in ("total", "totals", "grand total"):
            continue

        qty = _get(qty_col)
        cost = _get(cost_col)
        value = _get(value_col)

        if value == 0 and cost > 0 and qty > 0:
            value = qty * cost
        if qty == 0 and value == 0:
            continue

        rows.append({
            "item": display_name,
            "code": code,
            "quantity": qty,
            "unit_cost": cost,
            "total_value": value,
        })

    return rows
