"""SEC EDGAR Full-Text Search.

Searches SEC EDGAR for any filings mentioning the business name.
For private SMEs, EDGAR hits usually mean:
  - A public company disclosed this business in an 8-K or 10-K
    (acquisition target, material contract, litigation opponent)
  - A prior acquisition attempt was disclosed publicly
  - The business is a subsidiary of a public entity

Any EDGAR hit on a supposedly private business warrants investigation.
"""
import logging
import httpx

log = logging.getLogger("evaluate.web.sec")
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
TIMEOUT = 12


def search(business_name: str) -> list[dict]:
    try:
        params = {
            "q": f'"{business_name}"',
            "dateRange": "custom",
            "startdt": "2018-01-01",
            "enddt": "2026-12-31",
            "hits.hits.total.value": 1,
            "hits.hits._source.period_of_report": 1,
        }
        resp = httpx.get(SEARCH_URL, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "LexCrypta-Evaluate/1.0 contact@lexcrypta.com"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        return hits
    except Exception as e:
        log.warning("SEC EDGAR search failed: %s", e)
        return []


def analyse(business_name: str, state: str = "") -> list[dict]:
    hits = search(business_name)
    if not hits:
        return []

    findings = []
    seen_forms = {}
    for hit in hits[:8]:
        src = hit.get("_source", {})
        form_type = src.get("form_type", "")
        filed = src.get("file_date", "")
        entity = src.get("entity_name") or src.get("display_names", ["Unknown"])[0] if src.get("display_names") else "Unknown"
        description_text = src.get("period_of_report", "")

        key = f"{form_type}-{entity}"
        if key in seen_forms:
            continue
        seen_forms[key] = True

        severity = "amber"
        if form_type in ("8-K", "SC 13D", "SC 13G", "DEFM14A", "PREM14A"):
            severity = "red"
            note = (
                f"Form {form_type} — this is a material event filing. "
                "8-K filings indicate the business was mentioned in connection with a "
                "material corporate event (acquisition, merger, significant contract, "
                "or litigation). "
            )
        elif form_type in ("10-K", "10-Q", "20-F"):
            note = (
                f"Form {form_type} — annual/quarterly report. "
                "A public company disclosed this business in a periodic filing — "
                "it may be a subsidiary, a significant customer/supplier, or a litigation party. "
            )
        else:
            note = f"Form {form_type} filing referencing this business name. "

        findings.append({
            "source_name": "SEC EDGAR",
            "source_type": "web_sec",
            "severity": severity,
            "title": f"SEC EDGAR HIT: {form_type} filed by {entity[:60]}",
            "description": (
                f"SEC filing: {business_name!r} appears in a {form_type} "
                f"filed by {entity} on {filed}. {note}"
                "For a business presented as fully private, any SEC filing mention warrants "
                "investigation: (1) confirm the business is not a subsidiary of a public entity "
                "— if so, buyer needs public company acquisition procedures, "
                "(2) if referenced in litigation filings, obtain details, "
                "(3) if referenced in M&A filings, a prior sale attempt may have failed — "
                "understand why."
            ),
            "confidence": 0.75,
            "raw": src,
        })

    return findings
