"""OFAC Consolidated Sanctions List check.

Downloads the OFAC Specially Designated Nationals (SDN) and
consolidated sanctions list, checks business name and owner names
against all listed entities.

An OFAC match on any party to a transaction is a federal compliance
violation. No US person may conduct business with a sanctioned entity
without specific OFAC authorisation. A false negative here has
catastrophic legal consequences — this check must be conservative.

Matching approach:
  - Normalise names (remove punctuation, uppercase, collapse spaces)
  - Check for substring matches on normalised strings
  - Flag near-matches for manual review
  - Never auto-clear an OFAC check — always recommend professional review
"""
import csv
import io
import logging
import re
import httpx

log = logging.getLogger("evaluate.web.ofac")
SDN_URL = "https://www.treasury.gov/ofac/downloads/consolidated/consolidated.csv"
TIMEOUT = 20


def _normalise(name: str) -> str:
    name = name.upper()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _download_list() -> list[dict]:
    try:
        resp = httpx.get(SDN_URL, timeout=TIMEOUT,
                         headers={"User-Agent": "LexCrypta-Evaluate/1.0"})
        if resp.status_code != 200:
            return []
        reader = csv.DictReader(io.StringIO(resp.text))
        return list(reader)
    except Exception as e:
        log.warning("OFAC list download failed: %s", e)
        return []


def _check_name(name: str, sdn_rows: list[dict]) -> list[dict]:
    norm = _normalise(name)
    words = [w for w in norm.split() if len(w) > 3]
    matches = []
    for row in sdn_rows:
        sdn_name = _normalise(row.get("name", "") or row.get("Name", "") or "")
        if not sdn_name:
            continue
        if norm == sdn_name:
            matches.append({"match_type": "EXACT", "sdn_name": sdn_name, "row": row})
        elif len(words) >= 2 and all(w in sdn_name for w in words):
            matches.append({"match_type": "STRONG", "sdn_name": sdn_name, "row": row})
    return matches


def analyse(business_name: str, owner_names: list[str] | None = None, state: str = "") -> list[dict]:
    sdn_rows = _download_list()
    if not sdn_rows:
        return [{
            "source_name": "OFAC Consolidated Sanctions",
            "source_type": "web_ofac",
            "severity": "amber",
            "title": "OFAC CHECK: Could not complete — manual review required",
            "description": (
                "OFAC consolidated sanctions list could not be retrieved. "
                "Manual OFAC screening is required before closing. "
                "All parties must be checked, including business name, owners, directors, and key vendors."
            ),
            "confidence": 0.50,
            "raw": {},
        }]

    findings = []
    names_to_check = [business_name] + (owner_names or [])

    all_clear = True
    for name in names_to_check:
        if not name or not name.strip():
            continue
        matches = _check_name(name, sdn_rows)
        if matches:
            all_clear = False
            for m in matches:
                row = m["row"]
                programme = row.get("programs", row.get("program", "UNKNOWN"))
                findings.append({
                    "source_name": "OFAC Consolidated Sanctions",
                    "source_type": "web_ofac",
                    "severity": "red",
                    "title": f"OFAC MATCH ({m['match_type']}): {name[:60]} — {programme}",
                    "description": (
                        f"OFAC SANCTIONS MATCH — {m['match_type']}: '{name}' matched "
                        f"SDN entry '{m['sdn_name']}' (Programme: {programme}). "
                        "CRITICAL: No US person may conduct any business with a sanctioned entity "
                        "without specific OFAC licence. This match MUST be reviewed by a "
                        "sanctions compliance attorney before any further due diligence or "
                        "transaction activity. "
                        "Note: OFAC matches require professional verification — false positives "
                        "exist due to name similarities. Do NOT conclude without expert review."
                    ),
                    "confidence": 0.90,
                    "raw": row,
                })

    if all_clear:
        findings.append({
            "source_name": "OFAC Consolidated Sanctions",
            "source_type": "web_ofac",
            "severity": "green",
            "title": f"OFAC CLEAR: No matches on {len(names_to_check)} name(s) checked",
            "description": (
                f"OFAC consolidated sanctions list: no matches found for "
                f"{', '.join(names_to_check[:3])}{'...' if len(names_to_check) > 3 else ''}. "
                "Note: This automated check uses name normalisation. "
                "Always supplement with a manual OFAC search for all transaction parties."
            ),
            "confidence": 0.75,
            "raw": {},
        })

    return findings
