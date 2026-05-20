"""DOL Wage & Hour + OSHA Inspection Database.

Queries the Department of Labor enforcement databases for:
  - Wage and hour violations (back pay, civil money penalties)
  - OSHA safety inspections, citations, and penalties

WHD violations signal: underpayment of workers (minimum wage, overtime),
child labour violations, tip theft. These create successor liability
risk and indicate systemic HR issues that persist post-acquisition.

OSHA citations signal: workplace safety failures, repeat violations,
willful violations. Some industries carry post-acquisition successor
liability for OSHA penalties under OSHRC precedent.
"""
import logging
import httpx

log = logging.getLogger("evaluate.web.dol")
DOL_BASE = "https://data.dol.gov/get"
TIMEOUT = 15


def _get(endpoint: str, name: str) -> list[dict]:
    try:
        encoded = name.replace("'", "''")
        url = f"{DOL_BASE}/{endpoint}"
        params = {
            "filters": f"trade_nm like '%{encoded}%'" if endpoint == "whd_whisard"
                       else f"estab_name like '%{encoded}%'",
            "limit": "10",
        }
        resp = httpx.get(url, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "LexCrypta-Evaluate/1.0"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        # DOL API returns {endpoint_name: [...]}
        for key, val in data.items():
            if isinstance(val, list):
                return val
        return []
    except Exception as e:
        log.warning("DOL %s check failed: %s", endpoint, e)
        return []


def analyse_whd(business_name: str, state: str = "") -> list[dict]:
    rows = _get("whd_whisard", business_name)
    if not rows:
        return []

    findings = []
    for row in rows[:5]:
        trade_nm = row.get("trade_nm", business_name)
        bw_amt = float(row.get("bw_atp_amt") or 0)       # back wages
        ee_cnt = int(row.get("ee_atp_cnt") or 0)          # employees owed
        civil = float(row.get("cmp_assd_amt") or 0)       # civil money penalty
        violation_type = row.get("violtn_cnt", 0)
        findings_start = row.get("findings_start_date", "")

        if bw_amt == 0 and civil == 0:
            continue

        severity = "red" if bw_amt > 50000 or civil > 10000 else "amber"
        findings.append({
            "source_name": "DOL Wage & Hour Division",
            "source_type": "web_dol_whd",
            "severity": severity,
            "title": f"WAGE VIOLATION: {trade_nm[:70]} — ${bw_amt:,.0f} back wages",
            "description": (
                f"DOL Wage & Hour enforcement record: {trade_nm}. "
                f"Back wages assessed: ${bw_amt:,.0f} affecting {ee_cnt} employee(s). "
                f"Civil money penalty: ${civil:,.0f}. "
                f"Findings period start: {findings_start}. "
                "Wage violations indicate: underpayment of workers (minimum wage, overtime), "
                "tip theft, or misclassification of employees as independent contractors. "
                "Buyer risk: (1) DOL back-wage orders may still be outstanding — "
                "confirm current compliance status with WHD, "
                "(2) Systemic wage practices that violated the law likely continue post-close "
                "unless HR and payroll systems are overhauled, "
                "(3) Employee relations damage may affect retention post-acquisition. "
                "Request current DOL compliance certificate and any open WHD cases."
            ),
            "confidence": 0.85,
            "raw": row,
        })

    return findings


def analyse_osha(business_name: str, state: str = "") -> list[dict]:
    rows = _get("full_inspections", business_name)
    if not rows:
        return []

    findings = []
    total_penalties = 0.0
    citation_types = []

    for row in rows[:10]:
        penalty = float(row.get("total_current_penalty") or 0)
        total_penalties += penalty
        cit_type = row.get("citation_type", "")
        if cit_type and cit_type not in citation_types:
            citation_types.append(cit_type)

    if total_penalties == 0 and not citation_types:
        return []

    has_willful = any("willful" in ct.lower() for ct in citation_types)
    has_repeat = any("repeat" in ct.lower() for ct in citation_types)
    severity = "red" if (has_willful or has_repeat or total_penalties > 50000) else "amber"

    findings.append({
        "source_name": "OSHA Enforcement Database",
        "source_type": "web_osha",
        "severity": severity,
        "title": f"OSHA CITATIONS: {business_name[:60]} — ${total_penalties:,.0f} in penalties",
        "description": (
            f"OSHA enforcement history: {len(rows)} inspection(s) found for {business_name}. "
            f"Total penalties: ${total_penalties:,.0f}. "
            f"Citation types: {', '.join(citation_types) if citation_types else 'Various'}. "
            f"{'WILLFUL VIOLATIONS DETECTED — highest severity OSHA category. ' if has_willful else ''}"
            f"{'REPEAT VIOLATIONS — OSHA escalates penalties for repeat offenders. ' if has_repeat else ''}"
            "Buyer risk: (1) Open OSHA cases and outstanding penalties transfer in a stock deal, "
            "(2) Repeat and willful violations can result in enhanced penalties post-close, "
            "(3) Worker injury history creates workers' compensation claims that affect "
            "experience modification rates (EMR) and insurance premiums, "
            "(4) Ongoing safety culture issues increase post-acquisition liability exposure. "
            "Request full OSHA inspection history and current abatement status for all citations."
        ),
        "confidence": 0.82,
        "raw": rows[0] if rows else {},
    })

    return findings


def analyse(business_name: str, state: str = "") -> list[dict]:
    findings = []
    findings += analyse_whd(business_name, state)
    findings += analyse_osha(business_name, state)
    return findings
