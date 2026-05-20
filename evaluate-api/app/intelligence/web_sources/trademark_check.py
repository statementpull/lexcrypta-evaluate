"""USPTO Trademark & brand IP intelligence.

Searches the USPTO trademark database for registrations under the
business name. Reveals:
  - Whether the brand is actually protected (or just assumed to be)
  - Trademark ownership — is the mark owned by the business or the owner personally?
    (Personal ownership = licenced back to the business, doesn't transfer in asset deal)
  - Opposition or cancellation proceedings — contested marks are liabilities
  - Dead/abandoned marks — brand may be unprotected despite prior registration
  - Class coverage — a mark registered only in one class may leave the core
    business use unprotected

Critical M&A IP issues:
  If the trademark is owned by the seller personally (not the business entity),
  it does NOT transfer automatically in an asset sale. The buyer gets the business
  operations but NOT the brand — they'd need a separate trademark assignment,
  and the seller could refuse or demand additional consideration.

  If the trademark is pending opposition or cancellation, the brand is contested —
  the buyer may acquire a business whose name it cannot legally use.
"""
import logging
import httpx

log = logging.getLogger("evaluate.web.trademark")
SEARCH_URL = "https://developer.uspto.gov/ibd-api/v1/application/basicSearch"
TIMEOUT = 15

STATUS_MAP = {
    "registered": ("REGISTERED", "green"),
    "pending": ("PENDING — not yet registered", "amber"),
    "abandoned": ("ABANDONED — unprotected", "red"),
    "cancelled": ("CANCELLED — registration lapsed", "red"),
    "expired": ("EXPIRED", "amber"),
    "opposition": ("UNDER OPPOSITION", "red"),
}


def search(business_name: str) -> list[dict]:
    try:
        params = {
            "searchText": business_name,
            "start": 0,
            "rows": 10,
        }
        resp = httpx.get(SEARCH_URL, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "LexCrypta-Evaluate/1.0"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("results", []) or data.get("hits", []) or []
    except Exception as e:
        log.warning("USPTO trademark search failed: %s", e)
        return []


def analyse(business_name: str, state: str = "") -> list[dict]:
    results = search(business_name)
    if not results:
        # No results — could mean no trademark at all
        return [{
            "source_name": "USPTO Trademark",
            "source_type": "web_trademark",
            "severity": "amber",
            "title": f"TRADEMARK: No registration found for {business_name[:60]}",
            "description": (
                f"No USPTO trademark registration found for {business_name!r}. "
                "An unregistered brand carries IP risk: "
                "(1) common law trademark rights exist only in the geographic area of use — "
                "the business cannot expand its brand into new markets without risk of conflict, "
                "(2) without federal registration, the brand cannot use the ® symbol and "
                "enforcement against infringers is more difficult and expensive, "
                "(3) a competitor could register the same mark in a different class or geography. "
                "If the brand is a significant part of the acquisition value, "
                "commission a trademark clearance search and consider filing for registration "
                "post-acquisition."
            ),
            "confidence": 0.65,
            "raw": {},
        }]

    findings = []
    registered_count = 0
    problem_count = 0

    for r in results[:5]:
        mark_name = (
            r.get("markLiteralElements") or
            r.get("mark_identification") or
            r.get("wordMark") or
            business_name
        )
        owner = (
            r.get("ownerName") or
            r.get("owner") or
            r.get("applicantName") or
            "Unknown owner"
        )
        status_raw = (r.get("statusCode") or r.get("status") or "").lower()
        serial = r.get("serialNumber") or r.get("serial_number") or ""
        gs_class = r.get("internationalClassDescription") or r.get("goodsAndServices") or ""

        # Map status
        status_label, severity = "UNKNOWN", "amber"
        for kw, (label, sev) in STATUS_MAP.items():
            if kw in status_raw:
                status_label, severity = label, sev
                break

        if severity == "green":
            registered_count += 1
        elif severity == "red":
            problem_count += 1

        # Key flag: owner is a person, not the business entity
        owner_mismatch = (
            owner.upper() != business_name.upper() and
            any(name_word in owner.upper()
                for name_word in business_name.upper().split()[:2])
        )

        desc_parts = [
            f"Trademark: {mark_name!r} — {status_label}. "
            f"Owner of record: {owner}. "
        ]

        if owner.upper() != business_name.upper():
            desc_parts.append(
                f"OWNER MISMATCH: trademark is registered to '{owner}', "
                f"not to '{business_name}'. "
                "If the mark is owned personally by the seller (not the business entity), "
                "it will NOT transfer automatically in an asset sale. "
                "A separate trademark assignment agreement is required — "
                "confirm the seller has authority to assign and include assignment "
                "in the purchase agreement schedules. "
            )

        if "ABANDONED" in status_label or "CANCELLED" in status_label:
            desc_parts.append(
                "A cancelled or abandoned registration means the brand currently has "
                "NO federal trademark protection. Common law rights may still exist "
                "in limited geographies, but federal registration benefits are lost. "
                "Commission a new trademark search and file for re-registration. "
            )

        if gs_class:
            desc_parts.append(f"Goods/Services class: {str(gs_class)[:150]}. ")

        findings.append({
            "source_name": "USPTO Trademark",
            "source_type": "web_trademark",
            "severity": severity,
            "title": f"TRADEMARK: {mark_name[:50]} — {status_label} (Owner: {owner[:40]})",
            "description": "".join(desc_parts),
            "confidence": 0.80,
            "raw": r,
        })

    return findings
