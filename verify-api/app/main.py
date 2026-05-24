import base64
import hashlib
import hmac as _hmac
import io
import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("verify")

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import DEMO_KEY, LICENSE_SECRET, MAX_CSV_MB, MAX_PDF_MB
from .database import Base, create_verify_schema, engine, get_db
from .intelligence.signals import build_verify_result, run_signals
from .models import AnalysisResult, Counterparty, Document, License, Matter, TransactionRevision
from .parsers.bank_parser import parse_bank_csv_text, parse_bank_pdf
from .parsers.file_detector import detect_file_type
from .parsers.security_scanner import scan_pdf_bytes, validate_pdf_header
from .seed import seed_demo_data
from .services.counterparty_service import (
    enrich_signals,
    get_counterparty,
    list_counterparties,
    tag_counterparty,
    upsert_counterparties,
)
from .services import ofac_service, dfat_service

# In-memory progress store — keyed by matter_id
_analysis_progress: dict = {}


def _txn_hash(t: dict) -> str:
    key = f"{t.get('date','')}|{t.get('merchant','')}|{t.get('amount','')}|{t.get('direction','')}"
    return hashlib.md5(key.encode()).hexdigest()


# ── License ───────────────────────────────────────────────────────────────────

class LicenseRequest(BaseModel):
    key: str


class RevisionRequest(BaseModel):
    txn_hash: str
    rev_type: str          # correct | annotate | reclassify
    field: str = ""
    orig_value: str = ""
    new_value: str = ""
    note: str = ""
    signal_override: str = ""
    severity_override: str = ""
    is_false_positive: bool = False
    analyst_id: str = ""


def _validate_license_key(key: str) -> bool:
    if DEMO_KEY and key.strip().upper() == DEMO_KEY.upper():
        return True
    secret = os.getenv("LICENSE_SECRET", "")
    if not secret:
        return False
    parts = key.strip().upper().split("-")
    if len(parts) != 4 or parts[0] != "LEXV":
        return False
    payload = f"LEXV-{parts[1]}-{parts[2]}"
    h = _hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    expected = base64.b32encode(h).decode()[:8].upper()
    return _hmac.compare_digest(expected, parts[3])


def require_license(db: Session = Depends(get_db)):
    if not db.query(License).first():
        raise HTTPException(status_code=403, detail="License not activated.")
    return True


# ── Schema migrations ─────────────────────────────────────────────────────────

def _run_migrations():
    from sqlalchemy import text as _text
    _migrations = [
        "ALTER TABLE verify.matters ADD COLUMN IF NOT EXISTS report_tier VARCHAR(20) DEFAULT 'trustee'",
        "ALTER TABLE verify.matters ADD COLUMN IF NOT EXISTS debtor_name VARCHAR(200) DEFAULT ''",
        "ALTER TABLE verify.matters ADD COLUMN IF NOT EXISTS case_number VARCHAR(100) DEFAULT ''",
        "ALTER TABLE verify.matters ADD COLUMN IF NOT EXISTS jurisdiction VARCHAR(20) DEFAULT 'US'",
        """CREATE TABLE IF NOT EXISTS verify.transaction_revisions (
            id SERIAL PRIMARY KEY,
            matter_id INTEGER NOT NULL,
            txn_hash VARCHAR(64) NOT NULL,
            rev_type VARCHAR(20) NOT NULL,
            field VARCHAR(50) DEFAULT '',
            orig_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            note TEXT DEFAULT '',
            signal_override VARCHAR(100) DEFAULT '',
            severity_override VARCHAR(10) DEFAULT '',
            is_false_positive BOOLEAN DEFAULT FALSE,
            analyst_id VARCHAR(100) DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ]
    try:
        with engine.connect() as conn:
            for sql in _migrations:
                conn.execute(_text(sql))
            conn.commit()
        logger.info("Schema migrations applied successfully")
    except Exception:
        logger.exception("Schema migration failed — some features may be unavailable")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="LexCrypta Verify", version="1.0.0")

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.on_event("startup")
def startup():
    try:
        create_verify_schema()
        Base.metadata.create_all(bind=engine)
        _run_migrations()
    except Exception:
        logger.exception("Database initialisation failed — check DATABASE_URL")
        raise

    try:
        db = next(get_db())
        try:
            seed_demo_data(db)
        finally:
            db.close()
    except Exception:
        logger.exception("Demo seed failed — continuing without demo data")

    # OFAC SDN index — load from cache or fetch fresh in background
    def _init_ofac():
        db = next(get_db())
        try:
            if ofac_service.needs_refresh(db):
                logger.info("OFAC SDN cache stale or empty — fetching in background …")
                ofac_service.fetch_and_cache(db)
            else:
                count = ofac_service.load_index(db)
                logger.info("OFAC SDN index loaded from cache: %d entries", count)
        except Exception:
            logger.exception("OFAC initialisation failed — SDN screening will be unavailable until /admin/sync-ofac is called")
        finally:
            db.close()

    threading.Thread(target=_init_ofac, daemon=True, name="ofac-init").start()

    # DFAT Consolidated Sanctions List — load from cache or fetch fresh in background
    def _init_dfat():
        db = next(get_db())
        try:
            if dfat_service.needs_refresh(db):
                logger.info("DFAT sanctions cache stale or empty — fetching in background …")
                dfat_service.fetch_and_cache(db)
            else:
                count = dfat_service.load_index(db)
                logger.info("DFAT sanctions index loaded from cache: %d entries", count)
        except Exception:
            logger.exception("DFAT initialisation failed — AU sanctions screening will be unavailable until /admin/sync-dfat is called")
        finally:
            db.close()

    threading.Thread(target=_init_dfat, daemon=True, name="dfat-init").start()


# ── Health / Version ──────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"version": "v2026.05", "libraries": 8, "signals": 20, "product": "LexCrypta Verify", "ofac_screening": True, "dfat_screening": True}


# ── License endpoints ─────────────────────────────────────────────────────────

@app.get("/license-status")
def license_status(db: Session = Depends(get_db)):
    return {"activated": db.query(License).first() is not None}


@app.post("/activate-license")
def activate_license(req: LicenseRequest, db: Session = Depends(get_db)):
    if not _validate_license_key(req.key):
        raise HTTPException(status_code=400, detail="Invalid license key.")
    if not db.query(License).first():
        db.add(License(key_hash=hashlib.sha256(req.key.encode()).hexdigest()))
        db.commit()
    return {"activated": True}


# ── Matters ───────────────────────────────────────────────────────────────────

def _matter_to_dict(m: Matter) -> dict:
    return {
        "id": m.id,
        "subject": m.subject,
        "ref": m.ref,
        "type": m.type,
        "type_label": m.type_label,
        "matter_date": m.matter_date or "—",
        "assigned_to": m.assigned_to or "—",
        "notes": m.notes or "",
        "exposure": m.exposure,
        "att": m.att,
        "att_flag": m.att_flag or "",
        "last_run": m.last_run or "—",
        "doc_count": m.doc_count,
        "analysed": m.analysed,
        "report_tier": m.report_tier or "trustee",
        "debtor_name": m.debtor_name or "",
        "case_number": m.case_number or "",
        "jurisdiction": m.jurisdiction or "US",
        "las": {
            "score": m.las_score,
            "verdict": m.las_verdict,
            "verdict_cls": m.las_verdict_cls,
            "reason": m.las_reason,
        } if m.las_score is not None else None,
    }


@app.post("/matters")
def create_matter(
    subject: str = Form(...),
    ref: str = Form(None),
    type: str = Form("civil"),
    matter_date: str = Form(""),
    assigned_to: str = Form(""),
    notes: str = Form(""),
    report_tier: str = Form("trustee"),
    debtor_name: str = Form(""),
    case_number: str = Form(""),
    jurisdiction: str = Form("US"),
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    type_labels = {
        "bankruptcy": "Bankruptcy",
        "family_law": "Family Law",
        "civil": "Civil / Commercial",
    }
    if not ref:
        ref = f"VRF-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    m = Matter(
        subject=subject,
        ref=ref,
        type=type,
        type_label=type_labels.get(type, "Civil / Commercial"),
        matter_date=matter_date,
        assigned_to=assigned_to,
        notes=notes,
        report_tier=report_tier,
        debtor_name=debtor_name,
        case_number=case_number,
        jurisdiction=jurisdiction,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _matter_to_dict(m)


@app.get("/matters")
def list_matters(
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    matters = db.query(Matter).order_by(Matter.id.desc()).all()
    return [_matter_to_dict(m) for m in matters]


@app.delete("/matters/{matter_id}/purge")
def purge_matter(
    matter_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    m = db.query(Matter).filter(Matter.id == matter_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Matter not found.")
    db.delete(m)
    db.commit()
    return {"purged": True}


# ── Upload ────────────────────────────────────────────────────────────────────

@app.post("/matters/{matter_id}/upload")
async def upload_documents(
    matter_id: int,
    files: list[UploadFile] = File(...),
    zone: str = Form("bank"),
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    m = db.query(Matter).filter(Matter.id == matter_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Matter not found.")
    _ALLOWED = {"bank_pdf", "pdf_financial_report", "bank_csv", "myob_gl", "myob_pl",
                "xlsx", "quickbooks_pl", "balance_sheet", "aged_debtors",
                "inventory", "customer_sales", "csv_unknown"}
    file_ids = []
    for f in files:
        content = await f.read()
        detected = detect_file_type(io.BytesIO(content), f.filename)
        if detected == "unknown":
            raise HTTPException(
                status_code=400,
                detail=f"{f.filename}: unsupported file type. Upload PDF, CSV, or XLSX.",
            )
        mb = len(content) / (1024 * 1024)
        limit = MAX_PDF_MB if detected in ("bank_pdf", "pdf_financial_report") else MAX_CSV_MB
        if mb > limit:
            raise HTTPException(status_code=413, detail=f"{f.filename} exceeds {limit}MB limit.")
        if detected in ("bank_pdf", "pdf_financial_report"):
            hdr_err = validate_pdf_header(content, f.filename)
            if hdr_err:
                raise HTTPException(status_code=400, detail=hdr_err)
            threat = scan_pdf_bytes(content, f.filename)
            if threat:
                logger.warning("Security threat in upload: %s — %s",
                               f.filename, threat["meta"]["threats_found"])
                raise HTTPException(
                    status_code=400,
                    detail=f"{f.filename}: security threat detected — {'; '.join(threat['meta']['threats_found'])}. File rejected.",
                )
        doc = Document(matter_id=matter_id, filename=f.filename, zone=zone, content=content)
        db.add(doc)
        db.flush()
        file_ids.append(doc.id)
    m.doc_count = db.query(Document).filter(Document.matter_id == matter_id).count()
    db.commit()
    return {"uploaded": len(files), "file_ids": file_ids}


# ── Run Analysis ──────────────────────────────────────────────────────────────

def _trustee_reason(las: dict) -> str:
    """Return the engine-generated reason string for trustee PURSUE/SKIP bullets.
    las["reason"] is already ' · '-delimited — the frontend splits it into bullet points."""
    return las.get("reason") or "Review flagged transactions for full detail."


@app.post("/matters/{matter_id}/run")
async def run_analysis(
    matter_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    m = db.query(Matter).filter(Matter.id == matter_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Matter not found.")

    # If seeded demo matter with pre-computed result, return it directly
    existing = db.query(AnalysisResult).filter(AnalysisResult.matter_id == matter_id).first()
    if existing and m.analysed:
        return json.loads(existing.result_json)

    # Run engine on uploaded documents
    docs = db.query(Document).filter(Document.matter_id == matter_id).all()
    if not docs:
        raise HTTPException(
            status_code=400,
            detail="No documents uploaded. Upload bank statements first.",
        )

    _analysis_progress[matter_id] = {
        "stage": f"LOADING {len(docs)} FILES",
        "file_index": 0, "file_total": len(docs),
        "txn_count": 0, "signals": [], "done": False
    }

    transactions = []
    parse_errors = []
    for idx, doc in enumerate(docs, start=1):
        _analysis_progress[matter_id].update({
            "stage": f"READING FILE {idx} OF {len(docs)}: {doc.filename}",
            "file_index": idx,
        })
        try:
            raw = bytes(doc.content)  # memoryview → bytes for pdfplumber
            if doc.filename.lower().endswith(".pdf"):
                txns = parse_bank_pdf(raw, doc.filename)
            else:
                txns = parse_bank_csv_text(raw.decode("utf-8", errors="replace"))
            transactions.extend(txns)
            _analysis_progress[matter_id]["txn_count"] += len(txns)
        except Exception as e:
            logger.exception("Parse failed for %s: %s", doc.filename, e)
            parse_errors.append(doc.filename)

    # Pull document-level integrity signals out of transactions before running engine
    doc_signals = [t for t in transactions if t.get("signal_type") == "document_integrity"]
    transactions = [t for t in transactions if t.get("signal_type") != "document_integrity"]

    _analysis_progress[matter_id]["stage"] = "RUNNING SIGNALS..."
    raw_signals = run_signals(transactions) + doc_signals
    raw_signals = enrich_signals(db, raw_signals)
    result = build_verify_result(
        matter_id=matter_id,
        raw_signals=raw_signals,
        transactions=transactions,
    )
    if parse_errors:
        result["parse_errors"] = parse_errors
    result["transactions_parsed"] = len(transactions)

    las = result["las"]
    m.las_score = las["score"]
    m.las_verdict = las["verdict"]
    m.las_verdict_cls = las["verdict_cls"]
    m.las_reason = las["reason"]
    m.exposure = result["exposure"]
    m.att = result["att"]
    m.att_flag = result["att_flag"]
    m.analysed = True
    m.last_run = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M")

    if m.report_tier == "trustee":
        if (m.las_score or 0) >= 50:
            m.las_verdict     = "PURSUE"
            m.las_verdict_cls = "high"
        else:
            m.las_verdict     = "SKIP"
            m.las_verdict_cls = "low"
        m.las_reason = _trustee_reason(las)

    if existing:
        existing.result_json = json.dumps(result)
    else:
        db.add(AnalysisResult(matter_id=matter_id, result_json=json.dumps(result)))

    db.commit()

    # Register all merchants into the cross-matter counterparty library
    upsert_counterparties(db, matter_id, transactions)

    _analysis_progress[matter_id]["done"] = True

    return result


# ── Results ───────────────────────────────────────────────────────────────────

@app.get("/matters/{matter_id}/results")
def get_results(
    matter_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    ar = db.query(AnalysisResult).filter(AnalysisResult.matter_id == matter_id).first()
    if not ar:
        raise HTTPException(status_code=404, detail="No results yet. Run analysis first.")
    return json.loads(ar.result_json)


@app.get("/matters/{matter_id}/progress")
def get_analysis_progress(matter_id: int):
    p = _analysis_progress.get(matter_id)
    if not p:
        return {"stage": "idle", "file_index": 0, "file_total": 0,
                "txn_count": 0, "signals": [], "done": True}
    return p


# ── Revisions ─────────────────────────────────────────────────────────────────

@app.post("/matters/{matter_id}/revisions")
def add_revision(matter_id: int, req: RevisionRequest,
                 db: Session = Depends(get_db),
                 _lic = Depends(require_license)):
    rev = TransactionRevision(
        matter_id         = matter_id,
        txn_hash          = req.txn_hash,
        rev_type          = req.rev_type,
        field             = req.field,
        orig_value        = req.orig_value,
        new_value         = req.new_value,
        note              = req.note,
        signal_override   = req.signal_override,
        severity_override = req.severity_override,
        is_false_positive = req.is_false_positive,
        analyst_id        = req.analyst_id,
    )
    db.add(rev)
    db.commit()
    db.refresh(rev)
    return {"id": rev.id, "txn_hash": rev.txn_hash, "rev_type": rev.rev_type}


@app.get("/matters/{matter_id}/revisions")
def get_revisions(matter_id: int, db: Session = Depends(get_db),
                  _lic = Depends(require_license)):
    revs = db.query(TransactionRevision).filter_by(matter_id=matter_id).all()
    return [{"id": r.id, "txn_hash": r.txn_hash, "rev_type": r.rev_type,
             "field": r.field, "orig_value": r.orig_value, "new_value": r.new_value,
             "note": r.note, "signal_override": r.signal_override,
             "severity_override": r.severity_override,
             "is_false_positive": r.is_false_positive,
             "analyst_id": r.analyst_id,
             "created_at": r.created_at.isoformat() if r.created_at else ""} for r in revs]


@app.delete("/matters/{matter_id}/revisions/{rev_id}")
def delete_revision(matter_id: int, rev_id: int, db: Session = Depends(get_db),
                    _lic = Depends(require_license)):
    rev = db.query(TransactionRevision).filter_by(id=rev_id, matter_id=matter_id).first()
    if not rev:
        raise HTTPException(status_code=404, detail="Revision not found")
    db.delete(rev)
    db.commit()
    return {"deleted": rev_id}


# ── Documents ─────────────────────────────────────────────────────────────────

@app.get("/matters/{matter_id}/documents/{doc_id}")
def serve_document(matter_id: int, doc_id: int, db: Session = Depends(get_db),
                   _lic = Depends(require_license)):
    doc = db.query(Document).filter_by(id=doc_id, matter_id=matter_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(content=bytes(doc.content), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc.filename}"'})


# ── Demo ─────────────────────────────────────────────────────────────────────

@app.post("/demo/analyse")
async def demo_analyse(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Public demo endpoint — no license required. One or more PDFs in, full result out."""
    if not files:
        raise HTTPException(status_code=400, detail="No files received.")

    all_transactions, doc_signals, parse_errors = [], [], []

    _analysis_progress[0] = {
        "stage": f"LOADING {len(files)} FILES",
        "file_index": 0, "file_total": len(files),
        "txn_count": 0, "signals": [], "done": False
    }

    for idx, file in enumerate(files, start=1):
        _analysis_progress[0].update({
            "stage": f"READING FILE {idx} OF {len(files)}: {file.filename}",
            "file_index": idx,
        })
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{file.filename}: only PDF files are accepted.")
        content = await file.read()
        if len(content) / (1024 * 1024) > MAX_PDF_MB:
            raise HTTPException(status_code=413, detail=f"{file.filename} exceeds {MAX_PDF_MB} MB limit.")
        hdr_err = validate_pdf_header(content, file.filename)
        if hdr_err:
            raise HTTPException(status_code=400, detail=hdr_err)
        threat = scan_pdf_bytes(content, file.filename)
        if threat:
            logger.warning("Security threat in demo upload: %s — %s",
                           file.filename, threat["meta"]["threats_found"])
            raise HTTPException(
                status_code=400,
                detail=f"{file.filename}: security threat detected — {'; '.join(threat['meta']['threats_found'])}. File rejected.",
            )
        try:
            txns = parse_bank_pdf(content, file.filename)
            doc_signals += [t for t in txns if t.get("signal_type") == "document_integrity"]
            all_transactions += [t for t in txns if t.get("signal_type") != "document_integrity"]
            _analysis_progress[0]["txn_count"] += len(
                [t for t in txns if t.get("signal_type") != "document_integrity"]
            )
        except Exception as e:
            logger.exception("Demo parse failed for %s: %s", file.filename, e)
            parse_errors.append(file.filename)

    if not all_transactions:
        msg = "No transactions found across the uploaded files."
        if parse_errors:
            msg += f" Parse errors: {', '.join(parse_errors)}."
        raise HTTPException(status_code=422, detail=msg)

    ref = f"DEMO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    subject = (
        files[0].filename.replace(".pdf", "").replace("_", " ").replace("-", " ").title()
        if len(files) == 1
        else f"{len(files)} Statements"
    )
    m = Matter(
        subject=subject or "Demo Analysis",
        ref=ref,
        type="civil",
        type_label="Demo Analysis",
        matter_date="",
        assigned_to="Demo",
        notes=f"Auto-created via demo upload. {len(files)} file(s).",
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    _analysis_progress[0]["stage"] = "RUNNING SIGNALS..."
    raw_signals = run_signals(all_transactions) + doc_signals
    raw_signals = enrich_signals(db, raw_signals)
    result = build_verify_result(matter_id=m.id, raw_signals=raw_signals, transactions=all_transactions)
    result["transactions_parsed"] = len(all_transactions)
    if parse_errors:
        result["parse_errors"] = parse_errors

    las = result["las"]
    m.las_score       = las["score"]
    m.las_verdict     = las["verdict"]
    m.las_verdict_cls = las["verdict_cls"]
    m.las_reason      = las["reason"]
    m.exposure        = result["exposure"]
    m.att             = result["att"]
    m.att_flag        = result["att_flag"]
    m.analysed        = True
    m.last_run        = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M")
    m.doc_count       = len(files)

    if m.report_tier == "trustee":
        if (m.las_score or 0) >= 50:
            m.las_verdict     = "PURSUE"
            m.las_verdict_cls = "high"
        else:
            m.las_verdict     = "SKIP"
            m.las_verdict_cls = "low"
        m.las_reason = _trustee_reason(las)

    db.add(AnalysisResult(matter_id=m.id, result_json=json.dumps(result)))
    db.commit()

    # Register all merchants into the cross-matter counterparty library
    upsert_counterparties(db, m.id, all_transactions)

    _analysis_progress[0]["done"] = True

    result["report_url"] = f"/reports/{m.id}"
    return result


# ── Counterparty Library ──────────────────────────────────────────────────────

@app.get("/counterparties")
def get_counterparties(
    severity: str = None,
    min_matters: int = 1,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    return list_counterparties(db, severity=severity, min_matters=min_matters, limit=limit)


@app.get("/counterparties/{name}")
def get_counterparty_detail(
    name: str,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    result = get_counterparty(db, name)
    if not result:
        raise HTTPException(status_code=404, detail="Counterparty not found.")
    return result


class CounterpartyTagRequest(BaseModel):
    severity: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


@app.patch("/counterparties/{name}")
def patch_counterparty(
    name: str,
    req: CounterpartyTagRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    result = tag_counterparty(db, name, severity=req.severity, tags=req.tags, notes=req.notes)
    if not result:
        raise HTTPException(status_code=404, detail="Counterparty not found.")
    return result


# ── OFAC ──────────────────────────────────────────────────────────────────────

@app.get("/ofac/status")
def get_ofac_status(
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    return ofac_service.status(db)


@app.post("/admin/sync-ofac")
def sync_ofac(
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    """Force a fresh download of the OFAC SDN list and rebuild the index."""
    try:
        return ofac_service.fetch_and_cache(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── DFAT ──────────────────────────────────────────────────────────────────────

@app.get("/dfat/status")
def get_dfat_status(
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    return dfat_service.status(db)


@app.post("/admin/sync-dfat")
def sync_dfat(
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    """Force a fresh download of the DFAT Consolidated Sanctions List and rebuild the index."""
    try:
        return dfat_service.fetch_and_cache(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Reports ───────────────────────────────────────────────────────────────────

def _brief_cashflow(cs: dict) -> str:
    if not cs:
        return ""
    ic = f"${cs.get('total_credits', 0):,.0f}"
    oc = f"${cs.get('total_debits', 0):,.0f}"
    return (
        f'<div style="margin-top:6px;font-size:10px;color:var(--muted)">'
        f'Inflows: <b style="color:#f2ede4">{ic}</b> &nbsp; '
        f'Outflows: <b style="color:#f2ede4">{oc}</b></div>'
    )


def _report_base_css() -> str:
    return """
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500&family=IBM+Plex+Mono&display=swap');
    :root{--navy:#0e1c2e;--navy2:#152336;--navy3:#1c2f44;--gold:#c8963e;--cream:#f2ede4;--text:#ccd6e8;--muted:#8a9bb4;--red:#c0392b;--green:#2e7d52;--border:#243650}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'IBM Plex Sans',Arial,sans-serif;background:var(--navy);color:var(--text);font-size:12px;line-height:1.6}
    .page{max-width:860px;margin:0 auto;padding:48px 40px}
    h2{font-family:Georgia,serif;color:var(--gold);font-weight:300;font-size:13px;letter-spacing:.18em;text-transform:uppercase;margin:28px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
    .print-btn{position:fixed;top:20px;right:20px;background:var(--gold);color:var(--navy);border:none;padding:9px 20px;font-size:10px;letter-spacing:.18em;text-transform:uppercase;cursor:pointer;font-weight:500}
    """


def _report_brief(m, result: dict) -> str:
    """Lawyer Brief — key findings only, no signal table or monthly data."""
    las   = result.get("las", {})
    intel = result.get("intel", [])
    cs    = result.get("cash_summary", {})
    score = las.get("score", 0)
    verdict = las.get("verdict", "REVIEW")
    reason  = las.get("reason", "")
    tier    = m.report_tier or "trustee"
    tier_lbl, tier_desc = _ATTY_LABELS.get(tier, ("Legal Report", ""))
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    v_col = "#c0392b" if score >= 60 else "#d4860a" if score >= 30 else "#2e7d52"

    bullets = "".join(
        f'<li style="margin-bottom:8px">{i["title"]} — <span style="color:#b8c4d4">'
        f'{i["narrative"][:140].rstrip(" ,")}{"…" if len(i["narrative"]) > 140 else ""}</span></li>'
        for i in intel[:6]
    ) or "<li>No significant signals detected.</li>"

    purpose = (
        f'<div style="border:2px solid #000;border-left:6px solid #c8963e;padding:12px 18px;'
        f'margin:16px 0 24px;background:#f9f6ef;color:#111">'
        f'<div style="font-size:8px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">REPORT PURPOSE</div>'
        f'<div style="font-size:11px;font-weight:700;margin-bottom:4px">{tier_lbl}</div>'
        f'<div style="font-size:11px">{tier_desc}</div></div>'
    ) if tier_lbl else ""

    debtor_line = f"<b>Debtor:</b> {m.debtor_name}<br>" if m.debtor_name else ""
    case_line   = f"<b>Case:</b> {m.case_number}<br>" if m.case_number else ""

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><title>Lawyer Brief — {m.subject} — LexCrypta Verify</title>
<style>{_report_base_css()}
  .score-big{{font-family:Georgia,serif;font-size:52px;line-height:1;color:{v_col}}}
  .verdict-lbl{{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:{v_col};margin-top:4px}}
  .finding-list{{list-style:none;padding:0;margin:0}}
  .finding-list li{{border-left:3px solid #c8963e;padding:10px 14px;margin-bottom:8px;background:var(--navy2);font-size:11px;line-height:1.7}}
  .finding-list li b{{color:var(--cream)}}
</style></head><body>
<button class="print-btn" onclick="window.print()">Print / Export PDF</button>
<div class="page">
  <div style="font-size:8px;letter-spacing:.3em;text-transform:uppercase;color:var(--muted);margin-bottom:6px">LEXCRYPTA VERIFY — LAWYER BRIEF</div>
  <div style="font-family:Georgia,serif;font-size:24px;color:var(--cream);font-weight:300;margin-bottom:4px">{m.subject}</div>
  <div style="font-size:10px;color:var(--muted);margin-bottom:20px">{debtor_line}{case_line}Generated: {now_str}</div>
  {purpose}
  <div style="display:flex;align-items:flex-start;gap:40px;margin:20px 0 28px">
    <div><div class="score-big">{score}</div><div class="verdict-lbl">{verdict}</div>
    <div style="font-size:9px;color:var(--muted);margin-top:4px">Lexi Attention Score / 100</div></div>
    <div style="flex:1;border-left:1px solid var(--border);padding-left:24px">
      <div style="font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Summary</div>
      <div style="font-size:12px;line-height:1.8;color:var(--text)">{" · ".join(reason.split(" · ")[:3]) if reason else "Analysis complete."}</div>
      {_brief_cashflow(cs)}
    </div>
  </div>
  <h2>Key Findings</h2>
  <ul class="finding-list">{bullets}</ul>
  <div style="margin-top:32px;font-size:9px;color:var(--muted);border-top:1px solid var(--border);padding-top:12px">
    This Lawyer Brief is a condensed summary prepared for professional review. For full forensic detail including all flagged transactions, signal analysis, and evidentiary appendices, request the Forensic Report.
    LexCrypta Verify · Lexcrypta LLC · {now_str}
  </div>
</div></body></html>"""


def _report_snapshot(m, result: dict) -> str:
    """Snapshot — one-page verdict + immediate action pathways."""
    las   = result.get("las", {})
    intel = result.get("intel", [])
    cs    = result.get("cash_summary", {})
    score = las.get("score", 0)
    verdict = las.get("verdict", "REVIEW")
    reason  = las.get("reason", "")
    tier    = m.report_tier or "trustee"
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    is_pursue = (tier == "trustee" and score >= 50) or (tier != "trustee" and score >= 60)
    v_word = "PURSUE" if is_pursue else "SKIP"
    v_col  = "#2e9e6b" if is_pursue else "#c0392b"

    bullets = "".join(
        f'<div style="padding-left:14px;position:relative;margin-bottom:6px;font-size:12px;color:var(--text);line-height:1.7">'
        f'<span style="position:absolute;left:0;color:var(--gold)">›</span>{b}</div>'
        for b in reason.split(" · ") if b
    ) or '<div style="color:var(--muted)">Review flagged transactions for full detail.</div>'

    # Build action pathways from intel signals
    pathways = []
    for i in intel[:5]:
        if i.get("path"):
            pathways.append(i["path"])
    # Generic LexCrypta Trace pathway always appended
    pathways.append(
        "Contact LexCrypta Trace to file exchange subpoenas and recover transaction records "
        "from Gemini, Binance, Coinbase, and CashApp. LexCrypta Trace handles the full subpoena "
        "and records recovery process for digital asset exchanges."
    )
    if cs.get("total_credits", 0) > 100_000:
        pathways.append(
            "Bank subpoena recommended — contact LexCrypta Trace to obtain full account history, "
            "counterparty details, and wire transfer records from financial institutions."
        )

    pathways_html = "".join(
        f'<div style="border-left:3px solid #c8963e;padding:10px 14px;margin-bottom:8px;background:var(--navy2);font-size:11px;line-height:1.7;color:var(--text)">'
        f'<span style="font-size:8px;letter-spacing:.15em;text-transform:uppercase;color:#c8963e;display:block;margin-bottom:4px">Action {idx+1}</span>'
        f'{p}</div>'
        for idx, p in enumerate(pathways[:5])
    )

    debtor_line = f"{m.debtor_name} · " if m.debtor_name else ""
    case_line   = f"Case {m.case_number} · " if m.case_number else ""

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><title>Snapshot — {m.subject} — LexCrypta Verify</title>
<style>{_report_base_css()}
  @media print{{.print-btn{{display:none}}}}
</style></head><body>
<button class="print-btn" onclick="window.print()">Print / Export PDF</button>
<div class="page">
  <div style="font-size:8px;letter-spacing:.3em;text-transform:uppercase;color:var(--muted);margin-bottom:6px">LEXCRYPTA VERIFY — CASE SNAPSHOT</div>
  <div style="font-family:Georgia,serif;font-size:22px;color:var(--cream);font-weight:300;margin-bottom:2px">{m.subject}</div>
  <div style="font-size:10px;color:var(--muted);margin-bottom:24px">{debtor_line}{case_line}{now_str}</div>

  <div style="display:flex;align-items:center;gap:32px;margin-bottom:28px;padding:20px 24px;background:var(--navy2);border:1px solid var(--border)">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:56px;font-weight:700;letter-spacing:.06em;color:{v_col};line-height:1">{v_word}</div>
    <div>
      <div style="font-size:28px;font-family:Georgia,serif;color:var(--cream)">{score}<span style="font-size:13px;color:var(--muted)">/100</span></div>
      <div style="font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted)">Lexi Attention Score</div>
    </div>
  </div>

  <h2>Findings</h2>
  {bullets}

  <h2>Immediate Next Steps</h2>
  {pathways_html}

  <div style="margin-top:28px;font-size:9px;color:var(--muted);border-top:1px solid var(--border);padding-top:10px">
    Case snapshot prepared by LexCrypta Verify · Lexcrypta LLC · {now_str} · For full forensic detail, request the Forensic Report.
  </div>
</div></body></html>"""


_ATTY_LABELS = {
    "trustee":  ("Trustee in Bankruptcy",
                 "This report has been prepared for use by a trustee in bankruptcy. "
                 "Insider transfers, recoverable transactions, unexplained credits, and estate "
                 "asset movements have been prioritised. All material transfers should be assessed "
                 "for preference or undervalue transactions recoverable for the benefit of creditors."),
    "divorce":  ("Family Law / Divorce",
                 "This report has been prepared for use in family law proceedings. "
                 "Sections relevant to asset dissipation, lifestyle expenditure, third-party transfers, "
                 "and undisclosed assets have been prioritised."),
    "civil":    ("Civil Litigation / Asset Recovery",
                 "This report has been prepared for use in civil litigation. "
                 "Transfers that may be designed to defeat creditors have been prioritised. "
                 "Luxury spending relative to claimed financial position is of particular note."),
    "criminal": ("Criminal / Fraud Investigation",
                 "This report has been prepared for use in a criminal or fraud investigation. "
                 "All detected indicators are reported at maximum detail with no findings suppressed. "
                 "Structuring patterns, sub-threshold transactions, digital asset indicators, "
                 "and transaction anomalies are of primary investigative interest."),
    "probate":  ("Estate / Probate",
                 "This report has been prepared for use in estate or probate proceedings. "
                 "Income sources, asset transfers, expenditure patterns, and closing balance history "
                 "have been prioritised to assist in identifying the full extent of the estate."),
}


@app.get("/reports/{matter_id}", response_class=HTMLResponse)
def get_report(
    matter_id: int,
    format: str = "forensic",
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    m = db.query(Matter).filter(Matter.id == matter_id).first()
    ar = db.query(AnalysisResult).filter(AnalysisResult.matter_id == matter_id).first()
    if not m or not ar:
        raise HTTPException(status_code=404, detail="Report not found.")
    result = json.loads(ar.result_json)
    if format == "brief":
        return _report_brief(m, result)
    if format == "snapshot":
        return _report_snapshot(m, result)
    las    = result.get("las", {})
    cs     = result.get("cash_summary", {})
    dr     = result.get("date_range", {})
    intel  = result.get("intel", [])
    ftxns  = result.get("flagged_transactions", [])
    n_txns = result.get("transactions_parsed", 0)
    counterparties = result.get("top_counterparties", [])
    monthly = result.get("monthly_breakdown", [])
    score  = las.get("score", 0)
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")

    # Colour helpers
    def score_col(s): return "#c0392b" if s >= 60 else "#d4860a" if s >= 30 else "#2e7d52"
    def sig_col(st):  return "#c0392b" if st == "detected" else "#d4860a" if st == "possible" else "#6a7a8e"

    # Executive summary bullets from detected signals
    bullets = [
        f"<li>{i['title']} — {i['narrative'][:120].rstrip(' ,')}{'…' if len(i['narrative']) > 120 else ''}</li>"
        for i in intel[:3]
    ]
    if cs.get("net_cash") is not None:
        net = cs["net_cash"]
        bullets.append(
            f"<li>Cash flow net: {'surplus of' if net >= 0 else 'deficit of'} "
            f"${abs(net):,.0f} across {n_txns:,} analysed transactions.</li>"
        )
    bullets_html = "\n".join(bullets) or "<li>No significant signals detected.</li>"

    # Signal table
    signals_html = "".join(
        f'<tr>'
        f'<td style="color:#e8e0d0;font-size:11px">{s["name"]}</td>'
        f'<td style="color:#6a7a8e;font-size:10px">{s["cat"]}</td>'
        f'<td style="color:{sig_col(s["status"])};font-weight:600;font-size:10px;letter-spacing:.08em">'
        f'{s["status"].upper()}</td>'
        f'<td style="font-family:monospace;color:#9aa8bc;font-size:10px">{s["count"] or "—"}</td>'
        f'<td style="font-family:monospace;color:#9aa8bc;font-size:10px">{s["amount"] or "—"}</td>'
        f'</tr>'
        for s in result.get("signals", [])
    )

    # Intelligence cards
    intel_html = ""
    for i in intel:
        border_col = "#c0392b" if i.get("rec_cls") == "high" else "#d4860a" if i.get("rec_cls") == "medium" else "#2e7d52"
        intel_html += f"""
        <div style="border:1px solid #2a3a50;border-left:3px solid {border_col};padding:18px 20px;margin-bottom:14px;page-break-inside:avoid">
          <div style="font-size:9px;letter-spacing:.22em;color:#c8963e;text-transform:uppercase;margin-bottom:5px">{i["cat"]} · {i.get("tier","")}</div>
          <div style="font-family:Georgia,serif;font-size:16px;color:#f0ebe0;font-weight:300;margin-bottom:10px">{i["title"]}</div>
          <p style="color:#b8c4d4;font-size:11px;line-height:1.75;margin:0 0 12px">{i["narrative"]}</p>
          <div style="font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:#c8963e;margin-bottom:5px">Path Forward</div>
          <p style="color:#7a8a9c;font-size:11px;line-height:1.65;margin:0;font-style:italic">{i["path"]}</p>
        </div>"""

    # Flagged transactions appendix
    ftxns_html = ""
    if ftxns:
        rows = "".join(
            f'<tr>'
            f'<td style="font-family:monospace;font-size:10px;color:#6a7a8e;padding:6px 10px">{t.get("date","—")}</td>'
            f'<td style="font-size:11px;color:#e8e0d0;padding:6px 10px">{t.get("merchant","")}</td>'
            f'<td style="font-family:monospace;font-size:10px;text-align:right;padding:6px 10px;color:{"#c0392b" if t.get("amount",0) < 0 else "#2e7d52"}">'
            f'{"−" if t.get("amount",0) < 0 else "+"}'
            f'${abs(t.get("amount",0)):,.2f}</td>'
            f'<td style="font-size:9px;color:#6a7a8e;padding:6px 10px">{(t.get("signal_type","")).replace("_"," ")}</td>'
            f'</tr>'
            for t in ftxns
        )
        ftxns_html = f"""
        <div style="page-break-before:always">
          <h2>Appendix A — Top Flagged Transactions</h2>
          <p style="font-size:10px;color:#6a7a8e;margin-bottom:12px">Transactions sorted by absolute value. These are the specific transactions that triggered one or more signal detections.</p>
          <table><thead><tr>
            <th>Date</th><th>Merchant / Description</th><th style="text-align:right">Amount</th><th>Signal Type</th>
          </tr></thead><tbody>{rows}</tbody></table>
        </div>"""

    # Monthly breakdown table
    monthly_html = ""
    if monthly:
        rows = "".join(
            f'<tr>'
            f'<td style="color:#e8e0d0;font-size:11px">{r["month"]}</td>'
            f'<td style="font-family:monospace;font-size:10px;color:#2e7d52;text-align:right">${r["credits"]:,.0f}</td>'
            f'<td style="font-family:monospace;font-size:10px;color:#9aa8bc;text-align:right">${r["debits"]:,.0f}</td>'
            f'<td style="font-family:monospace;font-size:10px;text-align:right;color:{"#c0392b" if r["net"] < 0 else "#2e7d52"}">'
            f'{"−" if r["net"] < 0 else "+"}${abs(r["net"]):,.0f}</td>'
            f'</tr>'
            for r in monthly
        )
        monthly_html = f"""
        <h2>Monthly Activity Profile</h2>
        <table style="max-width:480px">
          <thead><tr><th>Month</th><th style="text-align:right">Credits</th><th style="text-align:right">Debits</th><th style="text-align:right">Net</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    # Counterparty table — enrich with library data
    cp_html = ""
    if counterparties:
        # Build a lookup of known entities from the library
        cp_names = [c["merchant"].strip().upper() for c in counterparties]
        known_map = {}
        for cp_row in db.query(Counterparty).filter(Counterparty.name.in_(cp_names)).all():
            known_map[cp_row.name] = cp_row

        def _cp_badge(c):
            cp_row = known_map.get(c["merchant"].strip().upper())
            if cp_row and cp_row.matter_count > 1:
                sev_col = {"red": "#c0392b", "amber": "#d4860a", "green": "#2e7d52"}.get(cp_row.severity, "#c8963e")
                return (f'<span style="font-size:8px;background:{sev_col};color:#fff;'
                        f'padding:2px 6px;border-radius:2px;margin-left:6px;letter-spacing:.1em">'
                        f'SEEN {cp_row.matter_count}× MATTERS</span>')
            return ""

        rows = "".join(
            f'<tr>'
            f'<td style="font-size:11px;color:#e8e0d0">{c["merchant"][:55]}{_cp_badge(c)}</td>'
            f'<td style="font-family:monospace;font-size:10px;color:#c8963e;text-align:right">${c["total"]:,.0f}</td>'
            f'<td style="font-family:monospace;font-size:10px;color:#9aa8bc;text-align:right">{c["count"]}</td>'
            f'<td style="font-family:monospace;font-size:10px;color:{"#c0392b" if c["sent"] > c["received"] else "#2e7d52"};text-align:right">'
            f'{"↑" if c["received"] >= c["sent"] else "↓"} ${max(c["sent"], c["received"]):,.0f}</td>'
            f'</tr>'
            for c in counterparties
        )
        cp_html = f"""
        <h2>Top Counterparties</h2>
        <p style="font-size:10px;color:#6a7a8e;margin-bottom:10px">Entities ranked by total transaction volume. Arrows indicate dominant flow direction (↑ received, ↓ sent). Badge shows cross-matter frequency.</p>
        <table>
          <thead><tr><th>Entity</th><th style="text-align:right">Total Volume</th><th style="text-align:right">Transactions</th><th style="text-align:right">Dominant Flow</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    # Cash flow section
    cf_html = ""
    if cs:
        period = f"{dr['from_label']} – {dr['to_label']}" if dr and dr.get("from_label") else "Statement period"
        cf_html = f"""
        <h2>Cash Flow Analysis</h2>
        <table style="max-width:420px">
          <tr><td style="color:#9aa8bc">Total Inflows (Credits)</td>
              <td style="font-family:monospace;color:#c8963e;text-align:right">${cs.get("total_credits",0):,.0f}</td></tr>
          <tr><td style="color:#9aa8bc">Total Outflows (Debits)</td>
              <td style="font-family:monospace;color:#9aa8bc;text-align:right">${cs.get("total_debits",0):,.0f}</td></tr>
          <tr style="border-top:1px solid #2a3a50">
              <td style="color:#e8e0d0;font-weight:600">Net Cash Flow</td>
              <td style="font-family:monospace;color:{"#c0392b" if cs.get("net_cash",0) < 0 else "#2e7d52"};text-align:right;font-weight:600">
              {"−" if cs.get("net_cash",0) < 0 else "+"}${abs(cs.get("net_cash",0)):,.0f}</td></tr>
        </table>
        <p style="font-size:10px;color:#6a7a8e;margin-top:4px">Source: {n_txns:,} transactions · {period}</p>"""

    # Purpose banner — tier-specific context for the receiving professional
    tier = m.report_tier or "trustee"
    _purpose_html = ""
    if tier in _ATTY_LABELS:
        lbl, desc = _ATTY_LABELS[tier]
        _purpose_html = (
            f'<div style="border:2px solid #000;border-left:6px solid #c8963e;padding:12px 18px;'
            f'margin:16px 0 24px;background:#f9f6ef">'
            f'<div style="font-size:8px;color:#555;text-transform:uppercase;letter-spacing:1px;'
            f'margin-bottom:4px">REPORT PURPOSE</div>'
            f'<div style="font-size:11px;font-weight:700;margin-bottom:4px">{lbl}</div>'
            f'<div style="font-size:11px;color:#333">{desc}</div></div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forensic Report — {m.subject} — LexCrypta Verify</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500&family=IBM+Plex+Mono&display=swap');
  :root{{--navy:#0e1c2e;--navy2:#152336;--navy3:#1c2f44;--gold:#c8963e;--cream:#f2ede4;--text:#ccd6e8;--muted:#8a9bb4;--red:#c0392b;--green:#2e7d52;--border:#243650}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'IBM Plex Sans',Arial,sans-serif;background:var(--navy);color:var(--text);font-size:12px;line-height:1.6}}
  .page{{max-width:860px;margin:0 auto;padding:48px 40px}}
  h1{{font-family:Georgia,serif;color:var(--cream);font-weight:300;font-size:26px;margin-bottom:6px}}
  h2{{font-family:Georgia,serif;color:var(--gold);font-weight:300;font-size:13px;letter-spacing:.18em;text-transform:uppercase;margin:32px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}}
  table{{width:100%;border-collapse:collapse;margin-bottom:4px}}
  th{{font-size:8px;letter-spacing:.22em;text-transform:uppercase;color:var(--muted);text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}}
  td{{padding:7px 10px;border-bottom:1px solid #0d1a28}}
  .print-btn{{position:fixed;top:20px;right:20px;background:var(--gold);color:var(--navy);border:none;padding:9px 20px;font-size:10px;letter-spacing:.18em;text-transform:uppercase;cursor:pointer;font-weight:500}}
  .cover-rule{{height:1px;background:linear-gradient(to right,var(--gold),transparent);margin:16px 0 24px}}
  .las-num{{font-family:Georgia,serif;font-size:56px;line-height:1;color:{score_col(score)}}}
  .meta-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);border:1px solid var(--border);margin-bottom:28px}}
  .meta-cell{{background:var(--navy2);padding:12px 16px}}
  .meta-lbl{{font-size:8px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:3px}}
  .meta-val{{font-size:13px;color:var(--cream)}}
  .method-box{{background:var(--navy2);border-left:3px solid var(--gold);padding:14px 18px;margin-bottom:8px;font-size:11px;line-height:1.8;color:var(--text)}}
  .disclaimer{{background:#0a0f18;border:1px solid var(--border);padding:12px 16px;font-size:9px;color:var(--muted);line-height:1.7;margin-top:28px}}
  @media print{{
    body{{background:white;color:#1a1a2e}}
    .page{{padding:20px 28px}}
    .print-btn{{display:none}}
    h1{{color:#1a1a2e;font-size:22px}}
    h2{{color:#8b6914;border-bottom-color:#ddd}}
    .cover-rule{{background:linear-gradient(to right,#c8963e,transparent)}}
    .meta-grid{{border-color:#ddd;background:#ddd}}
    .meta-cell{{background:#f9f7f2}}
    .meta-lbl{{color:#888}}
    .meta-val{{color:#1a1a2e}}
    .method-box{{background:#f5f3ee;border-left-color:#c8963e;color:#333}}
    td{{color:#333;border-bottom-color:#e8e8e8}}
    th{{color:#888;border-bottom-color:#ccc}}
    .las-num{{color:{score_col(score)}}}
    .disclaimer{{background:#f5f5f5;border-color:#ddd;color:#666}}
    div[style*="border:1px solid #2a3a50"]{{border-color:#ddd!important;background:white!important}}
    p{{color:#333!important}}
    [style*="color:#6a7a8e"]{{color:#666!important}}
    [style*="color:#9aa8bc"]{{color:#333!important}}
    [style*="color:#b8c4d4"]{{color:#333!important}}
    [style*="color:#c8963e"]{{color:#8b6914!important}}
    [style*="color:#f0ebe0"]{{color:#1a1a2e!important}}
    [style*="background:#0e1c2e"],[style*="background:var(--navy"]{{background:white!important}}
  }}
</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">↓ Save / Print PDF</button>
<div class="page">

  <!-- Cover -->
  <div style="font-size:8px;letter-spacing:.35em;text-transform:uppercase;color:var(--muted);margin-bottom:14px">LexCrypta Verify · Forensic Financial Intelligence Report · Confidential</div>
  <h1>{m.subject}</h1>
  <div style="font-size:11px;color:var(--muted);margin-bottom:8px">{m.ref} · {m.type_label}</div>
  <div class="cover-rule"></div>

  <div class="meta-grid">
    <div class="meta-cell"><div class="meta-lbl">Matter Reference</div><div class="meta-val">{m.ref}</div></div>
    <div class="meta-cell"><div class="meta-lbl">Matter Type</div><div class="meta-val">{m.type_label}</div></div>
    <div class="meta-cell"><div class="meta-lbl">Analysis Completed</div><div class="meta-val">{m.last_run or now_str}</div></div>
    <div class="meta-cell"><div class="meta-lbl">Engine Version</div><div class="meta-val">LexCrypta Verify · v2026.05 · 17-Signal Library</div></div>
    <div class="meta-cell"><div class="meta-lbl">Transactions Analysed</div><div class="meta-val">{n_txns:,}</div></div>
    <div class="meta-cell"><div class="meta-lbl">Statement Period</div><div class="meta-val">{f"{dr['from_label']} – {dr['to_label']}" if dr and dr.get("from_label") else "—"}</div></div>
  </div>

  {_purpose_html}

  <!-- LAS Score -->
  <h2>Lexi Attention Score</h2>
  <div style="display:flex;align-items:flex-end;gap:24px;margin-bottom:20px">
    <div>
      <div class="las-num">{score}</div>
      <div style="font-family:Georgia,serif;font-size:16px;color:var(--cream);margin-top:4px">{las.get("verdict","—")}</div>
      <div style="font-size:11px;color:var(--muted);margin-top:3px">{las.get("reason","")}</div>
    </div>
    <div style="flex:1;background:var(--navy2);border:1px solid var(--border);padding:14px 16px">
      <div style="font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Score Components</div>
      {"".join(f'<div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="font-size:10px;color:var(--text)">{c["label"]}</span><span style="font-family:monospace;font-size:10px;color:var(--cream)">{c["val"]}/{c["max"]}</span></div>' for c in las.get("components",[]))}
    </div>
  </div>

  <!-- Executive Summary -->
  <h2>Executive Summary</h2>
  <div class="method-box">
    <ul style="padding-left:16px;color:var(--text)">
      {bullets_html}
    </ul>
  </div>

  <!-- Methodology -->
  <h2>Analytical Methodology</h2>
  <div class="method-box">
    This report was generated by LexCrypta Verify, an AI-assisted forensic financial intelligence platform.
    The analysis applied a 17-signal detection library across {n_txns:,} transactions extracted from bank statement
    documents uploaded by the engaging practitioner. Detection categories include digital asset exchange activity,
    structuring behaviour, cross-border value transfer, real estate and mortgage indicators, gift card/prepaid
    obfuscation, and document integrity assessment.<br><br>
    The <strong style="color:var(--cream)">Lexi Attention Score (LAS)</strong> is a composite metric (0–100) weighted across four dimensions:
    Signal Severity (max 40), Timing &amp; Urgency (max 25), Financial Gap (max 20), and Recovery Potential (max 15).
    A score of 60 or above indicates high-priority investigation. A score below 30 indicates limited signals —
    this does not preclude the existence of undisclosed assets outside the statement period analysed.
  </div>

  {cf_html}

  {monthly_html}

  {cp_html}

  <!-- Signal Table -->
  <h2>Signal Detection — 17 Categories</h2>
  <table>
    <thead><tr><th>Signal</th><th>Category</th><th>Status</th><th>Count</th><th>Amount</th></tr></thead>
    <tbody>{signals_html}</tbody>
  </table>

  <!-- Intelligence Detail -->
  <h2 style="page-break-before:always">Intelligence Detail</h2>
  {intel_html if intel_html else '<p style="font-size:11px;color:var(--muted);font-style:italic;padding:12px 0">No signals detected across all 17 categories. Consider uploading additional statement periods.</p>'}

  {ftxns_html}

  <!-- Chain of Custody -->
  <div style="page-break-before:always">
  <h2>Chain of Custody</h2>
  <table style="max-width:500px">
    <tr><td style="color:var(--muted)">Matter Reference</td><td style="color:var(--cream)">{m.ref}</td></tr>
    <tr><td style="color:var(--muted)">Analysis Engine</td><td style="color:var(--cream)">LexCrypta Verify v2026.05</td></tr>
    <tr><td style="color:var(--muted)">Analysis Run</td><td style="color:var(--cream)">{m.last_run or "—"}</td></tr>
    <tr><td style="color:var(--muted)">Report Generated</td><td style="color:var(--cream)">{now_str}</td></tr>
    <tr><td style="color:var(--muted)">Transactions Analysed</td><td style="color:var(--cream)">{n_txns:,}</td></tr>
  </table>

  <div class="disclaimer">
    <strong style="color:var(--gold);letter-spacing:.1em;text-transform:uppercase">Disclaimer</strong><br><br>
    LexCrypta Verify is an analytical intelligence platform for qualified legal and accounting professionals.
    This report was generated from documents uploaded by the engaging practitioner and has not been independently
    verified by LexCrypta LLC. All findings require confirmation through formal legal process including subpoena,
    court order, or other appropriate discovery mechanism before reliance in proceedings.<br><br>
    This report does not constitute legal advice. Signal detection is probabilistic — the absence of a signal does
    not confirm the absence of the underlying activity. The engaging practitioner assumes full responsibility for
    the use of this report in any legal, judicial, or regulatory proceeding.<br><br>
    LexCrypta LLC · Detroit, Michigan · lexcryptaglobal.com · Confidential — Attorney Work Product
  </div>
  </div>

</div>
</body></html>"""
    return HTMLResponse(content=html)
