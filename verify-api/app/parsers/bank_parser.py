import csv
import io
import re

import pdfplumber


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# ── Date patterns — AU and US ─────────────────────────────────────────────────
# Matches at the START of a line (after optional whitespace):
#   AU:  02 May 2025  |  02/05/2025  |  2 May 25  |  02-05-25
#   US:  05/02/2025   |  May 2, 2025 |  05-02-2025 | 10/17 (MM/DD no year — Chase, BofA)

_DATE_RE = re.compile(
    r"""^[\s]*
    (
      \d{1,2}[\s/\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s/\-]\d{2,4}
      | (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}
      | \d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}
      | \d{1,2}/\d{1,2}(?=\s)           # MM/DD without year (Chase, BofA, Wells Fargo)
    )
    [\s,]+
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Monetary amounts: optional leading minus, optional $, digits with optional commas, mandatory cents
# e.g. -$3,200.00  |  $9,200.00  |  -9.99  |  3,650.18
_MONEY_RE = re.compile(r"(-?\$?[\d,]{1,12}\.\d{2})")

# Noise lines to skip — headers, balance carries, page breaks
_SKIP_RE = re.compile(
    r"^\s*(date|description|narration|narrative|details|transaction|debit|credit|"
    r"balance|opening|closing|brought forward|carried forward|page\s+\d|"
    r"account number|account name|bsb|statement period|available|"
    r"interest|fee[s]?\s+charged)\b",
    re.IGNORECASE,
)


def _parse_text_lines(text: str) -> list[dict]:
    """
    Parse transaction rows from raw text extracted from a bank statement PDF.
    Works for AU (Westpac, NAB, CBA, ANZ) and US (Wells Fargo, Chase, BofA) layouts.
    """
    txns = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < 8:
            continue
        if _SKIP_RE.match(stripped):
            continue

        m = _DATE_RE.match(stripped)
        if not m:
            continue

        date_str = m.group(1).strip()
        rest = stripped[m.end():].strip()

        # Find all monetary amounts in the remainder
        amounts = _MONEY_RE.findall(rest)
        if not amounts:
            continue

        # The description is the rest with all monetary values and currency symbols stripped
        desc = _MONEY_RE.sub("", rest).strip()
        # Remove trailing DR/CR markers, orphaned $ signs, and extra whitespace
        desc = re.sub(r"\s*\b(DR|CR)\b.*$", "", desc, flags=re.IGNORECASE).strip()
        desc = re.sub(r"\s*-?\$+\s*", " ", desc).strip()
        desc = re.sub(r"\s{2,}", " ", desc).strip()
        if not desc or len(desc) < 2:
            continue

        # First amount = transaction amount; last amount (if different) = running balance
        raw_amount = amounts[0].replace(",", "").replace("$", "")
        try:
            amount = float(raw_amount)
        except ValueError:
            continue

        # DR suffix means debit (outflow) → negative
        is_debit = bool(re.search(r"\bDR\b", rest, re.IGNORECASE))
        if is_debit and amount > 0:
            amount = -amount

        txns.append({
            "transaction_date": date_str,
            "merchant": desc.upper(),
            "amount": amount,
            "raw": {"line": stripped},
        })

    return txns


# ── CSV parser ─────────────────────────────────────────────────────────────────

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


# ── PDF parser ─────────────────────────────────────────────────────────────────

def parse_bank_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Extract transactions from a bank statement PDF.

    Strategy (in order):
    1. pdfplumber extract_table() — works for PDFs with embedded table objects.
    2. Text-line parser — fallback for text-based layouts (Westpac, Wells Fargo, etc.)
       that don't use table objects. Reads raw text and matches date + description + amount.
    """
    txns = []
    all_page_text: list[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            # ── Pass 1: table extraction ──
            table = page.extract_table()
            if table and len(table) > 1:
                headers = [str(h).lower().strip() if h else "" for h in table[0]]
                for row in table[1:]:
                    if not row or all(not c for c in row):
                        continue
                    row_dict = dict(zip(headers, [str(c or "").strip() for c in row]))
                    date = row_dict.get("date", row_dict.get("transaction date", ""))
                    desc = row_dict.get(
                        "description",
                        row_dict.get("narrative", row_dict.get("details", "")),
                    )
                    debit = row_dict.get(
                        "debit",
                        row_dict.get("withdrawals", row_dict.get("amount", "")),
                    )
                    credit = row_dict.get("credit", row_dict.get("deposits", ""))
                    if not desc:
                        continue
                    txns.append({
                        "transaction_date": date,
                        "merchant": desc.upper(),
                        "amount": normalise_amount(debit, credit),
                        "raw": row_dict,
                    })

            # ── Always collect page text for fallback ──
            page_text = page.extract_text()
            if page_text:
                all_page_text.append(page_text)

    # ── Pass 2: text-line fallback (if table extraction found nothing) ──
    if not txns and all_page_text:
        full_text = "\n".join(all_page_text)
        txns = _parse_text_lines(full_text)

    return txns
