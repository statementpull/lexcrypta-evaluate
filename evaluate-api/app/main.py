import base64
import hashlib
import hmac as _hmac
import io
import os
import uuid
from collections import defaultdict
from datetime import date as _date, datetime, timedelta, timezone

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import MAX_CSV_MB, MAX_PDF_MB, MAX_TOTAL_MB
from .database import Base, create_evaluate_schema, engine, get_db
from .intelligence import library_loader as _lib_mod
from .updater import run_update_check
from .intelligence.las_score import calculate_las, severity_band
from .intelligence.signals import run_all_signals
from .models import (
    Deal, DealTransaction, EvalSession, K1Partner, License,
    ReconciliationBreach, Report, RevenueGap, Signal, TaxDeclaration, UccFiling, WebFinding,
)
from .intelligence.web_scanner import run_web_scan
from .parsers.bank_parser import parse_bank_csv_text, parse_bank_pdf
from .parsers.file_detector import detect_file_type, xlsx_to_csv_sheets, _detect_csv_type
from .parsers.myob_parser import (
    parse_myob_aged_creditors,
    parse_myob_gl,
    parse_myob_pl,
)
from .parsers.normalizer_util import normalize_description
from .parsers.quickbooks_parser import parse_quickbooks_csv
from .parsers.quickbooks_pl_parser import parse_quickbooks_pl
from .parsers.balance_sheet_parser import parse_balance_sheet_text
from .parsers.aged_debtors_parser import parse_aged_debtors_csv
from .parsers.inventory_parser import parse_inventory_csv
from .parsers.customer_sales_parser import parse_customer_sales_csv
from .reconciliation.pass1 import enrich_breaches_with_intelligence, run_pass1_in_memory
from .reconciliation.pass2 import run_pass2_in_memory
from .report.generator import build_contradiction_section, build_reconciliation_report, build_report, build_deal_summary, build_lawyer_summary


# ── License ───────────────────────────────────────────────────────────────────

class LicenseRequest(BaseModel):
    key: str


def _validate_license_key(key: str) -> bool:
    # ── DEMO BYPASS (PwC Detroit demo, 2026-05-15) — REMOVE AFTER ─────────────
    # Returning True unconditionally so any key string activates Evaluate.
    return True
    # ── original HMAC validation, kept for reference ─────────────────────────
    secret = os.getenv("LICENSE_SECRET", "")
    if not secret:
        return False
    parts = key.strip().upper().split("-")
    if len(parts) != 4 or parts[0] != "LEXA":
        return False
    payload = f"LEXA-{parts[1]}-{parts[2]}"
    h = _hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    expected = base64.b32encode(h).decode()[:8].upper()
    return _hmac.compare_digest(expected, parts[3])


def require_license(db: Session = Depends(get_db)):
    # ── DEMO BYPASS — REMOVE AFTER ───────────────────────────────────────────
    return True
    if not db.query(License).first():
        raise HTTPException(status_code=403, detail="License not activated.")
    return True

app = FastAPI(title="LexCrypta Evaluate", version="1.0.0")
_cors_origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else []
_cors_localhost = [
    "http://localhost:8085", "http://127.0.0.1:8085",
    "http://localhost:8084", "http://127.0.0.1:8084",
    "http://localhost:8095", "http://127.0.0.1:8095",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not _cors_origins else (_cors_localhost + _cors_origins),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-License-Key"],
)


@app.on_event("startup")
def startup():
    import logging
    log = logging.getLogger("evaluate.startup")
    run_update_check()
    try:
        create_evaluate_schema()
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        log.error("Database init failed — running without DB: %s", exc)
    enc_path = os.path.join(os.path.dirname(__file__), "data", "lexi_libraries.db.enc")
    secret = os.getenv("LIBRARY_SECRET", "dev-secret")
    try:
        _lib_mod.init_loader(enc_path, secret)
    except Exception as exc:
        log.error("Library loader failed: %s", exc)


@app.get("/version")
def version():
    from pathlib import Path
    vf = Path(__file__).parent / "version.txt"
    v = vf.read_text().strip() if vf.exists() else "unknown"
    return {"version": v, "product": "LexCrypta Evaluate"}


@app.get("/health")
def health():
    loader = _lib_mod.get_loader()
    counts = loader.get_library_counts() if loader else {}
    return {
        "status": "ok",
        "library_tables": len(counts),
        "library_entries": sum(counts.values()),
    }


# ── License ───────────────────────────────────────────────────────────────────

@app.get("/license-status")
def license_status(db: Session = Depends(get_db)):
    # ── DEMO BYPASS — REMOVE AFTER ───────────────────────────────────────────
    # Always report activated. Frontend won't prompt for a key.
    return {"activated": True}
    # original: activated = db.query(License).first() is not None
    # return {"activated": activated}


@app.post("/activate-license")
def activate_license(req: LicenseRequest, request: Request, db: Session = Depends(get_db)):
    if db.query(License).first():
        return {"message": "Already activated"}
    key = req.key.strip().upper()
    if not _validate_license_key(key):
        raise HTTPException(status_code=400, detail="Invalid license key. Please check the key and try again.")
    db.add(License(key=key))
    db.commit()
    return {"message": "License activated successfully"}


# ── Deals ─────────────────────────────────────────────────────────────────────

@app.post("/deals")
def create_deal(
    name: str = Form(...),
    deal_value: float = Form(0),
    industry: str = Form(""),
    analysis_period_months: int = Form(24),
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    ref = f"EVL-{datetime.now(timezone.utc).strftime('%Y')}-{str(uuid.uuid4())[:4].upper()}"
    deal = Deal(
        name=name,
        ref=ref,
        deal_value=deal_value,
        industry=industry,
        analysis_period_months=analysis_period_months,
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return {"id": deal.id, "ref": deal.ref, "name": deal.name}


@app.get("/deals")
def list_deals(db: Session = Depends(get_db), _: bool = Depends(require_license)):
    deals = (
        db.query(Deal)
        .filter(Deal.purged == False)
        .order_by(Deal.created_at.desc())
        .all()
    )
    result = []
    for d in deals:
        latest = (
            db.query(Report)
            .join(EvalSession, Report.session_id == EvalSession.id)
            .filter(EvalSession.deal_id == d.id)
            .order_by(Report.id.desc())
            .first()
        )
        result.append({
            "id": d.id,
            "ref": d.ref,
            "name": d.name,
            "deal_value": d.deal_value,
            "industry": d.industry,
            "created_at": str(d.created_at),
            "report_id": latest.id if latest else None,
            "verdict": latest.verdict if latest else None,
        })
    return result


# ── Source Ingestion ──────────────────────────────────────────────────────────

def _store_transactions(deal_id: int, rows: list[dict], source: str, report_type: str, db: Session) -> int:
    from datetime import date as _dt
    count = 0
    for row in rows:
        txn_date = row.get("transaction_date")
        if isinstance(txn_date, str):
            try:
                txn_date = _dt.fromisoformat(txn_date)
            except (ValueError, TypeError):
                txn_date = None
        if txn_date is None:
            continue

        # bank_parser returns 'merchant'; reconciliation rows use 'description'
        description = row.get("description") or row.get("merchant", "")
        raw_amount = float(row.get("amount") or 0)

        if "direction" in row:
            direction = row["direction"]
            amount = abs(raw_amount)
        else:
            direction = "debit" if raw_amount < 0 else "credit"
            amount = abs(raw_amount)

        txn = DealTransaction(
            deal_id=deal_id,
            source=source,
            report_type=report_type,
            transaction_date=txn_date,
            description=description,
            description_norm=row.get("description_norm") or normalize_description(description),
            reference=row.get("reference", ""),
            amount=amount,
            direction=direction,
        )
        db.add(txn)
        count += 1
    db.commit()
    return count


@app.post("/deals/{deal_id}/upload-bank")
async def upload_bank(
    deal_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    total_rows = 0
    for upload in files:
        raw = await upload.read()
        file_type = detect_file_type(io.BytesIO(raw), upload.filename or "")

        if file_type == "bank_pdf":
            rows = parse_bank_pdf(raw)
            report_type = "bank_statement"
        elif file_type == "bank_csv":
            rows = parse_bank_csv_text(raw.decode("utf-8", errors="replace"))
            report_type = "bank_statement"
        else:
            continue

        total_rows += _store_transactions(deal_id, rows, "bank", report_type, db)

    return {"rows_imported": total_rows, "source": "bank", "deal_id": deal_id}


@app.post("/deals/{deal_id}/upload-accounting")
async def upload_accounting(
    deal_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    total_rows = 0
    for upload in files:
        raw = await upload.read()
        text = raw.decode("utf-8", errors="replace")
        file_type = detect_file_type(io.BytesIO(raw), upload.filename or "")

        if file_type == "myob_gl":
            rows = parse_myob_gl(text)
            report_type = "myob_gl"
        elif file_type == "myob_pl":
            rows = parse_myob_pl(text)
            report_type = "myob_pl"
        else:
            rows = parse_quickbooks_csv(text)
            report_type = "quickbooks_gl"

        total_rows += _store_transactions(deal_id, rows, "accounting", report_type, db)

    return {"rows_imported": total_rows, "source": "accounting", "deal_id": deal_id}


# ── Tax Declarations + K-1 ────────────────────────────────────────────────────

import json as _json
from datetime import date as _date


class K1PartnerRequest(BaseModel):
    partner_name: str
    distributions: float
    income_share: float


@app.post("/deals/{deal_id}/tax-declaration")
async def create_tax_declaration(
    deal_id: int,
    payload: str = Form(...),
    pdf_file: UploadFile = File(None),
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    data = _json.loads(payload)

    def _pd(s):
        return _date.fromisoformat(s) if s else None

    pdf_data = None
    pdf_filename = None
    if pdf_file:
        pdf_data = await pdf_file.read()
        pdf_filename = pdf_file.filename

    decl = TaxDeclaration(
        deal_id=deal_id,
        jurisdiction=data["jurisdiction"],
        tax_year=data["tax_year"],
        period_start=_pd(data.get("period_start")),
        period_end=_pd(data.get("period_end")),
        declared_income=data.get("declared_income"),
        declared_expenses=data.get("declared_expenses"),
        declared_net=data.get("declared_net"),
        adjusted_gross_income=data.get("adjusted_gross_income"),
        schedule_c_profit=data.get("schedule_c_profit"),
        taxable_income=data.get("taxable_income"),
        pdf_filename=pdf_filename,
        pdf_data=pdf_data,
    )
    db.add(decl)
    db.commit()
    db.refresh(decl)

    return {
        "id": decl.id,
        "tax_year": decl.tax_year,
        "jurisdiction": decl.jurisdiction,
        "declared_income": float(decl.declared_income or 0),
        "declared_expenses": float(decl.declared_expenses or 0),
        "period_start": str(decl.period_start),
        "period_end": str(decl.period_end),
    }


@app.get("/deals/{deal_id}/tax-declarations")
def list_tax_declarations(
    deal_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    decls = (
        db.query(TaxDeclaration)
        .filter(TaxDeclaration.deal_id == deal_id)
        .order_by(TaxDeclaration.tax_year)
        .all()
    )
    return [
        {
            "id": d.id,
            "tax_year": d.tax_year,
            "jurisdiction": d.jurisdiction,
            "declared_income": float(d.declared_income or 0),
            "declared_expenses": float(d.declared_expenses or 0),
            "period_start": str(d.period_start),
            "period_end": str(d.period_end),
            "k1_partners": [
                {
                    "id": k.id,
                    "partner_name": k.partner_name,
                    "distributions": float(k.distributions or 0),
                    "income_share": float(k.income_share or 0),
                }
                for k in d.k1_partners
            ],
        }
        for d in decls
    ]


@app.post("/deals/{deal_id}/tax-declaration/{decl_id}/k1-partner")
def add_k1_partner(
    deal_id: int,
    decl_id: int,
    req: K1PartnerRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    decl = db.query(TaxDeclaration).filter(
        TaxDeclaration.id == decl_id,
        TaxDeclaration.deal_id == deal_id,
    ).first()
    if not decl:
        raise HTTPException(status_code=404, detail="Tax declaration not found")

    partner = K1Partner(
        tax_declaration_id=decl_id,
        deal_id=deal_id,
        partner_name=req.partner_name,
        distributions=req.distributions,
        income_share=req.income_share,
    )
    db.add(partner)
    db.commit()
    db.refresh(partner)
    return {
        "id": partner.id,
        "partner_name": partner.partner_name,
        "distributions": float(partner.distributions or 0),
        "income_share": float(partner.income_share or 0),
    }


# ── Run Reconciliation ────────────────────────────────────────────────────────

@app.post("/deals/{deal_id}/run-reconciliation")
def run_reconciliation(
    deal_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    session = EvalSession(deal_id=deal_id, status="running")
    db.add(session)
    db.commit()
    db.refresh(session)

    bank_rows_db = db.query(DealTransaction).filter(
        DealTransaction.deal_id == deal_id, DealTransaction.source == "bank"
    ).all()
    acct_rows_db = db.query(DealTransaction).filter(
        DealTransaction.deal_id == deal_id, DealTransaction.source == "accounting"
    ).all()

    bank_rows = [
        {"id": r.id, "transaction_date": r.transaction_date, "amount": float(r.amount or 0),
         "direction": r.direction, "description": r.description, "description_norm": r.description_norm or ""}
        for r in bank_rows_db
    ]
    acct_rows = [
        {"id": r.id, "transaction_date": r.transaction_date, "amount": float(r.amount or 0),
         "direction": r.direction, "description": r.description, "description_norm": r.description_norm or ""}
        for r in acct_rows_db
    ]

    breaches = run_pass1_in_memory(bank_rows, acct_rows)
    loader = _lib_mod.get_loader()
    breaches = enrich_breaches_with_intelligence(breaches, loader)

    # K-1 cross-check
    k1_partners = db.query(K1Partner).filter(K1Partner.deal_id == deal_id).all()
    for partner in k1_partners:
        partner_norm = normalize_description(partner.partner_name)
        bank_transfers = sum(
            float(r.get("amount") or 0)
            for r in bank_rows
            if partner_norm and partner_norm in (r.get("description_norm") or "")
            and r.get("direction") == "credit"
        )
        declared = float(partner.distributions or 0)
        gap = bank_transfers - declared
        if abs(gap) > 1.0:
            breaches.append({
                "breach_type": "k1_gap",
                "bank_transaction_id": None,
                "accounting_transaction_id": None,
                "bank_amount": bank_transfers,
                "accounting_amount": declared,
                "gap_amount": abs(gap),
                "transaction_date": None,
                "description": f"K-1 partner: {partner.partner_name} — declared ${declared:,.2f}, bank shows ${bank_transfers:,.2f}",
                "severity": "red" if abs(gap) > 10000 else "amber",
                "library_signal": None,
                "library_source": None,
            })

    # Load tax declarations here so Pattern Absence and Pass 2 can both use them
    decls_db = db.query(TaxDeclaration).filter(
        TaxDeclaration.deal_id == deal_id
    ).order_by(TaxDeclaration.tax_year).all()

    # Pattern Absence — Metro Cartons signal
    # Regular accounting creditor stops paying in 90 days before analysis end with no bank settlement
    if decls_db and acct_rows:
        def _to_date(v):
            if isinstance(v, _date) and not isinstance(v, datetime): return v
            if hasattr(v, "date"): return v.date()
            return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()

        analysis_end = max(_to_date(d.period_end) for d in decls_db)
        cutoff = analysis_end - timedelta(days=90)

        creditor_groups: dict = defaultdict(list)
        for row in acct_rows:
            if row.get("amount", 0) < 0:
                key = row.get("description_norm") or ""
                if key:
                    creditor_groups[key].append(row)

        for key, crows in creditor_groups.items():
            if len(crows) < 3:
                continue
            dates = sorted(_to_date(r["transaction_date"]) for r in crows)
            if (dates[-1] - dates[0]).days < 180:
                continue  # not regular across a meaningful period
            if dates[-1] >= cutoff:
                continue  # still active within the 90-day window
            has_settlement = any(
                r for r in bank_rows
                if key in (r.get("description_norm") or "")
                and r.get("amount", 0) < 0
                and _to_date(r["transaction_date"]) > dates[-1]
            )
            if not has_settlement:
                last_row = max(crows, key=lambda r: _to_date(r["transaction_date"]))
                days_gap = (analysis_end - dates[-1]).days
                breaches.append({
                    "breach_type": "pattern_absence",
                    "bank_transaction_id": None,
                    "accounting_transaction_id": last_row["id"],
                    "bank_amount": None,
                    "accounting_amount": float(last_row.get("amount", 0)),
                    "gap_amount": None,
                    "transaction_date": dates[-1],
                    "description": (
                        f"Pattern absence — '{last_row.get('description', key)}': "
                        f"regular creditor payments stop {days_gap} days before analysis end "
                        f"with no settlement recorded in bank"
                    ),
                    "severity": "red",
                    "library_signal": "PATTERN_ABSENCE",
                    "library_source": "Metro Cartons Signal",
                })

    for b in breaches:
        db.add(ReconciliationBreach(
            deal_id=deal_id, session_id=session.id,
            breach_type=b["breach_type"],
            bank_transaction_id=b.get("bank_transaction_id"),
            accounting_transaction_id=b.get("accounting_transaction_id"),
            bank_amount=b.get("bank_amount"), accounting_amount=b.get("accounting_amount"),
            gap_amount=b.get("gap_amount"), transaction_date=b.get("transaction_date"),
            description=b.get("description", ""), library_signal=b.get("library_signal"),
            library_source=b.get("library_source"), severity=b.get("severity", "amber"),
        ))

    decl_dicts = [
        {"tax_year": d.tax_year, "period_start": d.period_start, "period_end": d.period_end,
         "declared_income": float(d.declared_income or 0), "declared_expenses": float(d.declared_expenses or 0)}
        for d in decls_db
    ]
    revenue_gaps = run_pass2_in_memory(decl_dicts, bank_rows)

    for g in revenue_gaps:
        db.add(RevenueGap(
            deal_id=deal_id, session_id=session.id,
            tax_year=g["tax_year"], period_start=g["period_start"], period_end=g["period_end"],
            bank_total_credits=g["bank_total_credits"], declared_income=g["declared_income"],
            income_gap=g["income_gap"], bank_total_debits=g["bank_total_debits"],
            declared_expenses=g["declared_expenses"], expense_gap=g["expense_gap"],
            is_escalating=g["is_escalating"],
        ))

    session.status = "complete"
    session.completed_at = datetime.now(timezone.utc)

    red_breaches = [b for b in breaches if b.get("severity") == "red"]
    amber_breaches = [b for b in breaches if b.get("severity") == "amber"]
    escalating_gaps = [g for g in revenue_gaps if g["is_escalating"]]
    total_income_gap = sum(g["income_gap"] for g in revenue_gaps if g["income_gap"] > 0)
    total_expense_gap = sum(g["expense_gap"] for g in revenue_gaps if g["expense_gap"] > 0)

    breach_list = [
        {"type": b["breach_type"], "severity": b["severity"], "description": b["description"],
         "gap_amount": b.get("gap_amount"),
         "date": str(b["transaction_date"]) if b.get("transaction_date") else None,
         "library_signal": b.get("library_signal"), "library_source": b.get("library_source")}
        for b in breaches
    ]
    gap_list = [
        {"tax_year": g["tax_year"], "bank_total_credits": g["bank_total_credits"],
         "declared_income": g["declared_income"], "income_gap": g["income_gap"],
         "bank_total_debits": g["bank_total_debits"], "declared_expenses": g["declared_expenses"],
         "expense_gap": g["expense_gap"], "is_escalating": g["is_escalating"]}
        for g in revenue_gaps
    ]

    report_html = build_reconciliation_report(
        deal_name=deal.name, deal_ref=deal.ref, deal_value=deal.deal_value or 0,
        pass1={"breaches": breach_list},
        pass2={"total_income_gap": total_income_gap, "total_expense_gap": total_expense_gap, "gaps": gap_list},
    )
    band = (
        "findings detected" if len(red_breaches) > 0 or total_income_gap > 50000 else
        "findings noted" if len(amber_breaches) > 0 or total_income_gap > 10000 else
        "no material findings"
    )
    # LAS score: weighted from breach count + income gap magnitude + escalating signal
    las_score = min(100.0, (
        len(red_breaches) * 15
        + len(amber_breaches) * 5
        + (20 if total_income_gap > 100000 else 10 if total_income_gap > 50000 else 5 if total_income_gap > 10000 else 0)
        + (15 if escalating_gaps else 0)
    ))

    report = Report(session_id=session.id, verdict=band.upper(), las_score=las_score, report_html=report_html)
    db.add(report)
    db.commit()
    db.refresh(report)

    return {
        "session_id": session.id,
        "report_id": report.id,
        "band": band,
        "las_score": las_score,
        "verdict": band.upper(),
        "pass1": {
            "total_breaches": len(breaches), "red": len(red_breaches), "amber": len(amber_breaches),
            "breaches": breach_list,
        },
        "pass2": {
            "years": len(revenue_gaps), "total_income_gap": total_income_gap,
            "total_expense_gap": total_expense_gap, "escalating_years": len(escalating_gaps),
            "gaps": gap_list,
        },
    }


@app.get("/deals/{deal_id}/breaches")
def get_breaches(deal_id: int, db: Session = Depends(get_db), _: bool = Depends(require_license)):
    breaches = db.query(ReconciliationBreach).filter(
        ReconciliationBreach.deal_id == deal_id
    ).order_by(ReconciliationBreach.severity.desc()).all()
    return [
        {"id": b.id, "breach_type": b.breach_type, "severity": b.severity,
         "gap_amount": float(b.gap_amount or 0), "description": b.description,
         "date": str(b.transaction_date) if b.transaction_date else None,
         "library_signal": b.library_signal, "library_source": b.library_source}
        for b in breaches
    ]


@app.get("/deals/{deal_id}/gaps")
def get_gaps(deal_id: int, db: Session = Depends(get_db), _: bool = Depends(require_license)):
    gaps = db.query(RevenueGap).filter(
        RevenueGap.deal_id == deal_id
    ).order_by(RevenueGap.tax_year).all()
    return [
        {"tax_year": g.tax_year,
         "bank_total_credits": float(g.bank_total_credits or 0), "declared_income": float(g.declared_income or 0),
         "income_gap": float(g.income_gap or 0), "bank_total_debits": float(g.bank_total_debits or 0),
         "declared_expenses": float(g.declared_expenses or 0), "expense_gap": float(g.expense_gap or 0),
         "is_escalating": g.is_escalating}
        for g in gaps
    ]


# ── Web Intelligence Scan ────────────────────────────────────────────────────

class WebScanRequest(BaseModel):
    business_name: str
    state: str = ""
    owner_names: list[str] = []


@app.post("/deals/{deal_id}/web-scan")
def web_scan(
    deal_id: int,
    req: WebScanRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    # Clear previous web findings for this deal
    db.query(WebFinding).filter(WebFinding.deal_id == deal_id).delete()
    db.commit()

    findings = run_web_scan(
        business_name=req.business_name,
        state=req.state,
        owner_names=req.owner_names,
    )

    saved = 0
    for f in findings:
        db.add(WebFinding(
            deal_id=deal_id,
            source_name=f.get("source_name", "")[:100],
            source_type=f.get("source_type", "web_intel")[:50],
            severity=f.get("severity", "amber"),
            title=(f.get("title") or "")[:295],
            description=f.get("description", ""),
            confidence=f.get("confidence", 0.7),
            business_name_searched=req.business_name[:295],
        ))
        saved += 1

    db.commit()

    red = [f for f in findings if f["severity"] == "red"]
    amber = [f for f in findings if f["severity"] == "amber"]

    return {
        "deal_id": deal_id,
        "business_name": req.business_name,
        "findings_total": saved,
        "red": len(red),
        "amber": len(amber),
        "sources_run": ["PPP Database", "EPA ECHO", "SEC EDGAR", "DOL Enforcement", "OFAC", "News", "Court Records", "USPTO Trademark"],
        "findings": [
            {
                "source": f.get("source_name"),
                "type": f.get("source_type"),
                "severity": f.get("severity"),
                "title": f.get("title"),
                "description": f.get("description"),
            }
            for f in findings
        ],
    }


@app.get("/deals/{deal_id}/web-findings")
def get_web_findings(
    deal_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    findings = db.query(WebFinding).filter(
        WebFinding.deal_id == deal_id
    ).order_by(WebFinding.severity).all()
    return [
        {
            "id": f.id,
            "source_name": f.source_name,
            "source_type": f.source_type,
            "severity": f.severity,
            "title": f.title,
            "description": f.description,
            "confidence": f.confidence,
            "scanned_at": str(f.scanned_at),
        }
        for f in findings
    ]


# ── UCC Filings ───────────────────────────────────────────────────────────────

class UccFilingRequest(BaseModel):
    filing_number: str = ""
    filing_date: str = ""       # YYYY-MM-DD
    expiry_date: str = ""       # YYYY-MM-DD
    secured_party: str
    collateral_description: str = ""
    status: str = "active"      # active | terminated | lapsed | amended
    amount_stated: float = 0
    state: str = ""
    notes: str = ""


@app.post("/deals/{deal_id}/ucc-filing")
def add_ucc_filing(
    deal_id: int,
    req: UccFilingRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    def _pd(s):
        if not s:
            return None
        try:
            return _date.fromisoformat(s)
        except ValueError:
            return None

    filing = UccFiling(
        deal_id=deal_id,
        filing_number=req.filing_number,
        filing_date=_pd(req.filing_date),
        expiry_date=_pd(req.expiry_date),
        secured_party=req.secured_party,
        collateral_description=req.collateral_description,
        status=req.status,
        amount_stated=req.amount_stated if req.amount_stated else None,
        state=req.state.upper()[:10] if req.state else "",
        notes=req.notes,
    )
    db.add(filing)
    db.commit()
    db.refresh(filing)
    return {
        "id": filing.id,
        "secured_party": filing.secured_party,
        "status": filing.status,
        "filing_date": str(filing.filing_date) if filing.filing_date else "",
        "state": filing.state,
    }


@app.get("/deals/{deal_id}/ucc-filings")
def list_ucc_filings(
    deal_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    filings = db.query(UccFiling).filter(UccFiling.deal_id == deal_id).order_by(UccFiling.filing_date).all()
    return [
        {
            "id": f.id,
            "filing_number": f.filing_number or "",
            "filing_date": str(f.filing_date) if f.filing_date else "",
            "expiry_date": str(f.expiry_date) if f.expiry_date else "",
            "secured_party": f.secured_party,
            "collateral_description": f.collateral_description or "",
            "status": f.status,
            "amount_stated": float(f.amount_stated or 0),
            "state": f.state or "",
            "notes": f.notes or "",
        }
        for f in filings
    ]


@app.delete("/deals/{deal_id}/ucc-filing/{filing_id}")
def delete_ucc_filing(
    deal_id: int,
    filing_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    filing = db.query(UccFiling).filter(
        UccFiling.id == filing_id, UccFiling.deal_id == deal_id
    ).first()
    if not filing:
        raise HTTPException(status_code=404, detail="UCC filing not found")
    db.delete(filing)
    db.commit()
    return {"deleted": True, "id": filing_id}


# ── Upload + Analyse ───────────────────────────────────────────────────────────

def _apply_csv_to_buckets(
    file_type: str,
    text: str,
    all_transactions: list,
    pl_rows: list,
    supplementary: dict,
) -> None:
    if file_type == "bank_csv":
        all_transactions += parse_bank_csv_text(text)
    elif file_type == "myob_gl":
        all_transactions += parse_myob_gl(text)
    elif file_type == "myob_pl":
        pl_rows += parse_myob_pl(text)
    elif file_type == "quickbooks_pl":
        pl_rows += parse_quickbooks_pl(text)
    elif file_type == "balance_sheet":
        supplementary["balance_sheet"] += parse_balance_sheet_text(text)
    elif file_type == "aged_debtors":
        supplementary["aged_debtors"] += parse_aged_debtors_csv(text)
    elif file_type == "inventory":
        supplementary["inventory"] += parse_inventory_csv(text)
    elif file_type == "customer_sales":
        supplementary["customer_sales"] += parse_customer_sales_csv(text)


@app.post("/deals/{deal_id}/analyse")
async def analyse_deal(
    deal_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_license),
):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    session = EvalSession(deal_id=deal_id, status="running")
    db.add(session)
    db.commit()
    db.refresh(session)

    all_transactions: list[dict] = []
    pl_rows: list[dict] = []
    supplementary: dict = {
        "balance_sheet": [],
        "aged_debtors": [],
        "inventory": [],
        "customer_sales": [],
    }
    total_bytes = 0
    warnings: list[str] = []

    for upload in files:
        raw = await upload.read()
        fname = upload.filename or "file"
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413, detail=f"Total upload size exceeds {MAX_TOTAL_MB}MB session limit"
            )
        ext = fname.lower().rsplit(".", 1)[-1]
        limit = MAX_CSV_MB if ext in ("csv", "xlsx", "xls") else MAX_PDF_MB
        if len(raw) > limit * 1024 * 1024:
            raise HTTPException(
                status_code=413, detail=f"File {fname} exceeds {limit}MB limit"
            )
        file_type = detect_file_type(io.BytesIO(raw), fname)

        if file_type == "xlsx":
            try:
                sheets = xlsx_to_csv_sheets(raw)
                if not sheets:
                    warnings.append(f"{fname}: Excel file appears empty — no data extracted.")
                for sheet_name, csv_text in sheets:
                    sheet_type = _detect_csv_type(csv_text)
                    if sheet_type == "csv_unknown":
                        warnings.append(
                            f"{fname} (sheet: {sheet_name}): Could not identify document type. "
                            "Ensure the sheet has standard column headers or a recognised report title."
                        )
                        continue
                    _apply_csv_to_buckets(sheet_type, csv_text, all_transactions, pl_rows, supplementary)
            except Exception as e:
                warnings.append(f"{fname}: Could not read Excel file ({e}). Try saving as CSV.")
            continue

        if file_type == "xls_legacy":
            warnings.append(
                f"{fname}: Legacy Excel format (.xls) is not supported. "
                "Open in Excel and save as .xlsx or export as CSV."
            )
            continue

        if file_type == "pdf_financial_report":
            warnings.append(
                f"{fname}: This PDF appears to be a financial report (balance sheet, P&L, or similar). "
                "Export as CSV from QuickBooks, MYOB, or your accounting software for full analysis. "
                "Bank statement PDFs are fully supported."
            )
            continue

        text = raw.decode("utf-8", errors="replace")

        if file_type == "bank_pdf":
            all_transactions += parse_bank_pdf(raw)
        elif file_type == "csv_unknown":
            warnings.append(
                f"{fname}: Could not identify document type. "
                "Ensure the file has standard column headers (Date, Description, Debit, Credit) "
                "or a recognised report title (Balance Sheet, Profit & Loss, Aged Debtors, etc.)."
            )
        elif file_type == "unknown":
            warnings.append(
                f"{fname}: Unsupported file format. Accepted formats: PDF (bank statements), CSV, XLSX."
            )
        else:
            _apply_csv_to_buckets(file_type, text, all_transactions, pl_rows, supplementary)

    loader = _lib_mod.get_loader()

    # Pull any UCC filings entered for this deal
    ucc_db = db.query(UccFiling).filter(UccFiling.deal_id == deal_id).all()
    ucc_records = [
        {
            "filing_number": f.filing_number or "",
            "filing_date": str(f.filing_date) if f.filing_date else "",
            "expiry_date": str(f.expiry_date) if f.expiry_date else "",
            "secured_party": f.secured_party,
            "collateral_description": f.collateral_description or "",
            "status": f.status,
            "amount_stated": float(f.amount_stated or 0),
            "state": f.state or "",
        }
        for f in ucc_db
    ]

    signals = run_all_signals(
        all_transactions,
        pl_rows=pl_rows,
        loader=loader,
        ucc_records=ucc_records or None,
        supplementary=supplementary or None,
    )

    # Merge stored web findings into signals for report
    web_db = db.query(WebFinding).filter(WebFinding.deal_id == deal_id).all()
    for wf in web_db:
        signals.append({
            "signal_type": wf.source_type,
            "severity": wf.severity,
            "merchant": (wf.title or "")[:195],
            "amount": 0,
            "transaction_date": "",
            "description": wf.description or "",
            "library_match": wf.source_name or "",
            "confidence_weight": wf.confidence or 0.7,
        })

    # Auto-reconciliation: if we have bank data AND P&L data, cross-reference immediately
    if all_transactions and pl_rows:
        def _try_parse_date(s):
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%m-%d-%Y"):
                try:
                    from datetime import date as _date2
                    return datetime.strptime(str(s)[:10], fmt).date()
                except Exception:
                    pass
            return None

        bank_rows_recon = []
        for t in all_transactions:
            d = _try_parse_date(t.get("transaction_date", ""))
            if d:
                bank_rows_recon.append({
                    "transaction_date": d,
                    "amount": abs(float(t.get("amount") or 0)),
                    "direction": "credit" if float(t.get("amount") or 0) >= 0 else "debit",
                    "description": t.get("merchant", ""),
                    "description_norm": normalize_description(t.get("merchant", "")),
                })

        if bank_rows_recon:
            income_ytd = sum(float(r.get("ytd") or 0) for r in pl_rows if float(r.get("ytd") or 0) > 0)
            expense_ytd = abs(sum(float(r.get("ytd") or 0) for r in pl_rows if float(r.get("ytd") or 0) < 0))
            dates = [r["transaction_date"] for r in bank_rows_recon]
            period_start = min(dates)
            period_end = max(dates)
            tax_year = period_end.year

            auto_gaps = run_pass2_in_memory(
                [{"tax_year": tax_year, "period_start": period_start, "period_end": period_end,
                  "declared_income": income_ytd, "declared_expenses": expense_ytd}],
                bank_rows_recon,
            )
            for g in auto_gaps:
                db.add(RevenueGap(
                    deal_id=deal_id, session_id=session.id,
                    tax_year=g["tax_year"], period_start=g["period_start"], period_end=g["period_end"],
                    bank_total_credits=g["bank_total_credits"], declared_income=g["declared_income"],
                    income_gap=g["income_gap"], bank_total_debits=g["bank_total_debits"],
                    declared_expenses=g["declared_expenses"], expense_gap=g["expense_gap"],
                    is_escalating=g["is_escalating"],
                ))
                if abs(g["income_gap"]) > 5000:
                    signals.append({
                        "signal_type": "revenue_gap",
                        "severity": "red" if abs(g["income_gap"]) > 50000 else "amber",
                        "merchant": "INCOME RECONCILIATION",
                        "amount": abs(g["income_gap"]),
                        "transaction_date": str(g["period_end"]),
                        "description": (
                            f"Bank deposits ${g['bank_total_credits']:,.0f} vs "
                            f"declared income ${g['declared_income']:,.0f} — "
                            f"gap of ${abs(g['income_gap']):,.0f}"
                        ),
                        "library_match": "",
                        "confidence_weight": 0.9,
                    })
                if abs(g["expense_gap"]) > 5000:
                    signals.append({
                        "signal_type": "expense_gap",
                        "severity": "red" if abs(g["expense_gap"]) > 50000 else "amber",
                        "merchant": "EXPENSE RECONCILIATION",
                        "amount": abs(g["expense_gap"]),
                        "transaction_date": str(g["period_end"]),
                        "description": (
                            f"Bank outflows ${g['bank_total_debits']:,.0f} vs "
                            f"declared expenses ${g['declared_expenses']:,.0f} — "
                            f"gap of ${abs(g['expense_gap']):,.0f}"
                        ),
                        "library_match": "",
                        "confidence_weight": 0.9,
                    })

    total_volume = sum(abs(t["amount"]) for t in all_transactions)
    score = calculate_las(signals, total_volume)
    band = severity_band(score)

    for s in signals:
        db.add(
            Signal(
                session_id=session.id,
                signal_type=s["signal_type"],
                severity=s["severity"],
                merchant=(s["merchant"] or "")[:195],
                amount=s.get("amount", 0),
                description=s.get("description", ""),
                library_match=(s.get("library_match") or "")[:195] or None,
                confidence_weight=s.get("confidence_weight", 0),
                transaction_date=(s.get("transaction_date") or "")[:50],
            )
        )

    # Pull any reconciliation data already run for this deal
    existing_breaches_db = db.query(ReconciliationBreach).filter(
        ReconciliationBreach.deal_id == deal_id
    ).all()
    existing_gaps_db = db.query(RevenueGap).filter(
        RevenueGap.deal_id == deal_id
    ).all()
    breach_dicts = [
        {
            "breach_type": b.breach_type,
            "severity": b.severity,
            "description": b.description or "",
            "gap_amount": float(b.gap_amount or 0),
        }
        for b in existing_breaches_db
    ]
    gap_dicts = [
        {
            "tax_year": g.tax_year,
            "bank_total_credits": float(g.bank_total_credits or 0),
            "declared_income": float(g.declared_income or 0),
            "income_gap": float(g.income_gap or 0),
            "is_escalating": g.is_escalating,
        }
        for g in existing_gaps_db
    ]
    contradictions_html = build_contradiction_section(breach_dicts, gap_dicts)

    report_html = build_report(
        deal_name=deal.name,
        deal_ref=deal.ref,
        deal_value=deal.deal_value or 0,
        analysis_period_months=deal.analysis_period_months,
        signals=signals,
        las_score=score,
        band=band,
        contradictions_html=contradictions_html,
    )

    summary_html = build_deal_summary(
        deal_name=deal.name,
        deal_ref=deal.ref,
        deal_value=deal.deal_value or 0,
        signals=signals,
        las_score=score,
        band=band,
        contradictions_html=contradictions_html,
    )
    lawyer_html = build_lawyer_summary(
        deal_name=deal.name,
        deal_ref=deal.ref,
        signals=signals,
        las_score=score,
        band=band,
        contradictions_html=contradictions_html,
        breaches=breach_dicts,
        gaps=gap_dicts,
    )
    report = Report(session_id=session.id, verdict=band, las_score=score, report_html=report_html, deal_summary_html=summary_html, lawyer_summary_html=lawyer_html)
    db.add(report)
    session.status = "complete"
    session.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(report)

    red = [s for s in signals if s["severity"] == "red"]
    amber = [s for s in signals if s["severity"] == "amber"]

    return {
        "session_id": session.id,
        "report_id": report.id,
        "las_score": score,
        "band": band,
        "verdict": band,
        "signal_count": len(signals),
        "red": len(red),
        "amber": len(amber),
        "warnings": warnings,
    }


# ── Report ────────────────────────────────────────────────────────────────────

@app.get("/reports/{report_id}", response_class=HTMLResponse)
def get_report(report_id: int, db: Session = Depends(get_db), _: bool = Depends(require_license)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return HTMLResponse(content=report.report_html)


@app.get("/reports/{report_id}/deal-summary", response_class=HTMLResponse)
def get_deal_summary(report_id: int, db: Session = Depends(get_db), _: bool = Depends(require_license)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not report.deal_summary_html:
        raise HTTPException(status_code=404, detail="Deal summary not available for this report")
    return HTMLResponse(content=report.deal_summary_html)


@app.get("/reports/{report_id}/lawyer-summary", response_class=HTMLResponse)
def get_lawyer_summary(report_id: int, db: Session = Depends(get_db), _: bool = Depends(require_license)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not report.lawyer_summary_html:
        raise HTTPException(status_code=404, detail="Lawyer summary not available for this report")
    return HTMLResponse(content=report.lawyer_summary_html)


@app.get("/sessions/{session_id}/signals")
def get_signals(session_id: int, db: Session = Depends(get_db), _: bool = Depends(require_license)):
    signals = db.query(Signal).filter(Signal.session_id == session_id).all()
    return [
        {
            "type": s.signal_type,
            "severity": s.severity,
            "merchant": s.merchant,
            "amount": s.amount,
            "description": s.description,
            "confidence_weight": s.confidence_weight,
        }
        for s in signals
    ]


# ── Purge ─────────────────────────────────────────────────────────────────────

@app.delete("/deals/{deal_id}/purge")
def purge_deal(deal_id: int, db: Session = Depends(get_db), _: bool = Depends(require_license)):
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    # Clear child rows that FK-reference sessions before deleting sessions
    db.query(ReconciliationBreach).filter(ReconciliationBreach.deal_id == deal_id).delete()
    db.query(RevenueGap).filter(RevenueGap.deal_id == deal_id).delete()
    for session in deal.sessions:
        db.query(Signal).filter(Signal.session_id == session.id).delete()
        db.query(Report).filter(Report.session_id == session.id).delete()
    db.query(EvalSession).filter(EvalSession.deal_id == deal_id).delete()
    # Remaining reconciliation data
    db.query(DealTransaction).filter(DealTransaction.deal_id == deal_id).delete()
    db.query(K1Partner).filter(K1Partner.deal_id == deal_id).delete()
    db.query(TaxDeclaration).filter(TaxDeclaration.deal_id == deal_id).delete()
    db.query(UccFiling).filter(UccFiling.deal_id == deal_id).delete()
    db.query(WebFinding).filter(WebFinding.deal_id == deal_id).delete()
    deal.purged = True
    db.commit()
    return {"purged": True, "deal_id": deal_id}
