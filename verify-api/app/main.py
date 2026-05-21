import base64
import hashlib
import hmac as _hmac
import io
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("verify")

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import DEMO_KEY, LICENSE_SECRET, MAX_CSV_MB, MAX_PDF_MB
from .database import Base, create_verify_schema, engine, get_db
from .intelligence.signals import build_verify_result, run_signals
from .models import AnalysisResult, Document, License, Matter
from .parsers.bank_parser import parse_bank_csv_text, parse_bank_pdf
from .parsers.file_detector import detect_file_type
from .seed import seed_demo_data


# ── License ───────────────────────────────────────────────────────────────────

class LicenseRequest(BaseModel):
    key: str


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


# ── Health / Version ──────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"version": "v2026.05", "libraries": 8, "signals": 16}


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
        doc = Document(matter_id=matter_id, filename=f.filename, zone=zone, content=content)
        db.add(doc)
        db.flush()
        file_ids.append(doc.id)
    m.doc_count = db.query(Document).filter(Document.matter_id == matter_id).count()
    db.commit()
    return {"uploaded": len(files), "file_ids": file_ids}


# ── Run Analysis ──────────────────────────────────────────────────────────────

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

    transactions = []
    parse_errors = []
    for doc in docs:
        try:
            raw = bytes(doc.content)  # memoryview → bytes for pdfplumber
            if doc.filename.lower().endswith(".pdf"):
                txns = parse_bank_pdf(raw)
            else:
                txns = parse_bank_csv_text(raw.decode("utf-8", errors="replace"))
            transactions.extend(txns)
        except Exception as e:
            logger.exception("Parse failed for %s: %s", doc.filename, e)
            parse_errors.append(doc.filename)

    # Pull document-level integrity signals out of transactions before running engine
    doc_signals = [t for t in transactions if t.get("signal_type") == "document_integrity"]
    transactions = [t for t in transactions if t.get("signal_type") != "document_integrity"]

    raw_signals = run_signals(transactions) + doc_signals
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

    if existing:
        existing.result_json = json.dumps(result)
    else:
        db.add(AnalysisResult(matter_id=matter_id, result_json=json.dumps(result)))

    db.commit()
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


# ── Reports ───────────────────────────────────────────────────────────────────

@app.get("/reports/{matter_id}", response_class=HTMLResponse)
def get_report(
    matter_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    m = db.query(Matter).filter(Matter.id == matter_id).first()
    ar = db.query(AnalysisResult).filter(AnalysisResult.matter_id == matter_id).first()
    if not m or not ar:
        raise HTTPException(status_code=404, detail="Report not found.")
    result = json.loads(ar.result_json)
    signals_html = "".join(
        f'<tr><td>{s["name"]}</td><td>{s["cat"]}</td>'
        f'<td style="color:{"#c0392b" if s["status"]=="detected" else "#d4860a" if s["status"]=="possible" else "#6a7a8e"}">'
        f'{s["status"].upper()}</td>'
        f'<td>{s["count"] or "—"}</td><td>{s["amount"] or "—"}</td></tr>'
        for s in result.get("signals", [])
    )
    intel_html = "".join(
        f'<div style="border:1px solid #2a3a50;padding:16px;margin-bottom:12px">'
        f'<div style="font-size:10px;letter-spacing:.2em;color:#c8963e;text-transform:uppercase;margin-bottom:6px">{i["cat"]}</div>'
        f'<div style="font-size:18px;color:#f0ebe0;margin-bottom:8px">{i["title"]}</div>'
        f'<p style="color:#9aa8bc;font-size:11px;line-height:1.7;margin-bottom:10px">{i["narrative"]}</p>'
        f'<div style="font-size:10px;color:#c8963e;margin-bottom:4px">Path Forward</div>'
        f'<p style="color:#6a7a8e;font-size:11px;line-height:1.65">{i["path"]}</p>'
        f'</div>'
        for i in result.get("intel", [])
    )
    las = result.get("las", {})
    score = las.get("score", 0)
    score_color = "#c0392b" if score >= 60 else "#d4860a" if score >= 30 else "#2e7d52"
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Verify Report — {m.subject}</title>
<style>
body{{font-family:'IBM Plex Sans',Arial,sans-serif;background:#080f1c;color:#9aa8bc;margin:0;padding:40px;font-size:13px}}
h1{{font-family:Georgia,serif;color:#f0ebe0;font-weight:300;font-size:28px;margin-bottom:4px}}
h2{{font-family:Georgia,serif;color:#c8963e;font-weight:300;font-size:16px;letter-spacing:.1em;text-transform:uppercase;margin:28px 0 12px}}
table{{width:100%;border-collapse:collapse;margin-bottom:20px}}
th{{font-size:8px;letter-spacing:.2em;text-transform:uppercase;color:#6a7a8e;text-align:left;padding:8px 12px;border-bottom:1px solid #1a2a3a}}
td{{padding:8px 12px;border-bottom:1px solid #0f1e2d;font-size:11px}}
.score{{font-family:Georgia,serif;font-size:52px;color:{score_color};line-height:1}}
.kicker{{font-size:8px;letter-spacing:.3em;text-transform:uppercase;color:#6a7a8e;margin-bottom:20px}}
</style></head><body>
<div class="kicker">LexCrypta Verify · Forensic Report · Confidential</div>
<h1>{m.subject}</h1>
<div style="color:#6a7a8e;font-size:11px;margin-bottom:24px">{m.ref} · {m.type_label} · Run {m.last_run}</div>
<h2>Lexi Attention Score</h2>
<div class="score">{las.get("score", "—")}</div>
<div style="color:#9aa8bc;margin:8px 0 4px;font-size:14px">{las.get("verdict", "—")}</div>
<div style="color:#6a7a8e;font-size:11px;margin-bottom:24px">{las.get("reason", "")}</div>
<h2>Signal Results — 16 Detection Categories</h2>
<table><thead><tr><th>Signal</th><th>Category</th><th>Status</th><th>Count</th><th>Amount</th></tr></thead>
<tbody>{signals_html}</tbody></table>
<h2>Intelligence Detail</h2>
{intel_html}
<div style="margin-top:40px;padding-top:16px;border-top:1px solid #1a2a3a;font-size:9px;color:#3a4a5a">
LexCrypta Verify · Forensic intelligence for qualified legal professionals · Not legal advice · {datetime.now(timezone.utc).strftime("%d %b %Y")}
</div></body></html>"""
    return HTMLResponse(content=html)
