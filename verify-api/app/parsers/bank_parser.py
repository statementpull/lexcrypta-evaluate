import csv
import io
import re

import pdfplumber

# ── Amount helpers ────────────────────────────────────────────────────────────

_AMT_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$")
_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}$")


def _parse_float(s: str) -> float:
    if not s:
        return 0.0
    try:
        return float(re.sub(r"[,$\s]", "", s.strip()))
    except ValueError:
        return 0.0


def normalise_amount(debit: str, credit: str) -> float:
    d = _parse_float(debit)
    c = _parse_float(credit)
    if d:
        return -abs(d)
    if c:
        return abs(c)
    return 0.0


# ── Wells Fargo word-position parser ─────────────────────────────────────────
#
# Wells Fargo uses a text-layout table (no borders). Column x-positions:
#   Credits  x0 < 479
#   Debits   479 <= x0 < 535
#   Balance  x0 >= 535
# Determined empirically from extract_words() on Kangaroo Two LLC statements.

_WF_CREDIT_MAX_X = 479.0
_WF_DEBIT_MAX_X  = 530.0   # balance col starts at x0=533; keep margin
_WF_AMT_MIN_X    = 390.0   # ignore amounts in description area


def _is_wf_bank(text: str) -> bool:
    return "Wells Fargo" in text or "wellsfargo" in text.lower()


def _group_lines(words: list, y_tol: float = 3.0) -> list:
    """Group extract_words() output into lines by top y-position."""
    if not words:
        return []
    lines, current = [], [words[0]]
    for w in words[1:]:
        if abs(w["top"] - current[0]["top"]) <= y_tol:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
    lines.append(sorted(current, key=lambda x: x["x0"]))
    return lines


def _classify_wf_amount(word: dict) -> str:
    """Return 'credit', 'debit', 'balance', or None."""
    if not _AMT_RE.match(word["text"]):
        return None
    x = word["x0"]
    if x < _WF_AMT_MIN_X:
        return None
    if x < _WF_CREDIT_MAX_X:
        return "credit"
    if x < _WF_DEBIT_MAX_X:
        return "debit"
    return "balance"


def _parse_wellsfargo_pdf(pdf_bytes: bytes) -> list[dict]:
    txns = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if "Transaction history" not in text:
                continue

            words = page.extract_words()
            lines = _group_lines(words)

            in_section = False
            current = None

            for line in lines:
                line_text = " ".join(w["text"] for w in line)

                if "Transaction history" in line_text:
                    in_section = True
                    continue
                if not in_section:
                    continue
                # Skip column header lines
                if re.search(r"\bCredits?\b.*\bDebits?\b|\bDate\b.*\bDescription\b", line_text):
                    continue
                # End of transaction section
                if re.match(r"^Totals?\b", line_text.strip()):
                    break

                # Check if line starts a new transaction (begins with MM/DD)
                first = line[0]["text"] if line else ""
                is_new_txn = bool(_DATE_RE.match(first))

                if is_new_txn:
                    if current:
                        txns.append(_finalise_wf_txn(current))
                    credit, debit = 0.0, 0.0
                    desc_parts = []
                    for w in line:
                        col = _classify_wf_amount(w)
                        if col == "credit":
                            credit = _parse_float(w["text"])
                        elif col == "debit":
                            debit = _parse_float(w["text"])
                        elif col is None and not _DATE_RE.match(w["text"]):
                            desc_parts.append(w["text"])
                    current = {
                        "transaction_date": first,
                        "description": " ".join(desc_parts),
                        "credit": credit,
                        "debit": debit,
                    }
                elif current is not None:
                    # Continuation line — append non-amount words to description
                    for w in line:
                        if _classify_wf_amount(w) is None:
                            current["description"] += " " + w["text"]

            if current:
                txns.append(_finalise_wf_txn(current))

    return txns


def _finalise_wf_txn(t: dict) -> dict:
    desc = re.sub(r"\s+", " ", t["description"]).strip().upper()
    amount = t["credit"] - t["debit"] if (t["credit"] or t["debit"]) else 0.0
    return {
        "transaction_date": t["transaction_date"],
        "merchant": desc,
        "amount": amount,
        "credit": t["credit"],
        "debit": t["debit"],
        "raw": {"description": desc},
    }


# ── Generic table-based PDF parser (fallback for other banks) ─────────────────

def _parse_generic_pdf(pdf_bytes: bytes) -> list[dict]:
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


# ── Public interface ───────────────────────────────────────────────────────────

def parse_bank_pdf(pdf_bytes: bytes) -> list[dict]:
    # Detect bank from text content
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            first_text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
    except Exception:
        first_text = ""

    if _is_wf_bank(first_text):
        txns = _parse_wellsfargo_pdf(pdf_bytes)
        if txns:
            return txns

    # Fallback to generic table parser
    return _parse_generic_pdf(pdf_bytes)


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
