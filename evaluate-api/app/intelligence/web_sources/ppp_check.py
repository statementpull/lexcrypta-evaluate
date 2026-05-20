"""ProPublica PPP Loan Database cross-reference.

ProPublica maintains the full SBA PPP loan forgiveness database.
Cross-reference the business name to verify loan amounts against
what appears in the bank statement analysis.
"""
import logging
import httpx

log = logging.getLogger("evaluate.web.ppp")
BASE = "https://projects.propublica.org/coronavirus/bailouts/search.json"
TIMEOUT = 12


def search(business_name: str, state: str = "") -> list[dict]:
    try:
        params = {"query": business_name}
        if state:
            params["state"] = state.upper()
        resp = httpx.get(BASE, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "LexCrypta-Evaluate/1.0"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        log.warning("PPP check failed: %s", e)
        return []


def analyse(business_name: str, state: str = "") -> list[dict]:
    rows = search(business_name, state)
    if not rows:
        return []

    findings = []
    for row in rows[:5]:
        name = row.get("borrower_name", "")
        amount = float(row.get("amount") or 0)
        forgiven = float(row.get("forgiveness_amount") or 0)
        city = row.get("city", "")
        biz_state = row.get("state", "")
        loan_range = row.get("loan_range", "")
        biz_type = row.get("business_type", "")

        if amount == 0 and forgiven == 0:
            continue

        severity = "amber"
        if forgiven > 0:
            desc = (
                f"PPP LOAN CONFIRMED: {name} ({city}, {biz_state}). "
                f"Loan amount: ${amount:,.0f} ({loan_range}). "
                f"Forgiveness: ${forgiven:,.0f}. "
                f"Business type: {biz_type}. "
                "Cross-reference: verify this amount matches what appears in bank deposits. "
                "PPP forgiveness was recorded as tax-free income in the period received — "
                "confirm it is excluded from normalised EBITDA."
            )
        else:
            desc = (
                f"PPP LOAN ON RECORD — FORGIVENESS NOT CONFIRMED: {name} ({city}, {biz_state}). "
                f"Loan amount: ${amount:,.0f} ({loan_range}). "
                "Forgiveness amount not yet recorded in public database. "
                "Verify current status with SBA — outstanding PPP loans must be disclosed "
                "and may be buyer obligations in a stock deal."
            )

        findings.append({
            "source_name": "ProPublica PPP Database",
            "source_type": "web_ppp",
            "severity": severity,
            "title": f"PPP LOAN RECORD: {name[:80]} — ${amount:,.0f}",
            "description": desc,
            "confidence": 0.90,
            "raw": row,
        })

    return findings
