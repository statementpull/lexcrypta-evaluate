"""EPA ECHO — Environmental Compliance History.

Queries the EPA's Enforcement and Compliance History Online (ECHO)
database for environmental violations, enforcement actions, and
permit compliance history by facility name.

A business with EPA enforcement actions carries environmental liability
that transfers to the buyer in an asset deal depending on how the
purchase agreement is structured. CERCLA successor liability can attach
to the buyer even in an asset purchase if not properly disclaimed.
"""
import logging
import httpx

log = logging.getLogger("evaluate.web.epa")
FACILITY_URL = "https://echo.epa.gov/rest/services/cef/facilities"
TIMEOUT = 15


def search_facility(business_name: str, state: str = "") -> list[dict]:
    try:
        params = {"p_fn": business_name, "output": "JSON", "p_rows": "5"}
        if state:
            params["p_st"] = state.upper()
        resp = httpx.get(FACILITY_URL, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "LexCrypta-Evaluate/1.0"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("Results", {}).get("Facilities", []) or []
    except Exception as e:
        log.warning("EPA ECHO facility search failed: %s", e)
        return []


def analyse(business_name: str, state: str = "") -> list[dict]:
    facilities = search_facility(business_name, state)
    if not facilities:
        return []

    findings = []
    for fac in facilities[:3]:
        fname = fac.get("FacilityName", business_name)
        fac_id = fac.get("SourceID", "")
        city = fac.get("CityName", "")
        fac_state = fac.get("StateAbbr", state)

        # Programme flags: CAA=air, CWA=water, RCRA=hazardous waste, SDWA=drinking water
        caa = fac.get("CAAFlag", "")
        cwa = fac.get("CWAFlag", "")
        rcra = fac.get("RCRAFlag", "")
        sdwa = fac.get("SDWAFlag", "")

        programmes = []
        if caa and caa not in ("No Data", ""):
            programmes.append(f"Clean Air Act ({caa})")
        if cwa and cwa not in ("No Data", ""):
            programmes.append(f"Clean Water Act ({cwa})")
        if rcra and rcra not in ("No Data", ""):
            programmes.append(f"Hazardous Waste/RCRA ({rcra})")
        if sdwa and sdwa not in ("No Data", ""):
            programmes.append(f"Safe Drinking Water Act ({sdwa})")

        if not programmes:
            # Facility found but no active violations
            findings.append({
                "source_name": "EPA ECHO",
                "source_type": "web_epa",
                "severity": "green",
                "title": f"EPA RECORD FOUND: {fname[:80]} — no active violations",
                "description": (
                    f"EPA record found for {fname} ({city}, {fac_state}). "
                    "No active environmental compliance flags detected. "
                    "Confirm facility is in current compliance across all applicable programmes."
                ),
                "confidence": 0.80,
                "raw": fac,
            })
        else:
            findings.append({
                "source_name": "EPA ECHO",
                "source_type": "web_epa",
                "severity": "red",
                "title": f"EPA VIOLATIONS: {fname[:60]} — {len(programmes)} programme(s) flagged",
                "description": (
                    f"EPA enforcement record: {fname} ({city}, {fac_state}). "
                    f"Compliance flags: {', '.join(programmes)}. "
                    "Environmental compliance violations are a material risk in acquisitions: "
                    "(1) CERCLA successor liability can attach to buyers even in asset deals — "
                    "clean-up obligations follow the property and the operation, not just the entity, "
                    "(2) Active permit violations create regulatory risk — the buyer inherits "
                    "all ongoing enforcement actions and consent orders, "
                    "(3) Environmental remediation costs are often not quantifiable at closing — "
                    "consider an environmental indemnity and escrow holdback. "
                    "Engage an environmental attorney and commission a Phase I Environmental "
                    "Site Assessment as a condition of closing."
                ),
                "confidence": 0.88,
                "raw": fac,
            })

    return findings
