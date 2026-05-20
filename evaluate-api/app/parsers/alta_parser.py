"""ALTA Settlement Statement parser — extracts structured fields from ALTA PDFs."""
import re
import fitz  # PyMuPDF


ALTA_SIGNATURES = [
    "ALTA SETTLEMENT STATEMENT",
    "AMERICAN LAND TITLE",
    "SETTLEMENT STATEMENT",
    "ALTA/AALTA",
    "COMBINED SETTLEMENT",
    # Font-encoding artifacts: 'ti' ligature renders as 'z' in many Michigan title PDFs
    "ALTA COMBINED SEZLEMENT",
    "ALTA SELLER'S SEZLEMENT",
    "ALTA BUYER'S SEZLEMENT",
    "ALTA UNIVERSAL SEZLEMENT",
    "SEZLEMENT STATEMENT",
]

_MONEY_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{2})?)")
_DATE_RE = re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})")


def _parse_money(s: str) -> float | None:
    s = s.strip().replace(",", "").replace("$", "").replace("(", "-").replace(")", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


_ALTA_SECTION_HEADERS = {
    "BUYER", "SELLER", "LENDER", "PROPERTY", "BORROWER",
    "SEZLEMENT DATE", "SETTLEMENT DATE", "DISBURSEMENT DATE",
    "DISBURSEMENT", "PREPARED", "FILE #", "FILE NUMBER",
    "DEBIT", "CREDIT", "DATE", "DESCRIPTION", "AMOUNT",
}


def _is_header_line(line: str) -> bool:
    upper = line.strip().upper()
    return upper in _ALTA_SECTION_HEADERS or len(upper) < 3


def _find_value_after(lines: list[str], keyword: str, search_window: int = 8) -> str | None:
    kw = keyword.upper()
    for i, line in enumerate(lines):
        if kw in line.upper():
            # Check same line first (value on same line after colon)
            rest = line.split(":", 1)[-1].strip() if ":" in line else ""
            if rest and not _is_header_line(rest):
                return rest
            # Check next lines, skip known section headers and blanks
            for j in range(1, search_window + 1):
                if i + j >= len(lines):
                    break
                candidate = lines[i + j].strip()
                if candidate and not _is_header_line(candidate):
                    return candidate
    return None


def is_alta(text: str) -> bool:
    upper = text.upper()
    return any(sig in upper for sig in ALTA_SIGNATURES)


def parse_alta(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    page_texts = []
    for page in doc:
        pt = page.get_text()
        page_texts.append(pt)
        full_text += pt + "\n"

    if not is_alta(full_text):
        return {"error": "Not an ALTA settlement statement", "is_alta": False}

    lines = [ln.strip() for ln in full_text.split("\n") if ln.strip()]

    result: dict = {
        "is_alta": True,
        "document_type": "ALTA Settlement Statement",
        "raw_page_count": len(doc),
        # Transaction parties
        "buyer": None,
        "seller": None,
        "lender": None,
        # Property
        "property_address": None,
        "settlement_date": None,
        "settlement_agent": None,
        "file_number": None,
        # Financial summary
        "sale_price": None,
        "loan_amount": None,
        "earnest_deposit": None,
        "net_to_seller": None,
        "cash_from_buyer": None,
        "cash_to_seller": None,
        # Payoffs
        "payoffs": [],
        # Taxes and fees
        "state_transfer_tax": None,
        "county_transfer_tax": None,
        "lc_transfer_tax": None,
        "recording_fees": None,
        "title_insurance_owner": None,
        "title_insurance_lender": None,
        "commission_total": None,
        # Flags
        "flags": [],
    }

    # ── Settlement date ───────────────────────────────────────────────────────
    for line in lines:
        m = _DATE_RE.search(line)
        if m and any(kw in line.upper() for kw in ["SETTLEMENT DATE", "CLOSING DATE", "DATE OF CLOSING"]):
            result["settlement_date"] = line.strip()
            break

    # ── Parties ───────────────────────────────────────────────────────────────
    buyer_val = _find_value_after(lines, "BUYER")
    seller_val = _find_value_after(lines, "SELLER")
    lender_val = _find_value_after(lines, "LENDER")
    agent_val = _find_value_after(lines, "SETTLEMENT AGENT") or _find_value_after(lines, "SEZLEMENT AGENT")
    file_val = _find_value_after(lines, "FILE NUMBER") or _find_value_after(lines, "FILE #")
    prop_val = (
        _find_value_after(lines, "PROPERTY LOCATION")
        or _find_value_after(lines, "PROPERTY ADDRESS")
        or _find_value_after(lines, "PROPERTY")
    )

    if buyer_val and len(buyer_val) < 120:
        result["buyer"] = buyer_val
    if seller_val and len(seller_val) < 120:
        result["seller"] = seller_val
    if lender_val and len(lender_val) < 120:
        result["lender"] = lender_val
    if agent_val and len(agent_val) < 120:
        result["settlement_agent"] = agent_val
    if file_val and len(file_val) < 60:
        result["file_number"] = file_val
    if prop_val and len(prop_val) < 200:
        result["property_address"] = prop_val

    # ── Sale price ────────────────────────────────────────────────────────────
    for line in lines:
        if any(kw in line.upper() for kw in ["SALES PRICE", "PURCHASE PRICE", "CONTRACT PRICE"]):
            m = _MONEY_RE.search(line)
            if m:
                val = _parse_money(m.group(1))
                if val and val > 10000:
                    result["sale_price"] = val
                    break

    # ── Loan amount ───────────────────────────────────────────────────────────
    for line in lines:
        if any(kw in line.upper() for kw in ["LOAN AMOUNT", "NEW LOAN", "PRINCIPAL BALANCE"]):
            m = _MONEY_RE.search(line)
            if m:
                val = _parse_money(m.group(1))
                if val and val > 1000:
                    result["loan_amount"] = val
                    break

    # ── Earnest deposit ───────────────────────────────────────────────────────
    for line in lines:
        if any(kw in line.upper() for kw in ["EARNEST", "DEPOSIT", "EMD"]):
            m = _MONEY_RE.search(line)
            if m:
                val = _parse_money(m.group(1))
                if val and val > 100:
                    result["earnest_deposit"] = val
                    break

    # ── Net to seller / cash from buyer ───────────────────────────────────────
    for line in lines:
        upper = line.upper()
        m = _MONEY_RE.search(line)
        if not m:
            continue
        val = _parse_money(m.group(1))
        if not val:
            continue
        if "DUE TO SELLER" in upper or "NET TO SELLER" in upper or "CASH TO SELLER" in upper:
            result["net_to_seller"] = val
        elif "DUE FROM BUYER" in upper or "CASH FROM BUYER" in upper or "CASH TO CLOSE" in upper:
            result["cash_from_buyer"] = val

    # ── Payoffs (mortgage payoffs, land contract payoffs) ─────────────────────
    payoff_keywords = ["PAYOFF", "PAY OFF", "LOAN PAY", "MORTGAGE PAY", "LC PAYOFF", "LAND CONTRACT PAY"]
    for i, line in enumerate(lines):
        upper = line.upper()
        if any(kw in upper for kw in payoff_keywords):
            m = _MONEY_RE.search(line)
            amount = None
            if m:
                amount = _parse_money(m.group(1))
            elif i + 1 < len(lines):
                m2 = _MONEY_RE.search(lines[i + 1])
                if m2:
                    amount = _parse_money(m2.group(1))
            if amount and amount > 1000:
                result["payoffs"].append({
                    "description": line.strip(),
                    "amount": amount,
                })

    # ── Transfer taxes ────────────────────────────────────────────────────────
    for line in lines:
        upper = line.upper()
        m = _MONEY_RE.search(line)
        if not m:
            continue
        val = _parse_money(m.group(1))
        if "STATE TRANSFER" in upper or "STATE TAX" in upper:
            result["state_transfer_tax"] = val
        elif "COUNTY TRANSFER" in upper or "COUNTY TAX" in upper:
            result["county_transfer_tax"] = val
        elif "LAND CONTRACT TRANSFER" in upper or "LC TRANSFER" in upper:
            result["lc_transfer_tax"] = val
            result["flags"].append({
                "flag": "LC_TRANSFER_TAX",
                "severity": "amber",
                "description": f"Land Contract Transfer Tax detected: ${val:,.2f}. "
                               "Indicates property was previously on a land contract. "
                               "Obtain original LC terms — payoff amount should match disclosed balance.",
            })

    # ── Recording fees ────────────────────────────────────────────────────────
    for line in lines:
        if "RECORDING" in line.upper():
            m = _MONEY_RE.search(line)
            if m:
                val = _parse_money(m.group(1))
                if val:
                    result["recording_fees"] = (result["recording_fees"] or 0) + val

    # ── Title insurance ───────────────────────────────────────────────────────
    for line in lines:
        upper = line.upper()
        m = _MONEY_RE.search(line)
        if not m:
            continue
        val = _parse_money(m.group(1))
        if "OWNER" in upper and "TITLE" in upper and "INSURANCE" in upper:
            result["title_insurance_owner"] = val
        elif "LENDER" in upper and "TITLE" in upper and "INSURANCE" in upper:
            result["title_insurance_lender"] = val

    # ── Commission ────────────────────────────────────────────────────────────
    for line in lines:
        if "COMMISSION" in line.upper() or "BROKERAGE" in line.upper():
            m = _MONEY_RE.search(line)
            if m:
                val = _parse_money(m.group(1))
                if val:
                    result["commission_total"] = (result["commission_total"] or 0) + val

    # ── POA / Attorney-in-fact detection ─────────────────────────────────────
    if "ATTORNEY-IN-FACT" in full_text.upper() or "POWER OF ATTORNEY" in full_text.upper():
        result["flags"].append({
            "flag": "POA_SIGNING",
            "severity": "amber",
            "description": "Document signed via Power of Attorney (Attorney-In-Fact). "
                           "Verify POA was validly granted and recorded. "
                           "Obtain original POA instrument.",
        })

    # ── Record POA fee → confirms POA was used ────────────────────────────────
    if "RECORD POWER OF ATTORNEY" in full_text.upper():
        result["flags"].append({
            "flag": "POA_RECORDED",
            "severity": "amber",
            "description": "Power of Attorney recording fee found — POA was formally recorded at closing. "
                           "Standard when seller signed via designated agent while absent.",
        })

    # ── Stavvy / electronic closing ───────────────────────────────────────────
    if "STAVVY" in full_text.upper():
        result["flags"].append({
            "flag": "ELECTRONIC_CLOSING",
            "severity": "amber",
            "description": "Stavvy electronic signing platform detected. Remote/RON closing. "
                           "Verify all parties were properly identified and consented.",
        })

    # ── Commission rate anomaly ───────────────────────────────────────────────
    if result["commission_total"] and result["sale_price"]:
        rate = result["commission_total"] / result["sale_price"]
        if rate > 0.07:
            result["flags"].append({
                "flag": "HIGH_COMMISSION",
                "severity": "red",
                "description": f"Commission rate {rate:.1%} exceeds typical 6% maximum. "
                               f"Commission ${result['commission_total']:,.0f} on sale ${result['sale_price']:,.0f}. "
                               "Verify commission split and confirm no undisclosed payments.",
            })

    # ── Payoff > sale price anomaly ───────────────────────────────────────────
    total_payoffs = sum(p["amount"] for p in result["payoffs"])
    if result["sale_price"] and total_payoffs > result["sale_price"] * 0.95:
        result["flags"].append({
            "flag": "PAYOFF_EXCEEDS_SALE",
            "severity": "red",
            "description": f"Total payoffs ${total_payoffs:,.0f} approach or exceed sale price ${result['sale_price']:,.0f}. "
                           "Seller proceeds near zero or negative — investigate short sale, distressed disposition, "
                           "or undisclosed debt.",
        })

    return result


def parse_alta_from_path(path: str) -> dict:
    with open(path, "rb") as f:
        return parse_alta(f.read())
