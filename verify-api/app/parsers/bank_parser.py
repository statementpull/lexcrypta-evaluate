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

# WF transaction section header — different statement generations use different labels
_WF_SECTION_START_RE = re.compile(
    r"Transaction\s+history|Account\s+[Aa]ctivity|Account\s+History|"
    r"Transaction\s+History|Checking\s+Account\s+Transactions?|"
    r"Savings\s+Account\s+Transactions?|Account\s+Transactions?",
    re.IGNORECASE,
)

# Section headers that flag ALL following transactions as debits (money out)
# Used by Chase, BofA, Wells Fargo and similar US statement formats.
# The standalone ^WITHDRAWALS?$ pattern matches Wells Fargo's bare section header.
_DEBIT_SECTION_RE = re.compile(
    r"(ATM\s*[&\+]\s*DEBIT\s*CARD\s*WITHDRAWALS?|ELECTRONIC\s*WITHDRAWALS?|"
    r"OTHER\s*WITHDRAWALS?|SERVICE\s*FEES?|FEES?\s*CHARGED|CHECKS?\s*PAID|"
    r"ONLINE\s*PAYMENTS?|BILL\s*PAYMENTS?|\*start\*atm|\*start\*electronic|\*start\*service|"
    r"^WITHDRAWALS?$)",
    re.IGNORECASE,
)

# Section headers that flag ALL following transactions as credits (money in)
# The standalone ^DEPOSITS?$ pattern matches Wells Fargo's bare section header.
_CREDIT_SECTION_RE = re.compile(
    r"(DEPOSITS?\s+AND\s+ADDITIONS?|DEPOSITS?\s+AND\s+CREDITS?|"
    r"DIRECT\s+DEPOSITS?|OTHER\s+DEPOSITS?|CREDITS?|\*start\*deposits?|"
    r"^DEPOSITS?$)",
    re.IGNORECASE,
)

# Section headers that mean STOP parsing — balance summary tables, disclosures etc.
# Chase: "DAILY ENDING BALANCE" has 3 date+amount columns per row — must not parse as txns
_SKIP_SECTION_RE = re.compile(
    r"(DAILY\s+ENDING\s+BALANCE|DAILY\s+BALANCE|BALANCE\s+SUMMARY|"
    r"IN\s+CASE\s+OF\s+ERRORS|IMPORTANT\s+DISCLOSURES?|"
    r"\*start\*daily|\*start\*dre|\*start\*disclosure)",
    re.IGNORECASE,
)

# Resume parsing when a real transaction section restarts after a skip block
_RESUME_SECTION_RE = re.compile(
    r"(\*start\*deposits?|\*start\*atm|\*start\*electronic|"
    r"DEPOSITS?\s+AND\s+ADDITIONS?|ATM\s*[&\+]\s*DEBIT|ELECTRONIC\s*WITHDRAWALS?)",
    re.IGNORECASE,
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
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not _WF_SECTION_START_RE.search(text):
                continue

            words = page.extract_words()
            lines = _group_lines(words)

            in_section = False
            current = None

            for line in lines:
                line_text = " ".join(w["text"] for w in line)

                if _WF_SECTION_START_RE.search(line_text):
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
                        "page_number": page_num,
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
        "page_number": t.get("page_number", 0),
        "raw": {"description": desc},
    }


# ── Generic table-based PDF parser (fallback for other banks) ─────────────────

def _parse_generic_pdf(pdf_bytes: bytes) -> list[dict]:
    txns = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
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
                    "page_number": page_num,
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
# Combined-section lines end with: [-]amount  balance  (two numbers, amount may be negative)
_CHASE_COMBINED_AMT_RE = re.compile(r"(-?\d[\d,]*\.\d{2})\s+([\d,]*\.\d{2})\s*$")
_CHASE_DATE_RE   = re.compile(r"^(\d{1,2}/\d{1,2})\s+(.*)")
_CHASE_SECTION_START_RE = re.compile(r"^\*start\*(.+)", re.IGNORECASE)
_CHASE_SECTION_END_RE   = re.compile(r"^\*end\*", re.IGNORECASE)

# Chase section name fragments that contain credits
_CHASE_CREDIT_SECTIONS = {"deposits and additions", "deposits", "other credits"}
# Chase section name fragments that contain debits
_CHASE_DEBIT_SECTIONS  = {"atm", "debit withdrawal", "debit card", "electronic withdrawal",
                           "electronic payment", "checks paid", "fees", "service fee"}
# Chase combined section — single signed-amount column (newer statement format)
_CHASE_COMBINED_SECTIONS = {"transaction detail", "account activity", "account transactions"}

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
        # Build a list of (line_text, page_num) to preserve page provenance
        tagged_lines = []
        for page_num, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            for line in page_text.splitlines():
                tagged_lines.append((line, page_num))

    current_section    = ""
    is_credit_section  = False
    is_debit_section   = False
    is_combined_section = False
    skip_section       = False
    current            = None

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
            "page_number": t.get("page_number", 0),
            "raw": {"description": desc},
        })

    for line, line_page_num in tagged_lines:
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
            is_combined_section = not skip_section and any(
                cs in current_section for cs in _CHASE_COMBINED_SECTIONS
            )
            continue

        # Section end
        if _CHASE_SECTION_END_RE.match(stripped):
            _flush(current)
            current = None
            current_section = ""
            is_credit_section = is_debit_section = is_combined_section = skip_section = False
            continue

        active = is_credit_section or is_debit_section or is_combined_section
        if skip_section or not active:
            continue

        if _CHASE_SKIP_RE.match(stripped):
            continue

        # ACH continuation line — append to current description, skip amount parsing
        if current and _CHASE_ACH_CONT_RE.match(stripped):
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

            if is_combined_section:
                # Combined format: "DESCRIPTION [-]amount balance" — use signed amount directly
                am = _CHASE_COMBINED_AMT_RE.search(rest)
                if not am:
                    # Fallback: single unsigned amount (some continuation lines)
                    am2 = _CHASE_AMOUNT_RE.search(rest)
                    if not am2:
                        current = None
                        continue
                    raw_amount = _parse_float(am2.group(1))
                    desc_part  = rest[:am2.start()].strip()
                    signed_amount = raw_amount  # Unknown sign, treat as credit
                else:
                    signed_amount = _parse_float(am.group(1))   # Already signed
                    desc_part     = rest[:am.start()].strip()
            else:
                # Sectioned format: unsigned amounts, sign determined by section type
                am = _CHASE_AMOUNT_RE.search(rest)
                if not am:
                    current = None
                    continue
                raw_amount    = _parse_float(am.group(1))
                desc_part     = rest[:am.start()].strip()
                signed_amount = raw_amount if is_credit_section else -raw_amount

            current = {"date": date, "description": desc_part, "amount": signed_amount, "page_number": line_page_num}

        elif current:
            # Continuation of prior description — only append non-amount text
            am = _CHASE_AMOUNT_RE.search(stripped)
            if not am:
                current["description"] += " " + stripped

    _flush(current)
    return txns


# ── Westpac parser ────────────────────────────────────────────────────────────
#
# Westpac electronic statements use compact date format: DDJUL (no space).
# Column x-positions (empirical from eSaver statements, 595pt wide page):
#   Description : x0 < 295
#   Debit       : 295 <= x0 <= 343
#   Credit      : 343 < x0 <= 410
#   Balance     : x0 > 410

_WESTPAC_DATE_RE = re.compile(r"^\d{2}[A-Z]{3}$")

_WESTPAC_SKIP_RE = re.compile(
    r"STATEMENT\s+(OPENING|CLOSING)\s+BALANCE"
    r"|^DATE\s+DESCRIPTION"
    r"|^FROM\s+LAST\s+STATEMENT"
    r"|^\d{4}$"
    r"|STATEMENT\s+NO\.",
    re.IGNORECASE,
)

_WP_DEBIT_MIN_X   = 295.0
_WP_DEBIT_MAX_X   = 343.0
_WP_CREDIT_MIN_X  = 343.0
_WP_CREDIT_MAX_X  = 410.0
_WP_BALANCE_MIN_X = 410.0


def _is_westpac_bank(text: str) -> bool:
    t = text.upper()
    # Hard identifiers — any of these alone is definitive
    if ("WESTPAC BANKING CORPORATION" in t
            or "ABN 33 007 457 141" in t
            or "WESTPAC.COM.AU" in t):
        return True
    # Westpac is in the text — check for any corroborating signal
    if "WESTPAC" not in t:
        return False
    return (
        "ESAVER" in t                    # Westpac eSaver account
        or "WESTPAC CHOICE" in t         # Westpac Choice everyday account
        or "WESTPAC EVERYDAY" in t       # Westpac Everyday account
        or "WESTPAC FLEXI" in t          # Westpac Flexi First Option
        or "WESTPAC LIFE" in t           # Westpac Life savings
        or "WESTPAC SAVER" in t          # Westpac Saver
        or "WESTPAC LITE" in t           # Westpac Lite card
        or "WESTPAC ALTITUDE" in t       # Westpac Altitude rewards
        or "WESTPAC ACTIVATE" in t       # Westpac Activate account
        or "ELECTRONIC STATEMENT" in t   # Original: Westpac + Electronic Statement
        or "DETAILS OF YOUR ACCOUNT" in t  # Section header used by _parse_westpac_pdf
        or "STATEMENT PERIOD" in t       # Universal Westpac header
        or "STATEMENT NO." in t          # Westpac statement number field
        or ("BSB" in t and "WESTPAC" in t)  # BSB is AU-only; Westpac BSB confirmation
    )


def _classify_wp_amount(word: dict) -> str | None:
    s = word["text"].replace(",", "")
    try:
        float(s)
    except ValueError:
        return None
    x = word["x0"]
    if _WP_DEBIT_MIN_X <= x <= _WP_DEBIT_MAX_X:
        return "debit"
    if _WP_CREDIT_MIN_X < x <= _WP_CREDIT_MAX_X:
        return "credit"
    if x >= _WP_BALANCE_MIN_X:
        return "balance"
    return None


def _parse_westpac_pdf(pdf_bytes: bytes) -> list[dict]:
    txns = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            words = page.extract_words()
            lines = _group_lines(words, y_tol=3.0)
            in_txn_section = False
            current = None

            for line in lines:
                line_text = " ".join(w["text"] for w in line)

                if "DETAILS OF YOUR ACCOUNT" in line_text or "DESCRIPTION OF" in line_text:
                    in_txn_section = True
                    continue
                if not in_txn_section:
                    continue
                if _WESTPAC_SKIP_RE.search(line_text):
                    continue

                first = line[0] if line else None
                is_new_txn = (
                    first is not None
                    and bool(_WESTPAC_DATE_RE.match(first["text"]))
                    and first["x0"] < 130
                )

                if is_new_txn:
                    if current:
                        txns.append(_finalise_wp_txn(current))
                    desc_parts, debit, credit = [], 0.0, 0.0
                    for w in line[1:]:
                        col = _classify_wp_amount(w)
                        if col == "debit":
                            debit = _parse_float(w["text"])
                        elif col == "credit":
                            credit = _parse_float(w["text"])
                        elif col is None:
                            desc_parts.append(w["text"])
                    current = {
                        "date": first["text"],
                        "description": " ".join(desc_parts),
                        "debit": debit,
                        "credit": credit,
                        "page_number": page_num,
                    }
                elif current is not None:
                    for w in line:
                        col = _classify_wp_amount(w)
                        if col == "debit" and not current["debit"]:
                            current["debit"] = _parse_float(w["text"])
                        elif col == "credit" and not current["credit"]:
                            current["credit"] = _parse_float(w["text"])
                        elif col is None and w["x0"] < _WP_DEBIT_MIN_X:
                            current["description"] += " " + w["text"]

            if current:
                txns.append(_finalise_wp_txn(current))

    return txns


def _finalise_wp_txn(t: dict) -> dict:
    desc = _normalise_merchant(t["description"])
    amount = t["credit"] - t["debit"] if (t["credit"] or t["debit"]) else 0.0
    return {
        "transaction_date": t["date"],
        "merchant": desc,
        "amount": amount,
        "credit": t["credit"],
        "debit": t["debit"],
        "page_number": t.get("page_number", 0),
        "raw": {"description": desc},
    }


# ── NAB parser ─────────────────────────────────────────────────────────────────
#
# NAB statements use text-layout with dotted leaders for multi-line descriptions.
# Date format: D Mon YYYY (split across three words).
# Column x-positions (empirical from iSaver statements, 595pt wide page):
#   Date        : x0 < 95
#   Particulars : 95 <= x0 < 365
#   Debits      : 365 <= x0 <= 430
#   Credits     : 430 < x0 <= 500
#   Balance     : x0 > 500  (followed by "Cr" / "Dr" label at x0≈541)

_NAB_MONTHS = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}

_NAB_SKIP_RE = re.compile(
    r"Brought\s+forward|Balance\s+Forward"
    r"|Your\s+Interest\s+Rate|Interest\s+Rate\s+Brought|As\s+At\s+\d"
    r"|Account\s+Balance\s+Summary|Opening\s+balance|Closing\s+balance"
    r"|Total\s+credits|Total\s+debits|Transaction\s+Details"
    r"|Statement\s+number|Please\s+retain|Summary\s+of\s+Government"
    r"|Date\s+Particulars|Outlet\s+Details|Account\s+Details"
    r"|For\s+further\s+information|NAB\s+iSaver|Your\s+Savings\s+History",
    re.IGNORECASE,
)

_NAB_DEBIT_MIN_X   = 365.0
_NAB_DEBIT_MAX_X   = 430.0
_NAB_CREDIT_MIN_X  = 430.0
_NAB_CREDIT_MAX_X  = 500.0
_NAB_BALANCE_MIN_X = 500.0


def _is_nab_bank(text: str) -> bool:
    return "National Australia Bank" in text or "ABN 12 004 044 937" in text


_NAB_AMT_RE = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")

_NAB_SECTION_END_RE = re.compile(
    r"Your\s+Savings\s+History|Account\s+Balances\s+As\s+At"
    r"|Summary\s+of\s+Government|Explanatory\s+Notes",
    re.IGNORECASE,
)


def _classify_nab_amount(word: dict) -> str | None:
    if word["text"] in ("Cr", "Dr"):
        return None
    # Financial amounts always have 2 decimal places — reject years, counts, percentages
    if not _NAB_AMT_RE.match(word["text"]):
        return None
    x = word["x0"]
    if _NAB_DEBIT_MIN_X <= x <= _NAB_DEBIT_MAX_X:
        return "debit"
    if _NAB_CREDIT_MIN_X < x <= _NAB_CREDIT_MAX_X:
        return "credit"
    if x >= _NAB_BALANCE_MIN_X:
        return "balance"
    return None


def _nab_date_from_line(line: list) -> str | None:
    if len(line) < 3:
        return None
    d, m, y = line[0]["text"], line[1]["text"], line[2]["text"]
    if (re.match(r"^\d{1,2}$", d) and m in _NAB_MONTHS
            and re.match(r"^\d{4}$", y) and line[0]["x0"] < 95):
        return f"{d} {m} {y}"
    return None


def _parse_nab_pdf(pdf_bytes: bytes) -> list[dict]:
    txns = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            words = page.extract_words()
            lines = _group_lines(words, y_tol=3.0)
            current = None

            for line in lines:
                line_text = " ".join(w["text"] for w in line)
                # Section-break markers — flush current transaction, stop accumulating
                if _NAB_SECTION_END_RE.search(line_text):
                    if current:
                        txns.append(_finalise_nab_txn(current))
                        current = None
                    continue

                if _NAB_SKIP_RE.search(line_text):
                    continue

                date_str = _nab_date_from_line(line)
                if date_str:
                    if current:
                        txns.append(_finalise_nab_txn(current))
                    desc_parts, debit, credit = [], 0.0, 0.0
                    for w in line[3:]:  # skip day, month, year words
                        col = _classify_nab_amount(w)
                        if col == "debit":
                            debit = _parse_float(w["text"])
                        elif col == "credit":
                            credit = _parse_float(w["text"])
                        elif col is None and w["x0"] < _NAB_DEBIT_MIN_X:
                            desc_parts.append(w["text"].rstrip("."))
                    current = {
                        "date": date_str,
                        "description": " ".join(desc_parts),
                        "debit": debit,
                        "credit": credit,
                        "page_number": page_num,
                    }
                elif current is not None:
                    # Continuation or dotted-leader line — pick up amounts, append description
                    for w in line:
                        col = _classify_nab_amount(w)
                        if col == "debit" and not current["debit"]:
                            current["debit"] = _parse_float(w["text"])
                        elif col == "credit" and not current["credit"]:
                            current["credit"] = _parse_float(w["text"])
                        elif col is None and w["x0"] < _NAB_DEBIT_MIN_X:
                            # Strip dotted-leader noise (e.g., "Hasitha.....")
                            clean = w["text"].rstrip(".")
                            if clean and not re.match(r"^\.+$", clean):
                                current["description"] += " " + clean

            if current:
                txns.append(_finalise_nab_txn(current))

    return txns


def _finalise_nab_txn(t: dict) -> dict:
    desc = _normalise_merchant(t["description"])
    amount = t["credit"] - t["debit"] if (t["credit"] or t["debit"]) else 0.0
    return {
        "transaction_date": t["date"],
        "merchant": desc,
        "amount": amount,
        "credit": t["credit"],
        "debit": t["debit"],
        "page_number": t.get("page_number", 0),
        "raw": {"description": desc},
    }


# ── Michigan First Credit Union parser ────────────────────────────────────────
#
# Michigan First statements list transactions under section headers.
# Date format: Mon DD (e.g., "Nov 14").
# Column x-positions (empirical):
#   Date          : x0 < 80 (month at ~46, day at ~64)
#   Description   : 80 <= x0 < 380
#   Additions     : 380 <= x0 <= 440
#   Subtractions  : 440 < x0 <= 530  (negative values already include '-' sign)
#   Balance       : x0 > 530

_MF_MONTHS = _NAB_MONTHS

_MF_SKIP_RE = re.compile(
    r"Balance\s+Forward|Ending\s+Balance"
    r"|WITHDRAWALS\s+AND\s+OTHER\s+CHARGES"
    r"|DEPOSITS\s+AND\s+OTHER\s+CREDITS"
    r"|LOAN\s+ACCOUNTS|SAVINGS\s+ACCOUNTS|CHECKING\s+ACCOUNTS"
    r"|CERTIFICATE\s+ACCOUNTS|PAYMENT\s+INFORMATION"
    r"|Annual\s+Percentage\s+Yield|Date\s+Transaction\s+Description"
    r"|Account\s+Balances|Statement\s+of\s+Accounts"
    r"|MichiganFirst\.com|Withdrawals\s+and\s+Other\s+Charges\s+for"
    r"|Deposits\s+and\s+Other\s+Credits\s+for",
    re.IGNORECASE,
)

_MF_ADD_MIN_X  = 380.0
_MF_ADD_MAX_X  = 440.0
_MF_SUB_MIN_X  = 440.0
_MF_SUB_MAX_X  = 530.0
_MF_BAL_MIN_X  = 530.0


def _is_mf_bank(text: str) -> bool:
    return "MichiganFirst.com" in text or "Michigan First" in text


def _classify_mf_amount(word: dict) -> str | None:
    s = word["text"].replace(",", "").lstrip("-")
    try:
        float(s)
    except ValueError:
        return None
    x = word["x0"]
    if _MF_ADD_MIN_X <= x <= _MF_ADD_MAX_X:
        return "addition"
    if _MF_SUB_MIN_X < x <= _MF_SUB_MAX_X:
        return "subtraction"
    if x >= _MF_BAL_MIN_X:
        return "balance"
    return None


def _mf_date_from_line(line: list) -> str | None:
    if len(line) < 2:
        return None
    month, day = line[0]["text"], line[1]["text"]
    if (month in _MF_MONTHS and line[0]["x0"] < 60
            and re.match(r"^\d{1,2}$", day)):
        return f"{month} {day}"
    return None


def _parse_mf_pdf(pdf_bytes: bytes) -> list[dict]:
    txns = []
    in_summary_section = False

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            words = page.extract_words()
            lines = _group_lines(words, y_tol=3.0)
            current = None

            for line in lines:
                line_text = " ".join(w["text"] for w in line)

                # Stop parsing transactions once we hit the summary tables
                if re.search(r"WITHDRAWALS\s+AND\s+OTHER\s+CHARGES"
                              r"|DEPOSITS\s+AND\s+OTHER\s+CREDITS"
                              r"|LOAN\s+ACCOUNTS", line_text, re.IGNORECASE):
                    if current:
                        txns.append(_finalise_mf_txn(current))
                        current = None
                    in_summary_section = True
                    continue

                if in_summary_section:
                    # New account section header resets summary flag
                    if re.search(r"SAVINGS\s+ACCOUNTS|CHECKING\s+ACCOUNTS"
                                  r"|CERTIFICATE\s+ACCOUNTS", line_text, re.IGNORECASE):
                        in_summary_section = False
                    continue

                if _MF_SKIP_RE.search(line_text):
                    continue

                date_str = _mf_date_from_line(line)
                if date_str:
                    if current:
                        txns.append(_finalise_mf_txn(current))
                    desc_parts, addition, subtraction = [], 0.0, 0.0
                    for w in line[2:]:  # skip month, day words
                        col = _classify_mf_amount(w)
                        if col == "addition":
                            addition = _parse_float(w["text"])
                        elif col == "subtraction":
                            subtraction = _parse_float(w["text"].lstrip("-"))
                        elif col is None and w["x0"] < _MF_ADD_MIN_X:
                            desc_parts.append(w["text"])
                    current = {
                        "date": date_str,
                        "description": " ".join(desc_parts),
                        "addition": addition,
                        "subtraction": subtraction,
                        "page_number": page_num,
                    }
                elif current is not None:
                    # ACH continuation line (ID: XXXXXX CO: NAME) or other continuation
                    for w in line:
                        if w["x0"] >= _MF_ADD_MIN_X:
                            break  # amounts on a continuation line belong to a different summary
                        current["description"] += " " + w["text"]

            if current:
                txns.append(_finalise_mf_txn(current))
            in_summary_section = False  # reset between pages

    return txns


def _finalise_mf_txn(t: dict) -> dict:
    desc = _normalise_merchant(t["description"])
    amount = t["addition"] - t["subtraction"]
    return {
        "transaction_date": t["date"],
        "merchant": desc,
        "amount": amount,
        "credit": t["addition"],
        "debit": t["subtraction"],
        "page_number": t.get("page_number", 0),
        "raw": {"description": desc},
    }


# ── Public interface ───────────────────────────────────────────────────────────

# ── Text-line fallback parser ─────────────────────────────────────────────────
# Used when all bank-specific and table parsers return 0 transactions.
# Handles text-layout PDFs (WF, Chase, and AU banks) by scanning for lines that
# start with a date, then extracting the first money amount as the transaction value.

_TL_DATE_RE = re.compile(
    r"""^[\s]*
    (
      \d{1,2}/\d{1,2}/\d{2,4}                                        # MM/DD/YYYY
      | \d{1,2}/\d{1,2}(?=\s)                                         # MM/DD (no year)
      | \d{1,2}[\s\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\-]\d{2,4}
      | (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}
    )
    [\s,]+
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TL_MONEY_RE = re.compile(r"(-?\$?[\d,]{1,12}\.\d{2})")

_TL_SKIP_RE = re.compile(
    r"^\s*(date|description|narration|narrative|details|transaction|debit|credit|"
    r"balance|opening|closing|brought forward|carried forward|page\s+\d|"
    r"account number|account name|bsb|statement period|available)\b",
    re.IGNORECASE,
)


def _parse_text_lines(full_text: str) -> list[dict]:
    """Parse transactions from raw PDF text when all structured parsers return 0."""
    txns = []
    section_is_debit = False
    section_skip = False

    for line in full_text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < 8:
            continue

        if _SKIP_SECTION_RE.search(stripped):
            section_skip = True
            continue
        if section_skip:
            if _RESUME_SECTION_RE.search(stripped):
                section_skip = False
            else:
                continue

        if _DEBIT_SECTION_RE.search(stripped):
            section_is_debit = True
            continue
        if _CREDIT_SECTION_RE.search(stripped):
            section_is_debit = False
            continue

        if _TL_SKIP_RE.match(stripped):
            continue

        m = _TL_DATE_RE.match(stripped)
        if not m:
            continue

        date_str = m.group(1).strip()
        rest = stripped[m.end():].strip()

        amounts = _TL_MONEY_RE.findall(rest)
        if not amounts:
            continue

        desc = _TL_MONEY_RE.sub("", rest).strip()
        desc = re.sub(r"\s*\b(DR|CR)\b.*$", "", desc, flags=re.IGNORECASE).strip()
        desc = re.sub(r"\s*-?\$+\s*", " ", desc).strip()
        desc = re.sub(r"\s{2,}", " ", desc).strip()
        if not desc or len(desc) < 2:
            continue

        try:
            amount = float(amounts[0].replace(",", "").replace("$", ""))
        except ValueError:
            continue

        if amount > 0:
            if re.search(r"\bDR\b", rest, re.IGNORECASE) or section_is_debit:
                amount = -amount

        txns.append({
            "transaction_date": date_str,
            "merchant": _normalise_merchant(desc),
            "amount": amount,
            "raw": {"line": stripped},
        })

    return txns


def parse_bank_pdf(pdf_bytes: bytes, filename: str = "") -> list[dict]:
    # Detect bank from first two pages (some banks print identifying text on page 2)
    all_page_text: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            first_text = "\n".join(
                (p.extract_text() or "") for p in pdf.pages[:2]
            )
            all_page_text = [(p.extract_text() or "") for p in pdf.pages]
    except Exception:
        first_text = ""

    bank_parsers = [
        (_is_chase_bank,   _parse_chase_pdf),
        (_is_wf_bank,      _parse_wellsfargo_pdf),
        (_is_westpac_bank, _parse_westpac_pdf),
        (_is_nab_bank,     _parse_nab_pdf),
        (_is_mf_bank,      _parse_mf_pdf),
    ]

    for detect, parse in bank_parsers:
        if detect(first_text):
            txns = parse(pdf_bytes)
            if txns:
                meta_signal = _extract_pdf_metadata(pdf_bytes)
                if meta_signal:
                    txns.append(meta_signal)
                # Safety pass: ensure page_number and source_file on every transaction
                for t in txns:
                    t.setdefault("page_number", 0)
                    t.setdefault("source_file", filename)
                return txns

    # Fallback 1: generic table parser
    txns = _parse_generic_pdf(pdf_bytes)
    if not txns and all_page_text:
        # Fallback 2: text-line parser — handles text-layout PDFs with no embedded tables
        txns = _parse_text_lines("\n".join(all_page_text))

    meta_signal = _extract_pdf_metadata(pdf_bytes)
    if meta_signal:
        txns.append(meta_signal)
    # Safety pass: ensure page_number and source_file on every transaction
    for t in txns:
        t.setdefault("page_number", 0)
        t.setdefault("source_file", filename)
    return txns


def parse_bank_csv_text(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    txns = []
    for row in reader:
        keys = {k.lower().strip(): v for k, v in row.items()}
        date = keys.get("date", "")
        desc = keys.get("description", keys.get("narrative", keys.get("memo", "")))

        # Try split debit/credit columns first; fall back to single Amount column
        debit  = keys.get("debit",  keys.get("withdrawals", keys.get("debit amount", "")))
        credit = keys.get("credit", keys.get("deposits",    keys.get("credit amount", "")))
        if debit or credit:
            amount = normalise_amount(debit, credit)
        else:
            # Single Amount column — positive = credit, negative = debit
            raw_amt = keys.get("amount", keys.get("transaction amount", keys.get("amt", "")))
            amount = _parse_float(raw_amt) if raw_amt else 0.0

        if not (desc or "").strip():
            continue
        txns.append({
            "transaction_date": date.strip(),
            "merchant": desc.strip().upper(),
            "amount": amount,
            "raw": dict(row),
        })
    return txns
