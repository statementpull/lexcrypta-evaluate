import csv
import io
from datetime import datetime, date
from typing import Optional

from .normalizer_util import normalize_description


def _parse_date(s: str) -> Optional[date]:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_quickbooks_csv(text: str) -> list[dict]:
    if not text or not text.strip():
        return []

    reader = csv.DictReader(io.StringIO(text))
    rows = []

    for raw in reader:
        date_val = raw.get("Date", "").strip()
        num = raw.get("Num", "").strip()
        name = raw.get("Name", "").strip()
        memo = (raw.get("Memo/Description") or raw.get("Memo") or raw.get("Description") or "").strip()
        amount_str = (raw.get("Amount") or raw.get("Debit") or "0").strip().replace(",", "")

        if not date_val or not amount_str:
            continue

        txn_date = _parse_date(date_val)
        if txn_date is None:
            continue

        try:
            amount = float(amount_str)
        except ValueError:
            continue

        direction = "credit" if amount >= 0 else "debit"
        description = memo or name or raw.get("Transaction Type", "")
        desc_norm = normalize_description(description)

        rows.append({
            "transaction_date": txn_date,
            "description": description,
            "description_norm": desc_norm,
            "reference": num,
            "amount": abs(amount),
            "direction": direction,
            "report_type": "quickbooks_gl",
            "source": "accounting",
        })

    return rows
