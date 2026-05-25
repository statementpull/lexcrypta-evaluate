"""
Bankruptcy Petition Parser
===========================
Extracts asset and income declarations from US bankruptcy petition documents.
Supports Chapter 7 and Chapter 13. Handles both the new (2016+) Official Forms
and older pre-2016 schedules.

Target documents
----------------
  Official Form 106A/B   — Real and Personal Property (Schedule A/B)
  Official Form 106I     — Income (Schedule I)
  Official Form 106J     — Expenses (Schedule J)
  Official Form 107      — Statement of Financial Affairs (SOFA)
  Voluntary Petition     — Case metadata, debtor info, chapter

Key extracted fields
--------------------
  chapter                : 7 or 13
  real_property_value    : Total declared real estate value
  vehicle_value          : Total declared vehicle value
  bank_balance_declared  : Cash / bank account balances declared
  crypto_declared        : Whether debtor declared crypto (bool)
  crypto_value           : Value of declared crypto (if any)
  total_assets_declared  : Sum of all Schedule A/B assets
  monthly_income         : Schedule I current monthly income
  monthly_expenses       : Schedule J current monthly expenses
  sofa_income_yr1        : SOFA — income in year before filing
  sofa_income_yr2        : SOFA — income two years before filing
  insider_payments       : SOFA — payments to insiders in last year
  recent_transfers       : SOFA — transfers/gifts in last 2 years (bool + description)
  parse_confidence       : "high" / "partial" / "low"
"""

import io
import re

import pdfplumber


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_amount(raw: str) -> float | None:
    s = raw.strip().replace(",", "").replace("$", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _search_amount(text: str, *patterns: str) -> float | None:
    amount_re = re.compile(r"[-\(]?\$?[\d,]+(?:\.\d{2})?\)?")
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        snippet = text[m.end():m.end() + 150]
        a = amount_re.search(snippet)
        if a:
            val = _clean_amount(a.group())
            if val is not None and abs(val) < 100_000_000:  # sanity cap
                return val
    return None


def _search_yes_no(text: str, *patterns: str) -> bool | None:
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        snippet = text[m.end():m.end() + 80]
        yn = re.search(r"\b(yes|no)\b", snippet, re.IGNORECASE)
        if yn:
            return yn.group(1).lower() == "yes"
    return None


def _extract_chapter(text: str) -> int | None:
    m = re.search(r"Chapter\s+(7|11|12|13)\b", text[:1000], re.IGNORECASE)
    return int(m.group(1)) if m else None


def _sum_line_amounts(text: str, section_pattern: str, max_lines: int = 20) -> float | None:
    """Find a section and sum all dollar amounts found in the next max_lines."""
    m = re.search(section_pattern, text, re.IGNORECASE)
    if not m:
        return None
    snippet = text[m.end():m.end() + 120 * max_lines]
    amounts = re.findall(r"\$?([\d,]+\.\d{2})", snippet)
    values = []
    for a in amounts:
        v = _clean_amount(a)
        if v is not None and v > 0:
            values.append(v)
    return round(sum(values), 2) if values else None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_bankruptcy_petition(pdf_bytes: bytes) -> dict:
    """
    Parse a US bankruptcy petition PDF and return extracted declarations.
    All monetary amounts are in USD. Missing fields return None.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        full_text = "\n".join(pages)
    except Exception as exc:
        return {"is_petition": False, "parse_error": str(exc)}

    # ── Identity check ──────────────────────────────────────────────────────
    is_petition = bool(re.search(
        r"United States Bankruptcy Court|Schedule A/B|Schedule I|"
        r"Statement of Financial Affairs|Voluntary Petition|Official Form 10[0-9]",
        full_text, re.IGNORECASE,
    ))
    if not is_petition:
        return {
            "is_petition": False,
            "parse_error": "Not a bankruptcy petition — no court identifying text found",
        }

    result: dict = {
        "is_petition": True,
        "chapter": _extract_chapter(full_text),

        # Schedule A/B — Assets
        "real_property_value":   None,   # Real estate
        "vehicle_value":         None,   # Cars, trucks, motorcycles
        "bank_balance_declared": None,   # Cash, bank accounts
        "crypto_declared":       None,   # True/False — any crypto listed
        "crypto_value":          None,   # Dollar value if declared
        "business_interest":     None,   # Business ownership interest value
        "total_assets_declared": None,   # Total A/B sum

        # Schedule I — Income
        "monthly_income":        None,   # Current monthly take-home

        # Schedule J — Expenses
        "monthly_expenses":      None,   # Current monthly expenses

        # SOFA — Statement of Financial Affairs
        "sofa_income_yr1":       None,   # Income in year before filing
        "sofa_income_yr2":       None,   # Income 2 years before filing
        "insider_payments":      None,   # Payments to insiders last year
        "recent_transfers":      False,  # Transfers/gifts in last 2 years

        # Metadata
        "parse_confidence": "partial",
        "parse_error": None,
    }

    # ── Schedule A/B: Real Property ─────────────────────────────────────────
    result["real_property_value"] = _search_amount(
        full_text,
        r"Part 1[:\.].*?real property|1\.1\b|Describe.*?real estate",
        r"What is the property worth\?|Current value.*?real",
        r"Real estate.*?value",
    )

    # ── Schedule A/B: Vehicles ──────────────────────────────────────────────
    result["vehicle_value"] = _search_amount(
        full_text,
        r"Vehicles.*?current value|Cars,?\s+vans,?\s+trucks",
        r"Part 2[:\.].*?vehicle|3\.1\b|Automobiles",
    )

    # ── Schedule A/B: Bank accounts / cash ─────────────────────────────────
    result["bank_balance_declared"] = _search_amount(
        full_text,
        r"(?:Checking|Savings|bank\s+account).*?balance|Cash on hand",
        r"Deposits.*?money\s+market|Financial\s+accounts",
        r"17\.1\b|Part 4[:\.].*?financial",
    )

    # ── Schedule A/B: Crypto / Digital Assets ──────────────────────────────
    result["crypto_declared"] = _search_yes_no(
        full_text,
        r"cryptocurrency|digital\s+(?:currency|asset|token)",
        r"Bitcoin|Ethereum|virtual\s+currency",
    )
    if result["crypto_declared"]:
        result["crypto_value"] = _search_amount(
            full_text,
            r"cryptocurrency|digital\s+(?:currency|asset)",
            r"Bitcoin|Ethereum",
        )

    # ── Schedule A/B: Business interests ────────────────────────────────────
    result["business_interest"] = _search_amount(
        full_text,
        r"Business.*?ownership interest|Part 5[:\.].*?business",
        r"Equity interest.*?business",
    )

    # ── Schedule A/B: Total ─────────────────────────────────────────────────
    result["total_assets_declared"] = _search_amount(
        full_text,
        r"Total.*?Schedule A/B|Total value.*?assets",
        r"Add.*?Part 1.*?Part 2.*?Part 3|Sum of all",
        r"Total personal property",
    )
    # If not found, estimate from components
    if result["total_assets_declared"] is None:
        components = [
            result["real_property_value"],
            result["vehicle_value"],
            result["bank_balance_declared"],
            result["crypto_value"],
            result["business_interest"],
        ]
        filled = [c for c in components if c is not None]
        if filled:
            result["total_assets_declared"] = round(sum(filled), 2)

    # ── Schedule I: Monthly Income ──────────────────────────────────────────
    result["monthly_income"] = _search_amount(
        full_text,
        r"(?:Monthly|Current monthly).*?(?:take.home|net income|income after)",
        r"Schedule I.*?(?:net|take.home)",
        r"Combined monthly net income",
        r"Total monthly.*?(?:income|pay)",
    )

    # ── Schedule J: Monthly Expenses ────────────────────────────────────────
    result["monthly_expenses"] = _search_amount(
        full_text,
        r"Total monthly.*?expenses|Total estimated monthly",
        r"Schedule J.*?total",
        r"Are your expenses.*?increase|Line 22[ab]",
    )

    # ── SOFA: Income history ─────────────────────────────────────────────────
    # SOFA Part 2 — lists income for last 2 calendar years + current year
    sofa_section = ""
    m = re.search(r"Part\s+2[:\.].*?income|Statement of Financial Affairs.*?income", full_text, re.IGNORECASE)
    if m:
        sofa_section = full_text[m.start():m.start() + 2000]
    if sofa_section:
        result["sofa_income_yr1"] = _search_amount(
            sofa_section, r"(?:last|prior|preceding)\s+(?:calendar\s+)?year",
        )
        result["sofa_income_yr2"] = _search_amount(
            sofa_section, r"(?:two|2)\s+years?\s+(?:before|prior|ago)",
        )

    # ── SOFA: Insider payments ────────────────────────────────────────────────
    result["insider_payments"] = _search_amount(
        full_text,
        r"payments.*?insider|insider.*?payment|Part 6[:\.]",
        r"relative.*?payment|business partner.*?payment",
    )

    # ── SOFA: Transfers ───────────────────────────────────────────────────────
    result["recent_transfers"] = bool(re.search(
        r"transfer.*?property|gave.*?away|gift.*?property|Part 7",
        full_text, re.IGNORECASE,
    ))

    # ── Confidence rating ──────────────────────────────────────────────────
    key_fields = [
        result["total_assets_declared"],
        result["monthly_income"],
        result["real_property_value"],
    ]
    filled_count = sum(1 for f in key_fields if f is not None)
    if filled_count >= 2:
        result["parse_confidence"] = "high"
    elif filled_count == 1:
        result["parse_confidence"] = "partial"
    else:
        result["parse_confidence"] = "low"
        if not result["parse_error"]:
            result["parse_error"] = (
                "Key fields not found — check that Schedule A/B and Schedule I are included"
            )

    return result
