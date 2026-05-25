"""
Tax Return Parser
=================
Extracts key financial figures from IRS Form 1040 and supporting schedules.
Works on digital (text-extractable) PDFs. Scanned documents return partial data.

Extracted fields
----------------
  tax_year                 : Filing year (int)
  agi                      : Adjusted Gross Income (line 11)
  total_income             : Total income before adjustments
  wages                    : Wages, salaries, tips (W-2)
  business_income          : Schedule C net profit/loss
  rental_income            : Schedule E net rental income/loss
  capital_gains            : Schedule D net gain/loss
  digital_assets_declared  : True/False — debtor answered Yes to digital assets question
  other_income             : Catch-all for remaining income lines
  parse_confidence         : "high" / "partial" / "low"
"""

import io
import re

import pdfplumber


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_amount(raw: str) -> float | None:
    """Convert a string like '$42,300' or '(3,200)' to float."""
    s = raw.strip().replace(",", "").replace("$", "").replace(" ", "")
    # Parentheses = negative (accounting notation)
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _search_amount(text: str, *patterns: str) -> float | None:
    """Try each pattern in turn; return the first amount found after a match."""
    amount_re = re.compile(r"[-\(]?\$?[\d,]+(?:\.\d{2})?\)?")
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        # Look for the first dollar amount within 120 chars after the match
        snippet = text[m.end():m.end() + 120]
        a = amount_re.search(snippet)
        if a:
            val = _clean_amount(a.group())
            if val is not None:
                return val
    return None


def _search_yes_no(text: str, *patterns: str) -> bool | None:
    """Return True/False for a checkbox-style Yes/No field, or None if not found."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        snippet = text[m.end():m.end() + 80]
        yn = re.search(r"\b(yes|no)\b", snippet, re.IGNORECASE)
        if yn:
            return yn.group(1).lower() == "yes"
    return None


def _extract_year(text: str) -> int | None:
    """Extract the filing/tax year from the first 500 characters."""
    m = re.search(r"\b(20[12][0-9])\b", text[:600])
    return int(m.group(1)) if m else None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_tax_return(pdf_bytes: bytes) -> dict:
    """
    Parse an IRS Form 1040 PDF and return extracted financial figures.
    All monetary amounts are in USD. Missing fields return None.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        full_text = "\n".join(pages)
    except Exception as exc:
        return {"is_tax_return": False, "parse_error": str(exc)}

    # ── Identity check ──────────────────────────────────────────────────────
    is_1040 = bool(re.search(
        r"Form 1040|Individual Income Tax Return|U\.S\. Individual|Department of the Treasury.*Internal Revenue",
        full_text, re.IGNORECASE,
    ))
    if not is_1040:
        return {"is_tax_return": False, "parse_error": "Not a Form 1040 — no IRS identifying text found"}

    result: dict = {
        "is_tax_return": True,
        "document_type": "1040",
        "tax_year": _extract_year(full_text),

        # Income fields
        "wages":            None,   # Line 1a — W-2 wages
        "business_income":  None,   # Schedule C / Line 8
        "rental_income":    None,   # Schedule E / Line 5
        "capital_gains":    None,   # Schedule D / Line 7
        "other_income":     None,   # Line 8 / 1099-MISC / other
        "total_income":     None,   # Line 9 (total before adjustments)
        "agi":              None,   # Line 11 (adjusted gross income)

        # Digital asset disclosure
        "digital_assets_declared": None,  # True/False from checkbox

        # Metadata
        "parse_confidence": "partial",
        "parse_error": None,
    }

    # ── Extract income lines ─────────────────────────────────────────────────
    result["wages"] = _search_amount(
        full_text,
        r"Wages,?\s+salaries,?\s+tips",
        r"Total wages\b",
        r"\b1a\b.*wage",
    )
    result["business_income"] = _search_amount(
        full_text,
        r"Business income or \(loss\)",
        r"Profit or loss from business",
        r"Schedule C.*net",
    )
    result["rental_income"] = _search_amount(
        full_text,
        r"Rental real estate.*royalties",
        r"Schedule E.*net",
        r"Net rental",
    )
    result["capital_gains"] = _search_amount(
        full_text,
        r"Capital gain or \(loss\)",
        r"Schedule D.*net",
        r"\bLine 7\b.*capital",
    )
    result["other_income"] = _search_amount(
        full_text,
        r"Other income\b",
        r"Additional income",
        r"Schedule 1.*additional",
    )
    result["total_income"] = _search_amount(
        full_text,
        r"Total income\b",
        r"Add lines.*total income",
        r"\bLine 9\b",
    )
    result["agi"] = _search_amount(
        full_text,
        r"Adjusted gross income\b",
        r"This is your adjusted gross income",
        r"\bLine 11\b",
    )

    # ── Digital asset checkbox (added to 1040 in 2019) ──────────────────────
    result["digital_assets_declared"] = _search_yes_no(
        full_text,
        r"digital assets",
        r"virtual currency",
        r"cryptocurrency",
        r"receive.*sell.*exchange.*other.*digital",
    )

    # ── Compute total income estimate if not directly found ──────────────────
    if result["total_income"] is None:
        components = [
            result["wages"],
            result["business_income"],
            result["rental_income"],
            result["capital_gains"],
            result["other_income"],
        ]
        filled = [c for c in components if c is not None]
        if filled:
            result["total_income"] = round(sum(filled), 2)
            result["parse_error"] = "total_income estimated from components — verify manually"

    # ── Confidence rating ───────────────────────────────────────────────────
    key_fields = [result["agi"], result["total_income"], result["wages"]]
    filled_count = sum(1 for f in key_fields if f is not None)
    if filled_count >= 2:
        result["parse_confidence"] = "high"
    elif filled_count == 1:
        result["parse_confidence"] = "partial"
    else:
        result["parse_confidence"] = "low"
        if not result["parse_error"]:
            result["parse_error"] = (
                "Income figures not found — PDF may be scanned/image-based or use non-standard formatting"
            )

    return result
