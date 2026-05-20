"""Federal court records — litigation intelligence.

Searches federal court records for cases involving the business name
as a party (plaintiff or defendant). Federal cases include:
  - Civil litigation (breach of contract, fraud, tort)
  - Bankruptcy filings (Chapter 7, 11, 13)
  - Criminal indictments (corporate/white-collar)
  - SEC enforcement actions filed in federal court
  - EEOC discrimination suits
  - DOL/OSHA enforcement suits
  - Federal tax court matters

A business with active or recent federal litigation carries undisclosed
liability that may not appear in any financial statement. Sellers are not
always required to disclose litigation in an LOI process — this check
catches what they don't volunteer.
"""
import logging
import httpx

log = logging.getLogger("evaluate.web.court")
SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
TIMEOUT = 15

BANKRUPTCY_KW = ["bankruptcy", "chapter 7", "chapter 11", "chapter 13", "in re "]
CRIMINAL_KW = ["united states v.", "u.s. v.", "usa v.", "criminal", "indictment"]
FRAUD_KW = ["fraud", "securities fraud", "wire fraud", "mail fraud", "rico"]
EMPLOYMENT_KW = ["eeoc", "discrimination", "harassment", "wrongful termination", "flsa"]


def search(business_name: str) -> list[dict]:
    try:
        params = {
            "q": f'"{business_name}"',
            "type": "p",
            "order_by": "score desc",
            "format": "json",
        }
        resp = httpx.get(SEARCH_URL, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "LexCrypta-Evaluate/1.0"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("results", [])
    except Exception as e:
        log.warning("Court records search failed: %s", e)
        return []


def _classify(case_name: str, court: str) -> tuple[str, str]:
    cn = case_name.lower()
    if any(kw in cn for kw in BANKRUPTCY_KW):
        return "BANKRUPTCY", "red"
    if any(kw in cn for kw in CRIMINAL_KW):
        return "CRIMINAL / INDICTMENT", "red"
    if any(kw in cn for kw in FRAUD_KW):
        return "FRAUD LITIGATION", "red"
    if any(kw in cn for kw in EMPLOYMENT_KW):
        return "EMPLOYMENT LITIGATION", "amber"
    return "CIVIL LITIGATION", "amber"


def analyse(business_name: str, state: str = "") -> list[dict]:
    results = search(business_name)
    if not results:
        return []

    findings = []
    bankruptcy_count = 0
    criminal_count = 0
    civil_count = 0
    worst_severity = "amber"

    case_summaries = []

    for r in results[:10]:
        case_name = r.get("caseName", "") or r.get("case_name", "")
        date_filed = r.get("dateFiled", "") or r.get("date_filed", "")
        court = r.get("court", "") or r.get("court_id", "")
        status = r.get("status", "")
        case_type, severity = _classify(case_name, court)

        if "BANKRUPTCY" in case_type:
            bankruptcy_count += 1
            worst_severity = "red"
        elif "CRIMINAL" in case_type or "FRAUD" in case_type:
            criminal_count += 1
            worst_severity = "red"
        else:
            civil_count += 1

        case_summaries.append(
            f"{case_type}: {case_name[:80]} ({court}, {date_filed[:10]})"
        )

    total = len(results)
    summary_lines = case_summaries[:5]

    desc_parts = [
        f"Federal court records: {total} case(s) found involving {business_name!r}. "
    ]

    if bankruptcy_count:
        desc_parts.append(
            f"BANKRUPTCY FILINGS: {bankruptcy_count}. "
            "A bankruptcy filing — active or recent — is a critical acquisition risk: "
            "(1) assets may be subject to trustee control or automatic stay, "
            "(2) preference payment claims can claw back payments made to vendors or owners "
            "in the 90 days before filing, "
            "(3) buyer must confirm whether the business has been discharged or if proceedings "
            "are ongoing — deals out of bankruptcy require court approval. "
        )
    if criminal_count:
        desc_parts.append(
            f"CRIMINAL / FRAUD MATTERS: {criminal_count}. "
            "Federal criminal exposure — active or concluded — creates successor liability "
            "risk and reputational harm. Confirm disposition of all matters. "
        )
    if civil_count:
        desc_parts.append(
            f"Civil litigation: {civil_count} matter(s). "
            "Active civil cases represent contingent liabilities not always reflected "
            "on the balance sheet. Request disclosure of all pending and threatened claims "
            "and obtain reps & warranties coverage or escrow for litigation risk. "
        )

    desc_parts.append(
        f"Case summary: {' | '.join(summary_lines)}. "
        "Obtain full case details and current status for each matter before closing."
    )

    findings.append({
        "source_name": "Federal Court Records",
        "source_type": "web_court",
        "severity": worst_severity,
        "title": (
            f"COURT RECORDS: {total} federal case(s) — "
            f"{bankruptcy_count} bankruptcy, {criminal_count} criminal, {civil_count} civil"
        ),
        "description": "".join(desc_parts),
        "confidence": 0.85,
        "raw": results[:5],
    })

    return findings
