"""
DFAT Consolidated Sanctions List screening service.

Downloads the public sanctions list from the Australian Department of Foreign
Affairs and Trade, caches it in the DB, and provides fast in-memory name
checking against every counterparty.

Source: https://www.dfat.gov.au/international-relations/security/sanctions/consolidated-list
Format: CSV (updated continuously by DFAT)

Matching tiers (applied in order, stops at first hit):
  1. Exact normalised match        — confidence 1.0
  2. Containment (name in string or vice versa, length-weighted)
                                   — confidence 0.90–0.95
  3. Fuzzy SequenceMatcher ratio  >= FUZZY_THRESHOLD
                                   — confidence = ratio

False-positive guards:
  - Names shorter than MIN_NAME_LEN chars are skipped.
  - Fuzzy threshold 0.88 (same as OFAC service).
  - Containment requires >= 70 % coverage.
"""

import csv
import io
import json
import logging
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher, get_close_matches

import httpx
from sqlalchemy.orm import Session

from ..models import DfatEntry

logger = logging.getLogger("verify.dfat")

DFAT_CSV_URL  = (
    "https://www.dfat.gov.au/sites/default/files/regulation/dfat-consolidated-list.csv"
)
FUZZY_THRESHOLD = 0.88
MIN_NAME_LEN    = 4
MAX_AGE_DAYS    = 7

# ── In-memory index ────────────────────────────────────────────────────────────

_index_lock    = threading.RLock()
_dfat_set    : set[str]              = set()
_dfat_meta   : dict[str, dict]       = {}
_dfat_by_char: dict[str, list[str]]  = defaultdict(list)


def _is_loaded() -> bool:
    return bool(_dfat_set)


# ── Normalisation ──────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", " ", s.upper())
    return re.sub(r"\s+", " ", s).strip()


# ── Index management ───────────────────────────────────────────────────────────

def load_index(db: Session) -> int:
    global _dfat_set, _dfat_meta, _dfat_by_char
    rows = db.query(DfatEntry).all()
    new_set   : set[str]             = set()
    new_meta  : dict[str, dict]      = {}
    new_by_char: dict[str, list[str]] = defaultdict(list)

    for r in rows:
        n = r.name
        if not n:
            continue
        new_set.add(n)
        new_meta[n] = {"entity_type": r.entity_type, "regimes": json.loads(r.regimes or "[]")}
        new_by_char[n[0]].append(n)

    with _index_lock:
        _dfat_set     = new_set
        _dfat_meta    = new_meta
        _dfat_by_char = new_by_char

    logger.info("DFAT index loaded: %d entries", len(new_set))
    return len(new_set)


# ── CSV parsing ────────────────────────────────────────────────────────────────

def _full_name(row: dict) -> str:
    """Construct a full name from DFAT CSV row, handling Individual vs Entity."""
    # Try multiple column name variants DFAT has used across different list versions
    last  = (row.get("Last Name") or row.get("lastname") or row.get("Name") or "").strip()
    given = (row.get("Given Names") or row.get("givennames") or row.get("Given Name") or "").strip()
    if given:
        return f"{given} {last}".strip()
    return last


def _regime(row: dict) -> str:
    return (
        row.get("Regime Name") or row.get("regime") or row.get("Regime") or ""
    ).strip()


def _entity_type(row: dict) -> str:
    return (
        row.get("Type") or row.get("type") or row.get("Entity Type") or ""
    ).strip()


def _ref_code(row: dict) -> str:
    return (
        row.get("Reference Code") or row.get("refcode") or row.get("Ref") or ""
    ).strip()


def _parse_csv(content: bytes) -> list[DfatEntry]:
    """Parse DFAT CSV bytes into DfatEntry objects (primary names + aliases)."""
    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    entries: list[DfatEntry] = []

    # DFAT occasionally ships the CSV with a UTF-8 BOM
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    # Group rows by Reference Code so we can collect all aliases per entity
    by_ref: dict[str, list[dict]] = defaultdict(list)
    for row in reader:
        ref = _ref_code(row)
        by_ref[ref if ref else f"__noref_{len(by_ref)}"].append(row)

    for ref, rows in by_ref.items():
        if not rows:
            continue

        primary_row = rows[0]
        entity_type = _entity_type(primary_row)
        regime      = _regime(primary_row)
        regimes_json = json.dumps([regime] if regime else [])

        # Primary name
        primary = _norm(_full_name(primary_row))
        if primary and len(primary) >= MIN_NAME_LEN and primary not in seen:
            seen.add(primary)
            entries.append(DfatEntry(
                name=primary, entity_type=entity_type, regimes=regimes_json,
                is_alias=False, reference_code=ref, cached_at=now,
            ))

        # Aliases — check the "Aliases" column on the primary row first
        aliases_field = (
            primary_row.get("Aliases") or primary_row.get("aliases") or ""
        ).strip()
        for alias_raw in re.split(r"[;|,]", aliases_field):
            alias = _norm(alias_raw.strip())
            if alias and len(alias) >= MIN_NAME_LEN and alias not in seen:
                seen.add(alias)
                entries.append(DfatEntry(
                    name=alias, entity_type=entity_type, regimes=regimes_json,
                    is_alias=True, reference_code=ref, cached_at=now,
                ))

        # Additional rows under the same reference code = alternate name entries
        for extra_row in rows[1:]:
            extra_name = _norm(_full_name(extra_row))
            if extra_name and len(extra_name) >= MIN_NAME_LEN and extra_name not in seen:
                seen.add(extra_name)
                entries.append(DfatEntry(
                    name=extra_name, entity_type=entity_type, regimes=regimes_json,
                    is_alias=True, reference_code=ref, cached_at=now,
                ))

    return entries


# ── Download & cache ───────────────────────────────────────────────────────────

def fetch_and_cache(db: Session) -> dict:
    """Download the DFAT Consolidated Sanctions List, replace DB cache, reload index."""
    logger.info("Downloading DFAT Consolidated Sanctions List …")
    try:
        r = httpx.get(DFAT_CSV_URL, timeout=90, follow_redirects=True)
        r.raise_for_status()
    except Exception as exc:
        logger.error("DFAT download failed: %s", exc)
        raise RuntimeError(f"DFAT download failed: {exc}") from exc

    entries = _parse_csv(r.content)
    if not entries:
        raise RuntimeError("DFAT CSV parsed to zero entries — file format may have changed")

    db.query(DfatEntry).delete(synchronize_session=False)
    for entry in entries:
        db.add(entry)
    db.commit()

    count = load_index(db)
    logger.info("DFAT cache refreshed — %d unique names", count)
    return {
        "entries":      count,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Age check ─────────────────────────────────────────────────────────────────

def needs_refresh(db: Session) -> bool:
    row = db.query(DfatEntry).first()
    if not row:
        return True
    age = (datetime.now(timezone.utc) - row.cached_at.replace(tzinfo=timezone.utc)).days
    return age >= MAX_AGE_DAYS


# ── Name check ────────────────────────────────────────────────────────────────

def check_name(name: str) -> dict | None:
    """
    Screen a merchant name against the in-memory DFAT index.
    Returns a match dict on hit, None otherwise.
    """
    if not _is_loaded() or not name:
        return None
    norm = _norm(name)
    if len(norm) < MIN_NAME_LEN:
        return None

    with _index_lock:
        dfat_set     = _dfat_set
        dfat_meta    = _dfat_meta
        dfat_by_char = _dfat_by_char

    # 1. Exact match
    if norm in dfat_set:
        m = dfat_meta[norm]
        return _hit(norm, norm, 1.0, m["entity_type"], m["regimes"])

    # 2. Containment
    for dfat_name in dfat_set:
        if len(dfat_name) < MIN_NAME_LEN:
            continue
        if dfat_name in norm:
            coverage = len(dfat_name) / max(len(norm), 1)
            if coverage >= 0.70:
                m = dfat_meta[dfat_name]
                return _hit(norm, dfat_name, min(0.95, 0.70 + coverage * 0.25), m["entity_type"], m["regimes"])
        elif norm in dfat_name:
            coverage = len(norm) / max(len(dfat_name), 1)
            if coverage >= 0.80:
                m = dfat_meta[dfat_name]
                return _hit(norm, dfat_name, min(0.92, coverage), m["entity_type"], m["regimes"])

    # 3. Fuzzy — first-character bucket only
    bucket = dfat_by_char.get(norm[0], [])
    if bucket:
        matches = get_close_matches(norm, bucket, n=1, cutoff=FUZZY_THRESHOLD)
        if matches:
            matched = matches[0]
            ratio   = SequenceMatcher(None, norm, matched).ratio()
            m = dfat_meta.get(matched, {})
            return _hit(norm, matched, ratio, m.get("entity_type", ""), m.get("regimes", []))

    return None


def _hit(queried: str, matched: str, confidence: float, entity_type: str, regimes: list) -> dict:
    return {
        "matched_name": matched,
        "queried_name": queried,
        "confidence":   round(confidence, 3),
        "entity_type":  entity_type,
        "regimes":      regimes,
        "list":         "DFAT",
    }


# ── Status ────────────────────────────────────────────────────────────────────

def status(db: Session) -> dict:
    count = db.query(DfatEntry).count()
    row   = db.query(DfatEntry).order_by(DfatEntry.cached_at.desc()).first()
    return {
        "entries":       count,
        "in_memory":     len(_dfat_set),
        "cached_at":     row.cached_at.isoformat() if row else None,
        "needs_refresh": needs_refresh(db),
    }
