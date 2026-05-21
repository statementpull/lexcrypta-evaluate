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

_MERCHANT_NOISE_RE = re.compile(
    r"\s+#\d+$"      # trailing #4912
    r"|\s+\*\s*\d+$" # trailing * 44212
    r"|\s+\d{6,}$"   # trailing long reference codes
, re.IGNORECASE)


def _normalise_merchant(raw: str) -> str:
    """Strip trailing reference IDs from merchant descriptions."""
    s = re.sub(r"\s+", " ", raw).strip().upper()
    s = _MERCHANT_NOISE_RE.sub("", s).strip()
    return s


_WF_CREDIT_MAX_X = 479.0
_WF_DEBIT_MAX_X  = 530.0   # balance col starts at x0=533; keep margin
_WF_AMT_MIN_X    = 390.0   # ignore amounts in description area

# Lines containing these phrases are bank headers/footers or boilerplate — not transactions
_WF_SKIP_RE = re.compile(
    r"Wells Fargo Bank|Member FDIC|Ending balance|Beginning balance"
    r"|Account number|Page \d+ of \d+"
    r"|IN CASE OF ERRORS|ELECTRONIC FUNDS TRANSFER|CALL US AT 1-8"
    r"|\*END\*|\*START\*|DISCLOSURE MESSAGE|DAILY ENDING"
    r"|WE MUST HEAR FROM YOU|PROVISIONAL CREDIT",
    re.IGNORECASE,
)

# Lines that are clearly balance summary rows (dates and dollar amounts, no description)
_WF_BALANCE_ROW_RE = re.compile(
    r"^\s*[\d,]+\.\d{2}\s+\d{1,2}/\d{1,2}\s+[\d,]+\.\d{2}"
)


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
                    if _WF_SKIP_RE.search(line_text):
                        continue
                    if _WF_BALANCE_ROW_RE.match(line_text):
                        continue
                    # Continuation line — append non-amount words to description
                    for w in line:
                        if _classify_wf_amount(w) is None:
                            current["description"] += " " + w["text"]

            if current:
                txns.append(_finalise_wf_txn(current))

    return txns


def _finalise_wf_txn(t: dict) -> dict:
    desc = _normalise_merchant(t["description"])
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
                    "merchant": _normalise_merchant(desc),
                    "amount": normalise_amount(debit, credit),
                    "raw": row_dict,
                })
    return txns


# ── PDF metadata integrity check ──────────────────────────────────────────────

def _extract_pdf_metadata(pdf_bytes: bytes) -> dict | None:
    """Return a metadata signal record if the PDF shows integrity concerns."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            meta = pdf.metadata or {}
    except Exception:
        return None

    creator   = str(meta.get("Creator",  meta.get("/Creator",  ""))).strip()
    producer  = str(meta.get("Producer", meta.get("/Producer", ""))).strip()
    created   = str(meta.get("CreationDate", meta.get("/CreationDate", ""))).strip()
    modified  = str(meta.get("ModDate",  meta.get("/ModDate",  ""))).strip()

    # Flag if document was re-saved by a tool other than the issuing bank
    resave_tools = ["adobe acrobat", "preview", "microsoft word", "libreoffice",
                    "foxit", "nitro", "pdfelement", "cutepdf", "ghostscript"]
    resaved = any(t in (creator + producer).lower() for t in resave_tools)
    modified_after_creation = (modified and created and modified != created)

    if resaved or modified_after_creation:
        note = []
        if resaved:
            note.append(f"Re-saved by third-party tool: {producer or creator}")
        if modified_after_creation:
            note.append("Modification date differs from creation date")
        return {
            "transaction_date": "",
            "merchant": "PDF METADATA INTEGRITY FLAG",
            "amount": 0.0,
            "signal_type": "document_integrity",
            "severity": "red",
            "meta": {
                "creator": creator,
                "producer": producer,
                "created": created,
                "modified": modified,
                "note": " · ".join(note),
            },
            "raw": {},
        }
    return None


# ── Chase bank parser ─────────────────────────────────────────────────────────
#
# Chase Business/Personal statements use section markers: *start*<name> / *end*
# Credits live in "deposits and additions", debits in "atm & debit card withdrawals"
# and "electronic withdrawals". Amounts trail the first description line.

_CHASE_AMOUNT_RE = re.compile(r"\$?([\d,]+\.\d{2})\s*$")
_CHASE_DATE_RE   = re.compile(r"^(\d{1,2}/\d{1,2})\s+(.*)")
_CHASE_SECTION_START_RE = re.compile(r"^\*start\*(.+)", re.IGNORECASE)
_CHASE_SECTION_END_RE   = re.compile(r"^\*end\*", re.IGNORECASE)

# Chase section name fragments that contain credits
_CHASE_CREDIT_SECTIONS = {"deposits and additions", "deposits", "other credits"}
# Chase section name fragments that contain debits
_CHASE_DEBIT_SECTIONS  = {"atm", "debit withdrawal", "debit card", "electronic withdrawal",
                           "electronic payment", "checks paid", "fees", "service fee"}

_CHASE_SKIP_RE = re.compile(
    r"^Total|^Account Number|^JPMorgan Chase|^Member FDIC"
    r"|^\(continued\)|^Beginning Balance|^Ending Balance"
    r"|^Date\s+Description|^ACCOUNT ACTIVITY",
    re.IGNORECASE,
)

# Multi-line ACH continuation fields — strip these
_CHASE_ACH_CONT_RE = re.compile(
    r"^\s*(Orig CO Name:|Orig ID:|Descr:|CO Entry Class:|Trn:|Trace#|Individual ID:|Individual Name:|PPD ID:)",
    re.IGNORECASE,
)

# Extract originating company name from ACH header lines
_CHASE_ORIG_CO_RE = re.compile(r"Orig CO Name:\s*([^O]+?)(?:\s+Orig ID:|$)", re.IGNORECASE)

# Card purchase pattern: "Card Purchase MM/DD <merchant> Card XXXX"
_CHASE_CARD_RE = re.compile(
    r"^Card (?:Purchase|Payment)\s+\d{1,2}/\d{1,2}\s+(.*?)\s+Card\s+\d{4}",
    re.IGNORECASE,
)

# Zelle pattern
_CHASE_ZELLE_RE = re.compile(r"^Zelle (?:Payment (?:From|To)|Transfer(?:\s+From|\s+To)?)\s+(.*?)\s+(?:on\s+)?\d{1,2}/\d{1,2}", re.IGNORECASE)


def _is_chase_bank(text: str) -> bool:
    return "JPMorgan Chase" in text or "Chase.com" in text or "Chase Bank" in text


def _clean_chase_desc(raw: str) -> str:
    """Extract a clean merchant name from a Chase transaction description."""
    s = raw.strip()

    # Card purchase — extract merchant between date and 'Card XXXX'
    m = _CHASE_CARD_RE.match(s)
    if m:
        return _normalise_merchant(m.group(1))

    # Zelle
    m = _CHASE_ZELLE_RE.match(s)
    if m:
        return _normalise_merchant("ZELLE " + m.group(1))

    # ACH with Orig CO Name embedded
    m = _CHASE_ORIG_CO_RE.search(s)
    if m:
        return _normalise_merchant(m.group(1).strip())

    return _normalise_merchant(s)


def _parse_chase_pdf(pdf_bytes: bytes) -> list[dict]:
    txns = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )

    lines = full_text.splitlines()
    current_section = ""
    is_credit_section = False
    is_debit_section  = False
    skip_section      = False
    current           = None

    def _flush(t):
        if not t:
            return
        desc  = _clean_chase_desc(t["description"])
        amt   = t["amount"]
        txns.append({
            "transaction_date": t["date"],
            "merchant": desc,
            "amount": amt,
            "credit": amt if amt > 0 else 0.0,
            "debit":  abs(amt) if amt < 0 else 0.0,
            "raw": {"description": desc},
        })

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Section start
        m = _CHASE_SECTION_START_RE.match(stripped)
        if m:
            _flush(current)
            current = None
            current_section = m.group(1).lower()
            skip_section = (
                "daily ending balance" in current_section
                or "summary" in current_section
                or "message" in current_section
            )
            is_credit_section = not skip_section and any(
                cs in current_section for cs in _CHASE_CREDIT_SECTIONS
            )
            is_debit_section = not skip_section and any(
                ds in current_section for ds in _CHASE_DEBIT_SECTIONS
            )
            continue

        # Section end
        if _CHASE_SECTION_END_RE.match(stripped):
            _flush(current)
            current = None
            current_section = ""
            is_credit_section = is_debit_section = skip_section = False
            continue

        if skip_section or (not is_credit_section and not is_debit_section):
            continue

        if _CHASE_SKIP_RE.match(stripped):
            continue

        # ACH continuation line — append to current description, skip amount parsing
        if current and _CHASE_ACH_CONT_RE.match(stripped):
            # Only keep Orig CO Name content; ignore the rest
            mc = _CHASE_ORIG_CO_RE.search(stripped)
            if mc:
                current["description"] = mc.group(1).strip()
            continue

        # New transaction: starts with MM/DD
        m = _CHASE_DATE_RE.match(stripped)
        if m:
            _flush(current)
            date = m.group(1)
            rest = m.group(2).strip()

            # Extract trailing amount
            am = _CHASE_AMOUNT_RE.search(rest)
            if not am:
                current = None
                continue
            raw_amount = _parse_float(am.group(1))
            desc_part  = rest[: am.start()].strip()

            # Sign: credits positive, debits negative
            signed_amount = raw_amount if is_credit_section else -raw_amount
            current = {"date": date, "description": desc_part, "amount": signed_amount}
        elif current:
            # Continuation of prior description — only append non-amount text
            am = _CHASE_AMOUNT_RE.search(stripped)
            if not am:
                current["description"] += " " + stripped

    _flush(current)
    return txns


# ── Public interface ───────────────────────────────────────────────────────────

def parse_bank_pdf(pdf_bytes: bytes) -> list[dict]:
    # Detect bank from text content
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            first_text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
    except Exception:
        first_text = ""

    if _is_chase_bank(first_text):
        txns = _parse_chase_pdf(pdf_bytes)
        if txns:
            meta_signal = _extract_pdf_metadata(pdf_bytes)
            if meta_signal:
                txns.append(meta_signal)
            return txns

    if _is_wf_bank(first_text):
        txns = _parse_wellsfargo_pdf(pdf_bytes)
        if txns:
            meta_signal = _extract_pdf_metadata(pdf_bytes)
            if meta_signal:
                txns.append(meta_signal)
            return txns

    # Fallback to generic table parser
    txns = _parse_generic_pdf(pdf_bytes)
    meta_signal = _extract_pdf_metadata(pdf_bytes)
    if meta_signal:
        txns.append(meta_signal)
    return txns


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
