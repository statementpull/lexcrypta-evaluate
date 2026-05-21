import csv
import io
import re

import pdfplumber


def normalise_amount(debit: str, credit: str) -> float:
    d = _parse_float(debit)
    c = _parse_float(credit)
    if d:
        return -abs(d)
    if c:
        return abs(c)
    return 0.0


def _parse_float(s: str) -> float:
    if not s:
        return 0.0
    cleaned = re.sub(r"[,$\s]", "", s.strip())
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_bank_csv_text(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    txns = []
    for row in reader:
        keys = {k.lower().strip(): v for k, v in row.items()}
        date = keys.get("date", "")
        desc = keys.get("description", keys.get("narrative", keys.get("memo", "")))
        debit = keys.get("debit", keys.get("withdrawals", ""))
        credit = keys.get("credit", keys.get("deposits", ""))
        amount = normalise_amount(debit, credit)
        if not (desc or "").strip():
            continue
        txns.append({
            "transaction_date": date.strip(),
            "merchant": desc.strip().upper(),
            "amount": amount,
            "raw": dict(row),
        })
    return txns


def parse_bank_pdf(pdf_bytes: bytes) -> list[dict]:
    txns = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            headers = [str(h).lower().strip() if h else "" for h in table[0]]
            for row in table[1:]:
                if not row or all(not c for c in row):
                    continue
                row_dict = dict(zip(headers, [str(c or "").strip() for c in row]))
                date = row_dict.get("date", row_dict.get("transaction date", ""))
                desc = row_dict.get("description", row_dict.get("narrative", row_dict.get("details", "")))
                debit = row_dict.get("debit", row_dict.get("withdrawals", row_dict.get("amount", "")))
                credit = row_dict.get("credit", row_dict.get("deposits", ""))
                if not desc:
                    continue
                txns.append({
                    "transaction_date": date,
                    "merchant": desc.upper(),
                    "amount": normalise_amount(debit, credit),
                    "raw": row_dict,
                })
    return txns
