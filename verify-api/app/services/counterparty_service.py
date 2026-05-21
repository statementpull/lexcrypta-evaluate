import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Counterparty, CounterpartyMatterLink
from .ofac_service import check_name as ofac_check

logger = logging.getLogger("verify.counterparty")


def upsert_counterparties(db: Session, matter_id: int, transactions: list) -> None:
    """Register every merchant from a completed analysis into the cross-matter library."""
    groups: dict[str, dict] = {}
    for t in transactions:
        name = (t.get("merchant") or "").strip().upper()
        if not name or name == "PDF METADATA INTEGRITY FLAG":
            continue
        if name not in groups:
            groups[name] = {"count": 0, "volume": 0.0, "dates": []}
        groups[name]["count"] += 1
        groups[name]["volume"] += abs(t.get("amount", 0.0))
        d = t.get("transaction_date", "")
        if d:
            groups[name]["dates"].append(d)

    for name, data in groups.items():
        dates = sorted(data["dates"])
        first = dates[0] if dates else ""
        last  = dates[-1] if dates else ""

        cp = db.query(Counterparty).filter_by(name=name).first()
        if not cp:
            cp = Counterparty(name=name, matter_count=0, transaction_count=0, total_volume=0.0,
                               first_seen=first, last_seen=last)
            db.add(cp)
            db.flush()  # get id

        cp.transaction_count += data["count"]
        cp.total_volume      += data["volume"]
        cp.updated_at         = datetime.now(timezone.utc)
        if first and (not cp.first_seen or first < cp.first_seen):
            cp.first_seen = first
        if last and (not cp.last_seen or last > cp.last_seen):
            cp.last_seen = last

        # OFAC SDN screening — only runs if index is loaded
        if cp.severity != "red":              # don't downgrade an existing red flag
            try:
                hit = ofac_check(name)
                if hit:
                    cp.severity = "red"
                    tags = json.loads(cp.tags or "[]")
                    if "ofac_sdn" not in tags:
                        tags.append("ofac_sdn")
                    cp.tags  = json.dumps(tags)
                    cp.notes = (
                        f"OFAC SDN MATCH — {hit['matched_name']} "
                        f"| type: {hit['sdn_type']} "
                        f"| programs: {', '.join(hit['programs'])} "
                        f"| confidence: {hit['confidence']:.0%}"
                    )
                    logger.warning(
                        "OFAC SDN match: merchant=%s sdn=%s conf=%.2f programs=%s",
                        name, hit["matched_name"], hit["confidence"], hit["programs"],
                    )
            except Exception:
                logger.exception("OFAC check failed for %s — skipping", name)

        link = db.query(CounterpartyMatterLink).filter_by(
            counterparty_id=cp.id, matter_id=matter_id
        ).first()
        if not link:
            link = CounterpartyMatterLink(
                counterparty_id=cp.id, matter_id=matter_id,
                transaction_count=0, total_volume=0.0
            )
            db.add(link)
            cp.matter_count += 1
        link.transaction_count += data["count"]
        link.total_volume      += data["volume"]

    db.commit()


def enrich_signals(db: Session, signals: list) -> list:
    """Add known_entity fields to any signal whose merchant is already in the library."""
    enriched = []
    for sig in signals:
        name = (sig.get("merchant") or "").strip().upper()
        if name:
            cp = db.query(Counterparty).filter_by(name=name).first()
            if cp and cp.matter_count > 0:
                sig = dict(sig)
                sig["known_entity"]        = True
                sig["entity_matter_count"] = cp.matter_count
                sig["entity_severity"]     = cp.severity
        enriched.append(sig)
    return enriched


def get_counterparty(db: Session, name: str) -> dict | None:
    cp = db.query(Counterparty).filter_by(name=name.strip().upper()).first()
    return _to_dict(cp) if cp else None


def list_counterparties(
    db: Session,
    severity: str | None = None,
    min_matters: int = 1,
    limit: int = 200,
) -> list[dict]:
    q = db.query(Counterparty).filter(Counterparty.matter_count >= min_matters)
    if severity:
        q = q.filter_by(severity=severity)
    q = q.order_by(Counterparty.matter_count.desc(), Counterparty.total_volume.desc()).limit(limit)
    return [_to_dict(cp) for cp in q.all()]


def tag_counterparty(
    db: Session,
    name: str,
    severity: str | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
) -> dict | None:
    cp = db.query(Counterparty).filter_by(name=name.strip().upper()).first()
    if not cp:
        return None
    if severity is not None:
        cp.severity = severity
    if tags is not None:
        cp.tags = json.dumps(tags)
    if notes is not None:
        cp.notes = notes
    cp.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _to_dict(cp)


def _to_dict(cp: Counterparty) -> dict:
    return {
        "name":              cp.name,
        "matter_count":      cp.matter_count,
        "transaction_count": cp.transaction_count,
        "total_volume":      round(cp.total_volume, 2),
        "first_seen":        cp.first_seen or "",
        "last_seen":         cp.last_seen or "",
        "category":          cp.category or "",
        "severity":          cp.severity or "none",
        "tags":              json.loads(cp.tags or "[]"),
        "notes":             cp.notes or "",
    }
