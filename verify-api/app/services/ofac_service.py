"""
OFAC SDN (Specially Designated Nationals) screening service.

Downloads the public SDN list from the US Treasury, caches it in the DB,
and provides fast in-memory name checking against every counterparty.

Matching tiers (applied in order, stops at first hit):
  1. Exact normalised match        — confidence 1.0
  2. SDN name contained in merchant string (or vice versa, length-weighted)
                                   — confidence 0.90–0.95
  3. Fuzzy SequenceMatcher ratio  >= FUZZY_THRESHOLD
                                   — confidence = ratio

False-positive guards:
  - Names shorter than MIN_NAME_LEN chars are skipped entirely.
  - Fuzzy matches require ratio >= 0.88.
  - Containment matches require the SDN term to cover >= 70% of the query.
"""

import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher, get_close_matches

import httpx
from sqlalchemy.orm import Session

from ..models import SdnEntry

logger = logging.getLogger("verify.ofac")

SDN_XML_URL = (
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
)
FUZZY_THRESHOLD = 0.88
MIN_NAME_LEN    = 4
MAX_AGE_DAYS    = 7

# ── In-memory index ────────────────────────────────────────────────────────────

_index_lock   = threading.RLock()
_sdn_set: set[str]                              = set()          # exact lookup O(1)
_sdn_meta: dict[str, dict]                     = {}             # name -> {sdn_type, programs}
_sdn_by_char: dict[str, list[str]]             = defaultdict(list)  # first-char bucket for fuzzy
_index_loaded_at: datetime | None              = None


def _is_loaded() -> bool:
    return bool(_sdn_set)


# ── Normalisation ──────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Uppercase, strip punctuation, collapse whitespace."""
    s = re.sub(r"[^\w\s]", " ", s.upper())
    return re.sub(r"\s+", " ", s).strip()


# ── Index management ───────────────────────────────────────────────────────────

def load_index(db: Session) -> int:
    """Load SDN rows from DB into the in-memory index. Returns count."""
    global _sdn_set, _sdn_meta, _sdn_by_char, _index_loaded_at
    rows = db.query(SdnEntry).all()
    new_set   : set[str]             = set()
    new_meta  : dict[str, dict]      = {}
    new_by_char: dict[str, list[str]] = defaultdict(list)

    for r in rows:
        n = r.name
        if not n:
            continue
        new_set.add(n)
        new_meta[n] = {"sdn_type": r.sdn_type, "programs": json.loads(r.programs or "[]")}
        new_by_char[n[0]].append(n)

    with _index_lock:
        _sdn_set      = new_set
        _sdn_meta     = new_meta
        _sdn_by_char  = new_by_char
        _index_loaded_at = datetime.now(timezone.utc)

    logger.info("OFAC index loaded: %d entries", len(new_set))
    return len(new_set)


# ── Download & parse ───────────────────────────────────────────────────────────

def _strip_ns(root: ET.Element) -> None:
    """Remove all XML namespace prefixes from element tags in-place."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def fetch_and_cache(db: Session) -> dict:
    """
    Download the OFAC SDN XML, parse primary names + all aliases,
    replace the DB cache, and reload the in-memory index.
    """
    logger.info("Downloading OFAC SDN list …")
    try:
        r = httpx.get(SDN_XML_URL, timeout=90, follow_redirects=True)
        r.raise_for_status()
    except Exception as exc:
        logger.error("OFAC download failed: %s", exc)
        raise RuntimeError(f"OFAC download failed: {exc}") from exc

    root = ET.fromstring(r.content)
    _strip_ns(root)

    def _name(el: ET.Element) -> str:
        first = (el.findtext("firstName") or "").strip()
        last  = (el.findtext("lastName")  or "").strip()
        return f"{first} {last}".strip() if first else last

    now  = datetime.now(timezone.utc)
    seen: set[str] = set()
    rows: list[SdnEntry] = []

    for entry in root.findall("sdnEntry"):
        uid_txt  = (entry.findtext("uid") or "").strip()
        uid      = int(uid_txt) if uid_txt.isdigit() else None
        sdn_type = (entry.findtext("sdnType") or "").strip()
        programs = [
            p.text.strip()
            for p in entry.findall(".//program")
            if p.text and p.text.strip()
        ]
        prg_json = json.dumps(programs)

        # Primary name
        primary = _norm(_name(entry))
        if primary and len(primary) >= MIN_NAME_LEN and primary not in seen:
            seen.add(primary)
            rows.append(SdnEntry(
                name=primary, sdn_type=sdn_type, programs=prg_json,
                is_alias=False, source_uid=uid, cached_at=now,
            ))

        # Aliases
        for aka in entry.findall(".//aka"):
            aka_name = _norm(_name(aka))
            if aka_name and len(aka_name) >= MIN_NAME_LEN and aka_name not in seen:
                seen.add(aka_name)
                rows.append(SdnEntry(
                    name=aka_name, sdn_type=sdn_type, programs=prg_json,
                    is_alias=True, source_uid=uid, cached_at=now,
                ))

    # Atomically replace the cache
    db.query(SdnEntry).delete(synchronize_session=False)
    for row in rows:
        db.add(row)
    db.commit()

    count = load_index(db)
    logger.info("OFAC SDN cache refreshed — %d unique names", count)
    return {
        "entries":      count,
        "refreshed_at": now.isoformat(),
    }


# ── Age check ─────────────────────────────────────────────────────────────────

def needs_refresh(db: Session) -> bool:
    row = db.query(SdnEntry).first()
    if not row:
        return True
    age = (datetime.now(timezone.utc) - row.cached_at.replace(tzinfo=timezone.utc)).days
    return age >= MAX_AGE_DAYS


# ── Name check ────────────────────────────────────────────────────────────────

def check_name(name: str) -> dict | None:
    """
    Screen a merchant name against the in-memory SDN index.
    Returns a match dict on hit, None otherwise.
    """
    if not _is_loaded() or not name:
        return None
    norm = _norm(name)
    if len(norm) < MIN_NAME_LEN:
        return None

    with _index_lock:
        sdn_set     = _sdn_set
        sdn_meta    = _sdn_meta
        sdn_by_char = _sdn_by_char

    # 1. Exact match — O(1)
    if norm in sdn_set:
        m = sdn_meta[norm]
        return _hit(norm, norm, 1.0, m["sdn_type"], m["programs"])

    # 2. Containment — SDN name is a substring of the merchant string (or vice versa)
    for sdn_name in sdn_set:
        if len(sdn_name) < MIN_NAME_LEN:
            continue
        if sdn_name in norm:
            # SDN term found inside merchant string
            coverage = len(sdn_name) / max(len(norm), 1)
            if coverage >= 0.70:
                m = sdn_meta[sdn_name]
                return _hit(norm, sdn_name, min(0.95, 0.70 + coverage * 0.25), m["sdn_type"], m["programs"])
        elif norm in sdn_name:
            coverage = len(norm) / max(len(sdn_name), 1)
            if coverage >= 0.80:
                m = sdn_meta[sdn_name]
                return _hit(norm, sdn_name, min(0.92, coverage), m["sdn_type"], m["programs"])

    # 3. Fuzzy match — only within same first-character bucket
    bucket = sdn_by_char.get(norm[0], [])
    if bucket:
        matches = get_close_matches(norm, bucket, n=1, cutoff=FUZZY_THRESHOLD)
        if matches:
            matched = matches[0]
            ratio   = SequenceMatcher(None, norm, matched).ratio()
            m = sdn_meta.get(matched, {})
            return _hit(norm, matched, ratio, m.get("sdn_type", ""), m.get("programs", []))

    return None


def _hit(queried: str, matched: str, confidence: float, sdn_type: str, programs: list) -> dict:
    return {
        "matched_name": matched,
        "queried_name": queried,
        "confidence":   round(confidence, 3),
        "sdn_type":     sdn_type,
        "programs":     programs,
    }


# ── Status ────────────────────────────────────────────────────────────────────

def status(db: Session) -> dict:
    count = db.query(SdnEntry).count()
    row   = db.query(SdnEntry).order_by(SdnEntry.cached_at.desc()).first()
    return {
        "entries":       count,
        "in_memory":     len(_sdn_set),
        "cached_at":     row.cached_at.isoformat() if row else None,
        "needs_refresh": needs_refresh(db),
    }
