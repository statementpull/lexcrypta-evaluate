# Verify+ Trustee Interface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing Verify frontend and backend into Verify+ — a trustee-grade forensic workbench with PURSUE/SKIP verdict, live parsing feed, transaction hyperlinking to source PDF, 4-tab segregated transaction table, analyst revision workbench, and 5 report tiers — ready to demo to Kristy Singal in 1–2 weeks.

**Architecture:** All changes are upgrades to two existing files (`verify-deploy/index.html` and `verify-api/app/main.py`) plus targeted additions to `models.py`, `bank_parser.py`, and a new `transaction_revisions` table. No new services — extend what exists.

**Tech Stack:** FastAPI (Python), SQLAlchemy, pdfplumber, Vanilla JS, PDF.js (CDN), CSS variables (existing navy/gold system), PostgreSQL/Supabase

---

## File Map

| File | Action | What changes |
|---|---|---|
| `verify-api/app/models.py` | Modify | Add `report_tier` to Matter; add `TransactionRevision` model |
| `verify-api/app/parsers/bank_parser.py` | Modify | Add `page_number` + `source_file` to every transaction dict |
| `verify-api/app/main.py` | Modify | Progress tracking, PURSUE/SKIP verdict, report tier, revision endpoints, PDF serve endpoint, report tier banner |
| `verify-deploy/index.html` | Modify | All frontend: lighter theme, case intake, verdict block, DB checks panel, live feed, PDF.js viewer, 4-tab table, revision workbench |

**Test data:** `LEXCRYPTA/Bank Statements/` — 11 Wells Fargo PDFs (real, US, WF parser live)

---

## Task 1: Add `page_number` and `source_file` to bank parser

**Files:**
- Modify: `verify-api/app/parsers/bank_parser.py`

Every transaction dict needs two new fields so the frontend can hyperlink back to the source.

- [ ] **Step 1: Locate the transaction yield point in `parse_bank_pdf()`**

Open `verify-api/app/parsers/bank_parser.py`. Find the function `parse_bank_pdf(pdf_bytes, filename="")`. Note that pdfplumber is opened as:
```python
with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
    for page_num, page in enumerate(pdf.pages, start=1):
```
Each parser sub-function receives `page` and builds transaction dicts. The page number must be passed through.

- [ ] **Step 2: Add `_page` parameter to each parser sub-function signature**

In `bank_parser.py`, every internal parser function that yields/returns transaction rows (e.g. `_parse_wf_page`, `_parse_westpac_page`, `_parse_nab_page`, `_parse_michigan_first_page`) currently receives `page`. Add `page_num: int = 0` as a second parameter to each.

Inside each function, on every transaction dict being appended, add:
```python
"page_number": page_num,
```

- [ ] **Step 3: Pass `page_num` from the main loop into each sub-function call**

In the main parsing loop in `parse_bank_pdf()`:
```python
# Before (example):
rows = _parse_wf_page(page)

# After:
rows = _parse_wf_page(page, page_num)
```
Apply to all parser sub-function calls inside the `enumerate(pdf.pages, start=1)` loop.

- [ ] **Step 4: Add `source_file` to every transaction dict**

In `parse_bank_pdf(pdf_bytes, filename="")`, after all transactions are collected into a list, do a single pass:
```python
for t in transactions:
    t.setdefault("page_number", 0)
    t.setdefault("source_file", filename)
```
This guarantees both fields exist even if a sub-parser didn't set them.

- [ ] **Step 5: Manual smoke test**

Start the API locally:
```bash
cd verify-api && uvicorn app.main:app --reload --port 8000
```
Upload one Wells Fargo PDF from `LEXCRYPTA/Bank Statements/`. Hit:
```
GET http://localhost:8000/matters/{id}/results
```
Confirm every transaction in `result_json.transactions` has `"page_number"` and `"source_file"` keys.

- [ ] **Step 6: Commit**
```bash
git add verify-api/app/parsers/bank_parser.py
git commit -m "feat: add page_number and source_file to every parsed transaction"
```

---

## Task 2: Add `TransactionRevision` model + `report_tier` to Matter

**Files:**
- Modify: `verify-api/app/models.py`

- [ ] **Step 1: Add `report_tier` column to `Matter`**

In `models.py`, inside the `Matter` class after the `las_reason` line, add:
```python
report_tier = Column(String(20), default="trustee")  # trustee | divorce | civil | criminal | probate
debtor_name = Column(String(200), default="")
case_number = Column(String(100), default="")
jurisdiction = Column(String(20), default="US")       # US | AU
```

- [ ] **Step 2: Add `TransactionRevision` model**

After the `DfatEntry` class at the bottom of `models.py`, append:
```python
class TransactionRevision(Base):
    """Analyst corrections, annotations, and reclassifications on parsed transactions."""
    __tablename__ = "transaction_revisions"
    __table_args__ = {"schema": "verify"}

    id          = Column(Integer, primary_key=True)
    matter_id   = Column(Integer, ForeignKey("verify.matters.id"), nullable=False, index=True)
    txn_hash    = Column(String(64), nullable=False)   # md5(date|merchant|amount|direction)
    rev_type    = Column(String(20), nullable=False)   # correct | annotate | reclassify
    field       = Column(String(50), default="")       # for 'correct': which field changed
    orig_value  = Column(Text, default="")             # original machine value
    new_value   = Column(Text, default="")             # analyst value
    note        = Column(Text, default="")             # free text annotation
    signal_override   = Column(String(100), default="")  # for reclassify
    severity_override = Column(String(10), default="")   # red | amber | green
    is_false_positive = Column(Boolean, default=False)
    analyst_id  = Column(String(100), default="")
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 3: Verify `create_all` picks up new tables**

In `verify-api/app/database.py`, confirm `Base.metadata.create_all(bind=engine)` is called on startup (it is — check `startup()` in `main.py`). The new columns and table will be created automatically on next deploy.

- [ ] **Step 4: Commit**
```bash
git add verify-api/app/models.py
git commit -m "feat: add report_tier/debtor_name/case_number to Matter, add TransactionRevision model"
```

---

## Task 3: Analysis progress tracking

**Files:**
- Modify: `verify-api/app/main.py`

The frontend needs to poll for live parsing progress. We use a simple in-memory store updated by `run_analysis` as it works through each document.

- [ ] **Step 1: Add in-memory progress store**

Near the top of `main.py`, after the imports, add:
```python
# In-memory progress store — keyed by matter_id
# { matter_id: { "stage": str, "file_index": int, "file_total": int,
#                "txn_count": int, "signals": [], "done": bool } }
_analysis_progress: dict = {}
```

- [ ] **Step 2: Add GET `/matters/{matter_id}/progress` endpoint**

After the `/matters/{matter_id}/results` endpoint, add:
```python
@app.get("/matters/{matter_id}/progress")
def get_analysis_progress(matter_id: int):
    p = _analysis_progress.get(matter_id)
    if not p:
        return {"stage": "idle", "file_index": 0, "file_total": 0,
                "txn_count": 0, "signals": [], "done": True}
    return p
```

- [ ] **Step 3: Instrument `run_analysis` to update progress**

In the `run_analysis` endpoint (currently at line ~304), find the loop that iterates over uploaded documents. Wrap it to update `_analysis_progress`:

```python
docs = db.query(Document).filter_by(matter_id=matter_id).all()
_analysis_progress[matter_id] = {
    "stage": f"LOADING {len(docs)} FILES",
    "file_index": 0, "file_total": len(docs),
    "txn_count": 0, "signals": [], "done": False
}

for idx, doc in enumerate(docs, start=1):
    _analysis_progress[matter_id].update({
        "stage": f"READING FILE {idx} OF {len(docs)}: {doc.filename}",
        "file_index": idx,
    })
    parsed = parse_bank_pdf(doc.content, doc.filename)
    txns = parsed.get("transactions", [])
    _analysis_progress[matter_id]["txn_count"] += len(txns)
    all_txns.extend(txns)

_analysis_progress[matter_id]["stage"] = "RUNNING SIGNALS..."
# ... existing signal run ...
_analysis_progress[matter_id]["done"] = True
```

Adjust to match the existing variable names in `run_analysis` — `all_txns` already exists in the current loop.

- [ ] **Step 4: Also instrument `demo_analyse`**

Same pattern in the `demo_analyse` endpoint (line ~396). Use `matter_id = 0` as the demo progress key:
```python
_analysis_progress[0] = {"stage": "LOADING FILES", "file_index": 0,
                          "file_total": len(files), "txn_count": 0,
                          "signals": [], "done": False}
```

- [ ] **Step 5: Smoke test**

Upload 3 Wells Fargo PDFs to a matter. Call `POST /matters/{id}/run`. Immediately poll `GET /matters/{id}/progress` — should see stage and file_index incrementing. After completion, `done: true`.

- [ ] **Step 6: Commit**
```bash
git add verify-api/app/main.py
git commit -m "feat: add analysis progress tracking endpoint for live parsing feed"
```

---

## Task 4: Report tier, PURSUE/SKIP verdict, and restored report banner

**Files:**
- Modify: `verify-api/app/main.py`

- [ ] **Step 1: Accept `report_tier`, `debtor_name`, `case_number`, `jurisdiction` in `create_matter`**

In the `create_matter` endpoint, the request body is currently `MatterCreate`. Find the Pydantic model (or Form fields). Add the new fields. If using a Pydantic model:
```python
class MatterCreate(BaseModel):
    subject: str
    ref: str
    type: str = "civil"
    type_label: str = ""
    matter_date: str = ""
    assigned_to: str = ""
    notes: str = ""
    report_tier: str = "trustee"
    debtor_name: str = ""
    case_number: str = ""
    jurisdiction: str = "US"
```
Then in the `create_matter` handler, pass these to the `Matter(...)` constructor.

- [ ] **Step 2: Update `_matter_to_dict` to include new fields**

In `_matter_to_dict(m)` (line ~171), add to the returned dict:
```python
"report_tier": m.report_tier or "trustee",
"debtor_name": m.debtor_name or "",
"case_number": m.case_number or "",
"jurisdiction": m.jurisdiction or "US",
```

- [ ] **Step 3: Add PURSUE/SKIP verdict logic to `run_analysis`**

After the existing LAS score is calculated (line ~356), add trustee-specific verdict override:
```python
# Existing LAS assigns verdict like "DO FIRST" / "REVIEW NEXT" / "LOW URGENCY"
# For trustee mode, override to PURSUE / SKIP
if m.report_tier == "trustee":
    if las["score"] >= 50:
        m.las_verdict     = "PURSUE"
        m.las_verdict_cls = "high"
    else:
        m.las_verdict     = "SKIP"
        m.las_verdict_cls = "low"
    # Regenerate reason bullets with trustee language
    m.las_reason = _trustee_reason(las)
```

- [ ] **Step 4: Add `_trustee_reason()` helper**

Before the `run_analysis` function, add:
```python
def _trustee_reason(las: dict) -> str:
    """Generate top-3 plain English bullets for trustee verdict."""
    signals = las.get("breakdown", [])
    top = sorted(signals, key=lambda s: s.get("points", 0), reverse=True)[:3]
    bullets = []
    for s in top:
        label  = s.get("label", "")
        count  = s.get("count", 0)
        detail = s.get("detail", "")
        if label and detail:
            bullets.append(f"{label}: {detail}")
    return " · ".join(bullets) if bullets else "No significant indicators detected."
```

- [ ] **Step 5: Restore `_ATTY_LABELS` report tier banner**

In the HTML report generation section of `main.py` (around the `get_report` endpoint, line ~575), add the tier banner block before the report body. Add this dict near the report function:
```python
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
```

In the HTML report string, after the cover block, insert:
```python
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
```

Insert `{_purpose_html}` into the report HTML string immediately after the cover block.

- [ ] **Step 6: Smoke test**

Create a matter with `report_tier: "trustee"`. Run analysis on a Wells Fargo PDF. Check:
- `GET /matters/{id}` → `las_verdict` = "PURSUE" or "SKIP"
- `GET /reports/{id}` → report shows the trustee purpose banner at top

- [ ] **Step 7: Commit**
```bash
git add verify-api/app/main.py
git commit -m "feat: report tiers restored, PURSUE/SKIP trustee verdict, tier banner in report"
```

---

## Task 5: Revision endpoints + PDF serve endpoint

**Files:**
- Modify: `verify-api/app/main.py`

- [ ] **Step 1: Add `txn_hash` helper**

Near the top of `main.py` (after imports):
```python
import hashlib

def _txn_hash(t: dict) -> str:
    key = f"{t.get('date','')}|{t.get('merchant','')}|{t.get('amount','')}|{t.get('direction','')}"
    return hashlib.md5(key.encode()).hexdigest()
```

- [ ] **Step 2: Add Pydantic model for revision request**

```python
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
```

- [ ] **Step 3: Add `POST /matters/{matter_id}/revisions` endpoint**

```python
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
```

- [ ] **Step 4: Add `GET /matters/{matter_id}/revisions` endpoint**

```python
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
```

- [ ] **Step 5: Add `DELETE /matters/{matter_id}/revisions/{rev_id}` endpoint**

```python
@app.delete("/matters/{matter_id}/revisions/{rev_id}")
def delete_revision(matter_id: int, rev_id: int, db: Session = Depends(get_db),
                    _lic = Depends(require_license)):
    rev = db.query(TransactionRevision).filter_by(id=rev_id, matter_id=matter_id).first()
    if not rev:
        raise HTTPException(status_code=404, detail="Revision not found")
    db.delete(rev)
    db.commit()
    return {"deleted": rev_id}
```

- [ ] **Step 6: Add `GET /matters/{matter_id}/documents/{doc_id}` PDF serve endpoint**

```python
from fastapi.responses import Response

@app.get("/matters/{matter_id}/documents/{doc_id}")
def serve_document(matter_id: int, doc_id: int, db: Session = Depends(get_db),
                   _lic = Depends(require_license)):
    doc = db.query(Document).filter_by(id=doc_id, matter_id=matter_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(content=bytes(doc.content), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc.filename}"'})
```

- [ ] **Step 7: Import `TransactionRevision` at top of `main.py`**

Find the existing models import line:
```python
from .models import Matter, Document, AnalysisResult, License, Counterparty, CounterpartyMatterLink, SdnEntry, DfatEntry
```
Add `TransactionRevision` to it.

- [ ] **Step 8: Smoke test**

```bash
# Create a revision
curl -X POST http://localhost:8000/matters/1/revisions \
  -H "Content-Type: application/json" \
  -H "X-License-Key: your-key" \
  -d '{"txn_hash":"abc123","rev_type":"annotate","note":"This is rent"}'

# List revisions
curl http://localhost:8000/matters/1/revisions -H "X-License-Key: your-key"

# Serve PDF
curl http://localhost:8000/matters/1/documents/1 -H "X-License-Key: your-key" --output test.pdf
open test.pdf
```

- [ ] **Step 9: Commit**
```bash
git add verify-api/app/main.py
git commit -m "feat: revision endpoints (POST/GET/DELETE) + PDF document serve endpoint"
```

---

## Task 6: Frontend — lighter theme + Verify+ branding + case intake

**Files:**
- Modify: `verify-deploy/index.html`

- [ ] **Step 1: Update dark mode navy to lighter blue**

Find in `index.html`:
```css
--navy:#0e1c2e;--navy2:#132538;--navy3:#1a3050;--navy4:#213a60;
```
Replace with:
```css
--navy:#0d2040;--navy2:#112649;--navy3:#1a3560;--navy4:#214070;
```
This lifts the dark mode from near-black to a clear medium navy, matching the Verify UI work already done.

- [ ] **Step 2: Update page title and topbar branding**

Find the topbar title element. Currently shows "Quick Scan · Bank Statement Analysis" or similar. Update to:
```html
<div class="topbar-title">Verify<sup style="font-size:9px">+</sup> <em>·</em> Trustee Forensic Intelligence</div>
```

- [ ] **Step 3: Add case intake fields to the matter creation modal**

Find the create-matter form/modal in `index.html`. It currently has `subject`, `ref`, `type`, etc. Add four new fields after the existing ones:

```html
<div class="form-row">
  <label class="form-label">Debtor Name</label>
  <input id="fDebtorName" type="text" class="form-input" placeholder="John Vincent Lombardo">
</div>
<div class="form-row">
  <label class="form-label">Case Number</label>
  <input id="fCaseNumber" type="text" class="form-input" placeholder="2024-BK-04467">
</div>
<div class="form-row">
  <label class="form-label">Jurisdiction</label>
  <select id="fJurisdiction" class="form-input">
    <option value="US">US Chapter 7</option>
    <option value="AU">AU Bankruptcy</option>
  </select>
</div>
<div class="form-row">
  <label class="form-label">Report Tier</label>
  <select id="fReportTier" class="form-input">
    <option value="trustee">Trustee in Bankruptcy</option>
    <option value="divorce">Family Law / Divorce</option>
    <option value="civil">Civil Litigation</option>
    <option value="criminal">Criminal / Fraud Investigation</option>
    <option value="probate">Estate / Probate</option>
  </select>
</div>
```

- [ ] **Step 4: Pass new fields when creating a matter**

Find the JS function that calls `POST /matters`. Add to the request body:
```javascript
debtor_name:  document.getElementById('fDebtorName')?.value?.trim() || '',
case_number:  document.getElementById('fCaseNumber')?.value?.trim() || '',
jurisdiction: document.getElementById('fJurisdiction')?.value || 'US',
report_tier:  document.getElementById('fReportTier')?.value  || 'trustee',
```

- [ ] **Step 5: Display debtor name + case number in matter header**

Find where matter data is rendered into the matter header block (the `mh-*` elements). After the existing subject/ref display, add:
```javascript
if (m.debtor_name) {
  document.getElementById('mhDebtorName').textContent = m.debtor_name;
}
if (m.case_number) {
  document.getElementById('mhCaseNumber').textContent = m.case_number;
}
```
Add corresponding `<span id="mhDebtorName">` and `<span id="mhCaseNumber">` elements to the matter header HTML.

- [ ] **Step 6: Visual check**

Open `verify-deploy/index.html` in browser (or via dev server). Confirm:
- Dark mode background is clearly navy-blue, not near-black
- "Verify+" appears in topbar
- Create matter modal shows all 4 new fields
- Light mode still readable

- [ ] **Step 7: Commit**
```bash
git add verify-deploy/index.html
git commit -m "feat: lighter navy theme, Verify+ branding, case intake fields"
```

---

## Task 7: Frontend — PURSUE/SKIP verdict block + database checks panel

**Files:**
- Modify: `verify-deploy/index.html`

- [ ] **Step 1: Add PURSUE/SKIP verdict block HTML**

Find the matter header section (where `mh-las-verdict` currently lives). Replace or augment with a larger verdict block:

```html
<div id="pursueSkipBlock" style="display:none;margin:24px 0">
  <div id="pursueSkipVerdict" class="ps-verdict"></div>
  <div id="pursueSkipScore" class="ps-score"></div>
  <div id="pursueSkipBullets" class="ps-bullets"></div>
</div>
```

- [ ] **Step 2: Add PURSUE/SKIP CSS**

In the `<style>` block, add:
```css
.ps-verdict{font-size:42px;font-weight:700;letter-spacing:.08em;font-family:var(--mono);line-height:1}
.ps-verdict.pursue{color:#2e9e6b}
.ps-verdict.skip{color:#c0392b}
.ps-score{font-size:11px;letter-spacing:.15em;color:var(--muted);margin:4px 0 12px;font-family:var(--mono)}
.ps-bullets{font-size:12px;line-height:1.8;color:var(--text);max-width:600px}
.ps-bullets span{display:block;padding-left:12px;position:relative}
.ps-bullets span::before{content:"›";position:absolute;left:0;color:var(--gold)}
```

- [ ] **Step 3: Populate verdict block after analysis completes**

Find the JS function that renders analysis results (currently renders `las.verdict` into `mhLASVerdict`). Add:

```javascript
function renderPursueSkip(las, reportTier) {
  const block  = document.getElementById('pursueSkipBlock');
  const vEl    = document.getElementById('pursueSkipVerdict');
  const sEl    = document.getElementById('pursueSkipScore');
  const bEl    = document.getElementById('pursueSkipBullets');
  if (!las || reportTier !== 'trustee') { block.style.display = 'none'; return; }

  const isPursue = las.score >= 50;
  vEl.textContent  = isPursue ? 'PURSUE' : 'SKIP';
  vEl.className    = 'ps-verdict ' + (isPursue ? 'pursue' : 'skip');
  sEl.textContent  = `Indicator Score: ${las.score}/100`;

  const reason  = las.reason || '';
  const bullets = reason.split(' · ').filter(Boolean);
  bEl.innerHTML = bullets.map(b => `<span>${b}</span>`).join('');
  block.style.display = 'block';
}
```

Call `renderPursueSkip(result.las, currentMatter.report_tier)` where results are rendered.

- [ ] **Step 4: Add database checks panel HTML**

After the pursue/skip block, add the 6-row panel:
```html
<div id="dbChecksPanel" style="display:none;margin:16px 0 24px">
  <div style="font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:10px">Database Checks</div>
  <div class="db-checks-grid" id="dbChecksGrid"></div>
</div>
```

CSS:
```css
.db-checks-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.db-check-row{display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--navy2);border:1px solid var(--border);border-radius:3px}
.db-check-name{font-size:10px;font-weight:500;flex:1}
.db-check-finding{font-size:10px;color:var(--muted);flex:2}
.db-pill{font-size:8px;letter-spacing:.1em;padding:2px 7px;border-radius:2px;font-weight:600}
.db-pill.hit{background:var(--reddim);color:var(--red)}
.db-pill.clear{background:rgba(46,158,107,.15);color:#2e9e6b}
.db-pill.pending{background:var(--golddim);color:var(--gold)}
```

- [ ] **Step 5: Populate DB checks panel with demo data**

```javascript
const DB_CHECKS_DEMO = [
  { name: "ASIC / ABN Lookup",         status: "hit",     finding: "3 directorships found — Morning Moon Developments Pty Ltd active" },
  { name: "PPSR",                       status: "hit",     finding: "2 security interests registered — Westpac Banking Corp (2019)" },
  { name: "AFSA Bankruptcy Register",  status: "clear",   finding: "No current or prior bankruptcy found" },
  { name: "Property Title Search",     status: "hit",     finding: "2 properties identified — Lot 54 Windsor, 9 Matherson Way Officer" },
  { name: "Court Records",             status: "hit",     finding: "CI-24-04467 · CI-24-06120 — active civil proceedings" },
  { name: "PACER (US Federal Courts)", status: "pending", finding: "Integration pending — available in full deployment" },
];

function renderDbChecks(show) {
  const panel = document.getElementById('dbChecksPanel');
  const grid  = document.getElementById('dbChecksGrid');
  if (!show) { panel.style.display = 'none'; return; }

  grid.innerHTML = DB_CHECKS_DEMO.map(c => `
    <div class="db-check-row">
      <span class="db-check-name">${c.name}</span>
      <span class="db-pill ${c.status}">${c.status.toUpperCase()}</span>
      <span class="db-check-finding">${c.finding}</span>
    </div>`).join('');
  panel.style.display = 'block';
}
```

Call `renderDbChecks(true)` after results render.

- [ ] **Step 6: Visual check**

Run analysis on a Wells Fargo PDF in a trustee matter. Confirm:
- PURSUE / SKIP shows in large text with score and bullets
- Database checks panel shows 6 rows with HIT / CLEAR / PENDING pills
- Both panels are hidden before analysis runs

- [ ] **Step 7: Commit**
```bash
git add verify-deploy/index.html
git commit -m "feat: PURSUE/SKIP verdict block + database checks panel"
```

---

## Task 8: Frontend — live parsing feed (replaces scan overlay)

**Files:**
- Modify: `verify-deploy/index.html`

- [ ] **Step 1: Replace scan overlay content with live feed container**

Find the `scanOverlay` div and its contents. Keep the overlay container but replace the inner content:
```html
<div id="scanOverlay" style="display:none">
  <div class="live-feed-container">
    <div class="live-feed-header">
      <span class="live-feed-title" id="liveFeedTitle">INITIALISING...</span>
      <span class="live-feed-count">
        <span id="liveFeedFileIdx">0</span>/<span id="liveFeedFileTotal">0</span> files ·
        <span id="liveFeedTxnCount">0</span> transactions
      </span>
    </div>
    <div id="liveFeedLog" class="live-feed-log"></div>
    <div class="live-feed-bar"><div id="liveFeedBarFill" class="live-feed-bar-fill" style="width:0%"></div></div>
  </div>
</div>
```

CSS:
```css
.live-feed-container{background:var(--navy2);border:1px solid var(--border);padding:24px;max-width:640px;width:100%}
.live-feed-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}
.live-feed-title{font-family:var(--mono);font-size:10px;letter-spacing:.2em;color:var(--gold)}
.live-feed-count{font-family:var(--mono);font-size:10px;color:var(--muted)}
.live-feed-log{font-family:var(--mono);font-size:10px;color:var(--text);height:140px;overflow-y:auto;line-height:2;border-top:1px solid var(--border);padding-top:10px;margin-bottom:12px}
.live-feed-log .lf-signal{color:var(--gold)}
.live-feed-log .lf-dim{color:var(--muted)}
.live-feed-bar{background:var(--navy3);height:2px;border-radius:1px}
.live-feed-bar-fill{background:var(--gold);height:2px;border-radius:1px;transition:width .3s}
```

- [ ] **Step 2: Add live feed polling function**

```javascript
let _liveFeedInterval = null;

function startLiveFeed(matterId) {
  const log   = document.getElementById('liveFeedLog');
  const title = document.getElementById('liveFeedTitle');
  const fIdx  = document.getElementById('liveFeedFileIdx');
  const fTot  = document.getElementById('liveFeedFileTotal');
  const tCnt  = document.getElementById('liveFeedTxnCount');
  const bar   = document.getElementById('liveFeedBarFill');
  log.innerHTML = '';

  _liveFeedInterval = setInterval(async () => {
    try {
      const r = await fetch(`/matters/${matterId}/progress`, {
        headers: { 'X-License-Key': _licenseKey }
      });
      const p = await r.json();

      title.textContent       = p.stage || 'PROCESSING...';
      fIdx.textContent        = p.file_index || 0;
      fTot.textContent        = p.file_total || 0;
      tCnt.textContent        = p.txn_count  || 0;
      const pct = p.file_total > 0
        ? Math.round((p.file_index / p.file_total) * 100)
        : (p.done ? 100 : 10);
      bar.style.width = pct + '%';

      // Append new signals to log
      if (p.stage && p.stage !== _lastStage) {
        _lastStage = p.stage;
        const line = document.createElement('div');
        line.className = p.stage.includes('SIGNAL') ? 'lf-signal' : 'lf-dim';
        line.textContent = '› ' + p.stage;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
      }

      if (p.done) {
        clearInterval(_liveFeedInterval);
        _liveFeedInterval = null;
      }
    } catch(e) { /* swallow — analysis may still be starting */ }
  }, 500);
}

let _lastStage = '';
```

- [ ] **Step 3: Wire `startLiveFeed` into the run analysis call**

Find where `POST /matters/{id}/run` is called. Immediately after the fetch starts (before awaiting the response), call:
```javascript
document.getElementById('scanOverlay').style.display = 'flex';
_lastStage = '';
startLiveFeed(matterId);
```

When the run completes, hide the overlay:
```javascript
document.getElementById('scanOverlay').style.display = 'none';
if (_liveFeedInterval) { clearInterval(_liveFeedInterval); _liveFeedInterval = null; }
```

- [ ] **Step 4: Test with real data**

Upload all 11 Wells Fargo PDFs from `LEXCRYPTA/Bank Statements/`. Run analysis. Confirm:
- Overlay shows "LOADING FILES"
- File counter increments 1/11 → 2/11 → ... → 11/11
- Transaction count grows as each file is processed
- Progress bar fills
- Overlay disappears when done and results render

- [ ] **Step 5: Commit**
```bash
git add verify-deploy/index.html
git commit -m "feat: live parsing feed replaces scan overlay — per-file progress + transaction counter"
```

---

## Task 9: Frontend — PDF.js transaction hyperlinking

**Files:**
- Modify: `verify-deploy/index.html`

- [ ] **Step 1: Add PDF.js from CDN**

In the `<head>` of `index.html`, add:
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf_viewer.min.css">
```
After the script tag, set the worker:
```html
<script>
  if (typeof pdfjsLib !== 'undefined') {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  }
</script>
```

- [ ] **Step 2: Add PDF viewer modal HTML**

Before the closing `</body>`:
```html
<div id="pdfViewerModal" style="display:none;position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.85);align-items:center;justify-content:center">
  <div style="background:var(--navy2);width:90vw;max-width:900px;height:90vh;display:flex;flex-direction:column;border:1px solid var(--border)">
    <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 20px;border-bottom:1px solid var(--border)">
      <span id="pdfViewerLabel" style="font-family:var(--mono);font-size:10px;letter-spacing:.15em;color:var(--gold)">SOURCE DOCUMENT</span>
      <button onclick="closePdfViewer()" class="btn" style="padding:6px 14px">✕ Close</button>
    </div>
    <canvas id="pdfViewerCanvas" style="flex:1;overflow:auto;display:block;margin:auto"></canvas>
    <div style="display:flex;align-items:center;gap:12px;padding:10px 20px;border-top:1px solid var(--border)">
      <button onclick="pdfViewerPrevPage()" class="btn" style="padding:6px 14px">← Prev</button>
      <span id="pdfViewerPageInfo" style="font-size:10px;color:var(--muted);font-family:var(--mono)">Page 1</span>
      <button onclick="pdfViewerNextPage()" class="btn" style="padding:6px 14px">Next →</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add PDF viewer JS**

```javascript
let _pdfDoc = null, _pdfPageNum = 1, _pdfDocCache = {};

async function openPdfViewer(matterId, docId, pageNum, filename) {
  document.getElementById('pdfViewerModal').style.display = 'flex';
  document.getElementById('pdfViewerLabel').textContent =
    (filename || 'SOURCE DOCUMENT') + '  ·  Page ' + pageNum;
  _pdfPageNum = pageNum || 1;

  const cacheKey = `${matterId}_${docId}`;
  if (!_pdfDocCache[cacheKey]) {
    const resp = await fetch(`/matters/${matterId}/documents/${docId}`,
      { headers: { 'X-License-Key': _licenseKey } });
    const buf  = await resp.arrayBuffer();
    _pdfDocCache[cacheKey] = await pdfjsLib.getDocument({ data: buf }).promise;
  }
  _pdfDoc = _pdfDocCache[cacheKey];
  await _renderPdfPage(_pdfPageNum);
}

async function _renderPdfPage(num) {
  const page   = await _pdfDoc.getPage(num);
  const canvas = document.getElementById('pdfViewerCanvas');
  const ctx    = canvas.getContext('2d');
  const vp     = page.getViewport({ scale: 1.4 });
  canvas.width  = vp.width;
  canvas.height = vp.height;
  await page.render({ canvasContext: ctx, viewport: vp }).promise;
  document.getElementById('pdfViewerPageInfo').textContent =
    `Page ${num} of ${_pdfDoc.numPages}`;
}

async function pdfViewerPrevPage() {
  if (_pdfPageNum > 1) { _pdfPageNum--; await _renderPdfPage(_pdfPageNum); }
}
async function pdfViewerNextPage() {
  if (_pdfDoc && _pdfPageNum < _pdfDoc.numPages) {
    _pdfPageNum++; await _renderPdfPage(_pdfPageNum);
  }
}
function closePdfViewer() {
  document.getElementById('pdfViewerModal').style.display = 'none';
}
```

- [ ] **Step 4: Add source link icon to each transaction row**

Find where transaction rows are rendered into the table (the JS that builds `<tr>` elements). Add a source link as the last column:

```javascript
// In the row builder, assuming `t` is a transaction object and `currentMatterId` is set:
const docId = t.doc_id || '';   // doc_id needs to be passed through from the backend
const pageNum = t.page_number || 1;
const srcCell = docId
  ? `<td><button class="btn-src" onclick="openPdfViewer(${currentMatterId},${docId},${pageNum},'${(t.source_file||'').replace(/'/g,"\\'")}')">⤴</button></td>`
  : '<td></td>';
```

CSS:
```css
.btn-src{background:none;border:none;color:var(--muted);cursor:pointer;font-size:12px;padding:2px 4px;transition:color .15s}
.btn-src:hover{color:var(--gold)}
```

- [ ] **Step 5: Pass `doc_id` through from the backend**

In `main.py`, in the `run_analysis` and `demo_analyse` endpoints, when building the transactions list for storage, add `doc_id` to each transaction:

```python
for doc in docs:
    parsed = parse_bank_pdf(doc.content, doc.filename)
    for t in parsed.get("transactions", []):
        t["doc_id"] = doc.id   # add this line
    all_txns.extend(parsed.get("transactions", []))
```

- [ ] **Step 6: Test**

Run analysis on a Wells Fargo matter. Click the ⤴ icon on any transaction row. Confirm:
- Modal opens
- Correct PDF page renders (matching the transaction date/merchant)
- Prev/Next navigation works

- [ ] **Step 7: Commit**
```bash
git add verify-deploy/index.html verify-api/app/main.py
git commit -m "feat: PDF.js transaction hyperlinking — click any transaction to view source page"
```

---

## Task 10: Frontend — 4-tab transaction table

**Files:**
- Modify: `verify-deploy/index.html`

- [ ] **Step 1: Add tab navigation HTML**

Find the existing transaction table container. Add tabs above it:
```html
<div class="txn-tabs" id="txnTabs">
  <button class="txn-tab active" data-tab="flagged"   onclick="switchTxnTab('flagged')">Flagged</button>
  <button class="txn-tab"        data-tab="all"       onclick="switchTxnTab('all')">All Transactions</button>
  <button class="txn-tab"        data-tab="highvalue" onclick="switchTxnTab('highvalue')">High Value</button>
  <button class="txn-tab"        data-tab="revised"   onclick="switchTxnTab('revised')">Analyst Revised <span id="revisedBadge" class="txn-tab-badge" style="display:none">0</span></button>
</div>
```

CSS:
```css
.txn-tabs{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:0}
.txn-tab{font-family:var(--sans);font-size:9px;letter-spacing:.15em;text-transform:uppercase;padding:10px 20px;background:none;border:none;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .15s}
.txn-tab.active{color:var(--gold);border-bottom-color:var(--gold)}
.txn-tab:hover{color:var(--text)}
.txn-tab-badge{background:var(--gold);color:var(--navy);font-size:8px;padding:1px 5px;border-radius:8px;margin-left:5px;font-weight:700}
```

- [ ] **Step 2: Add `switchTxnTab` function**

```javascript
let _allTxns = [], _flaggedTxns = [], _revisions = {}, _currentMatterId = null;
const HIGH_VALUE_THRESHOLD = 600;

function switchTxnTab(tab) {
  document.querySelectorAll('.txn-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });

  let rows = [];
  if (tab === 'flagged')   rows = _flaggedTxns;
  if (tab === 'all')       rows = _allTxns;
  if (tab === 'highvalue') rows = _allTxns.filter(t => Math.abs(t.amount||0) >= HIGH_VALUE_THRESHOLD);
  if (tab === 'revised')   rows = _allTxns.filter(t => _revisions[_txnHash(t)]);

  renderTxnTable(rows, tab);
}

function _txnHash(t) {
  // Mirror the backend md5 logic — use a simple composite key client-side
  return `${t.date||''}|${t.merchant||''}|${t.amount||''}|${t.direction||''}`;
}
```

- [ ] **Step 3: Update result rendering to populate `_allTxns` and `_flaggedTxns`**

In the function that renders analysis results:
```javascript
_allTxns     = result.transactions || [];
_flaggedTxns = result.flagged_transactions || [];
_currentMatterId = currentMatter.id;

// Load any existing revisions for this matter
loadRevisions(_currentMatterId);

// Default to Flagged tab
switchTxnTab('flagged');
document.getElementById('txnTabs').style.display = 'flex';
```

- [ ] **Step 4: Add `loadRevisions` function**

```javascript
async function loadRevisions(matterId) {
  _revisions = {};
  try {
    const r = await fetch(`/matters/${matterId}/revisions`,
      { headers: { 'X-License-Key': _licenseKey } });
    const revs = await r.json();
    revs.forEach(rv => { _revisions[rv.txn_hash] = rv; });
    // Update revised badge count
    const badge = document.getElementById('revisedBadge');
    const count = Object.keys(_revisions).length;
    badge.textContent = count;
    badge.style.display = count > 0 ? 'inline' : 'none';
  } catch(e) { /* swallow */ }
}
```

- [ ] **Step 5: Update `renderTxnTable` to flag revised rows**

In the transaction row builder, check if the transaction has a revision:
```javascript
const hash = _txnHash(t);
const rev  = _revisions[hash];
const revisedClass = rev ? ' txn-row-revised' : '';
// Apply to the <tr>: className = 'txn-row' + revisedClass
// Show pencil icon ✏ in a column if revised
```

CSS:
```css
.txn-row-revised{background:rgba(200,150,62,.06)}
```

- [ ] **Step 6: Test all four tabs**

Run analysis on the 11 Wells Fargo PDFs. Confirm:
- Flagged tab shows only transactions with signals
- All Transactions shows complete list (should be ~300-400 rows for 11 months)
- High Value shows only transactions above $600
- Analyst Revised starts empty (badge hidden)

- [ ] **Step 7: Commit**
```bash
git add verify-deploy/index.html
git commit -m "feat: 4-tab transaction table — Flagged / All / High Value / Analyst Revised"
```

---

## Task 11: Frontend — revision workbench

**Files:**
- Modify: `verify-deploy/index.html`

- [ ] **Step 1: Add edit icon to every transaction row**

In the transaction row builder (`renderTxnTable`), add a final cell:
```javascript
const editCell = `<td><button class="btn-edit-txn" onclick="openRevisionPanel(this, '${hash}')" title="Correct · Annotate · Reclassify">✏</button></td>`;
```

CSS:
```css
.btn-edit-txn{background:none;border:none;color:var(--muted);cursor:pointer;font-size:11px;padding:2px 4px;transition:color .15s}
.btn-edit-txn:hover{color:var(--gold)}
```

- [ ] **Step 2: Add revision panel HTML (inline, toggled per row)**

Add to the body (hidden by default):
```html
<div id="revisionPanel" style="display:none;position:fixed;right:0;top:0;bottom:0;width:360px;background:var(--navy2);border-left:1px solid var(--border);z-index:100;overflow-y:auto;padding:24px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
    <span style="font-family:var(--mono);font-size:9px;letter-spacing:.2em;color:var(--gold)">REVISION WORKBENCH</span>
    <button onclick="closeRevisionPanel()" class="btn" style="padding:4px 12px">✕</button>
  </div>
  <div id="revPanelTxnInfo" style="font-size:10px;color:var(--muted);margin-bottom:16px;padding:10px;background:var(--navy3);border-radius:2px"></div>

  <!-- Tab: Correct -->
  <div class="rev-section">
    <div class="rev-section-title">Correct Parser Error</div>
    <label class="form-label">Field</label>
    <select id="revField" class="form-input" style="margin-bottom:8px">
      <option value="merchant">Merchant / Description</option>
      <option value="amount">Amount</option>
      <option value="date">Date</option>
      <option value="direction">Direction (debit/credit)</option>
    </select>
    <label class="form-label">Corrected Value</label>
    <input id="revNewValue" type="text" class="form-input" style="margin-bottom:8px">
    <button class="btn btn-gold" style="width:100%" onclick="submitRevision('correct')">Save Correction</button>
  </div>

  <hr style="border-color:var(--border);margin:16px 0">

  <!-- Tab: Annotate -->
  <div class="rev-section">
    <div class="rev-section-title">Annotate</div>
    <label class="form-label">Analyst Note</label>
    <textarea id="revNote" class="form-input" rows="3" style="resize:vertical;margin-bottom:8px" placeholder="This is rent — not a related party transfer"></textarea>
    <button class="btn btn-gold" style="width:100%" onclick="submitRevision('annotate')">Save Note</button>
  </div>

  <hr style="border-color:var(--border);margin:16px 0">

  <!-- Tab: Reclassify -->
  <div class="rev-section">
    <div class="rev-section-title">Reclassify Signal</div>
    <label class="form-label">Signal Category</label>
    <select id="revSignal" class="form-input" style="margin-bottom:8px">
      <option value="">— No signal (false positive) —</option>
      <option value="Crypto Exchange Activity">Crypto Exchange Activity</option>
      <option value="Mortgage Servicer Activity">Mortgage Servicer Activity</option>
      <option value="Gambling Platforms">Gambling Platforms</option>
      <option value="Luxury Asset Merchants">Luxury Asset Merchants</option>
      <option value="Structuring / Cash Behaviour">Structuring / Cash Behaviour</option>
      <option value="Cross-Border Value Transfer">Cross-Border Value Transfer</option>
      <option value="Political Entity Payments">Political Entity Payments</option>
      <option value="Round-Trip Cash Flows">Round-Trip Cash Flows</option>
      <option value="NSF Fee Manipulation">NSF Fee Manipulation</option>
    </select>
    <label class="form-label">Severity</label>
    <select id="revSeverity" class="form-input" style="margin-bottom:8px">
      <option value="red">RED — High confidence</option>
      <option value="amber">AMBER — Moderate confidence</option>
      <option value="green">GREEN — Low confidence / informational</option>
    </select>
    <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:var(--muted);margin-bottom:12px;cursor:pointer">
      <input type="checkbox" id="revFalsePositive"> Mark as false positive (exclude from score)
    </label>
    <button class="btn btn-gold" style="width:100%" onclick="submitRevision('reclassify')">Save Reclassification</button>
  </div>

  <div id="revSavedMsg" style="display:none;margin-top:12px;padding:8px 12px;background:rgba(46,158,107,.15);color:#2e9e6b;font-size:10px;border-radius:2px">✓ Revision saved</div>
</div>
```

CSS:
```css
.rev-section-title{font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.rev-section{margin-bottom:4px}
```

- [ ] **Step 3: Add revision panel JS**

```javascript
let _activeTxnHash = null, _activeTxn = null;

function openRevisionPanel(btn, txnHash) {
  _activeTxnHash = txnHash;
  _activeTxn     = _allTxns.find(t => _txnHash(t) === txnHash) || {};

  const info = document.getElementById('revPanelTxnInfo');
  info.innerHTML = `
    <strong>${_activeTxn.merchant || '—'}</strong><br>
    ${_activeTxn.date || '—'} · $${Math.abs(_activeTxn.amount || 0).toFixed(2)} ${(_activeTxn.direction||'').toUpperCase()}
    · Page ${_activeTxn.page_number || '?'}
  `;

  // Pre-fill existing revision if present
  const existing = _revisions[txnHash];
  if (existing) {
    document.getElementById('revNewValue').value   = existing.new_value  || '';
    document.getElementById('revNote').value       = existing.note       || '';
    document.getElementById('revSignal').value     = existing.signal_override  || '';
    document.getElementById('revSeverity').value   = existing.severity_override || 'amber';
    document.getElementById('revFalsePositive').checked = existing.is_false_positive || false;
  } else {
    document.getElementById('revNewValue').value = '';
    document.getElementById('revNote').value     = '';
    document.getElementById('revFalsePositive').checked = false;
  }

  document.getElementById('revSavedMsg').style.display = 'none';
  document.getElementById('revisionPanel').style.display = 'block';
}

function closeRevisionPanel() {
  document.getElementById('revisionPanel').style.display = 'none';
}

async function submitRevision(revType) {
  if (!_activeTxnHash || !_currentMatterId) return;
  const body = {
    txn_hash:          _activeTxnHash,
    rev_type:          revType,
    field:             document.getElementById('revField')?.value || '',
    orig_value:        JSON.stringify(_activeTxn),
    new_value:         document.getElementById('revNewValue')?.value || '',
    note:              document.getElementById('revNote')?.value || '',
    signal_override:   document.getElementById('revSignal')?.value || '',
    severity_override: document.getElementById('revSeverity')?.value || '',
    is_false_positive: document.getElementById('revFalsePositive')?.checked || false,
    analyst_id:        'analyst',
  };

  await fetch(`/matters/${_currentMatterId}/revisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-License-Key': _licenseKey },
    body: JSON.stringify(body),
  });

  document.getElementById('revSavedMsg').style.display = 'block';
  setTimeout(() => {
    document.getElementById('revSavedMsg').style.display = 'none';
  }, 2500);

  // Reload revisions + refresh table
  await loadRevisions(_currentMatterId);
  switchTxnTab('revised');  // switch to revised tab to show the change
}
```

- [ ] **Step 4: Test all three revision types**

With a Wells Fargo matter analysed:

1. Click ✏ on any transaction → panel opens, shows transaction info
2. Enter a corrected merchant name → Save Correction → check Analyst Revised tab shows row highlighted
3. Click ✏ on another transaction → enter an annotation note → Save Note → check revised tab
4. Click ✏ on a third transaction → reclassify signal, check false positive → Save → badge count updates

- [ ] **Step 5: Commit**
```bash
git add verify-deploy/index.html
git commit -m "feat: revision workbench — inline correct/annotate/reclassify on every transaction"
```

---

## Task 12: Deploy + end-to-end demo test

**Files:**
- Deploy: `verify-api` → Railway (service ID: `fcd1b7c2`)
- Deploy: `verify-deploy` → Netlify

- [ ] **Step 1: Run full local test with all 11 Wells Fargo PDFs**

1. Start backend: `cd verify-api && uvicorn app.main:app --reload --port 8000`
2. Open `verify-deploy/index.html` (or serve with `npx serve verify-deploy`)
3. Create a matter: debtor "Test Debtor", case "2025-BK-TEST", jurisdiction US, tier Trustee
4. Upload all 11 Wells Fargo PDFs from `LEXCRYPTA/Bank Statements/`
5. Run analysis — watch live feed count files 1/11 → 11/11, transaction counter grow
6. Confirm PURSUE or SKIP drops with score + 3 bullets
7. Confirm database checks panel shows 6 rows
8. Check Flagged tab — grouped signals
9. Click ⤴ on a flagged transaction — PDF opens at correct page
10. Click ✏ on a transaction — annotate "test note" — check Analyst Revised tab
11. Hit Generate Report — select Trustee tier — confirm purpose banner at top

- [ ] **Step 2: Deploy backend to Railway**

```bash
cd "Project Folder Claw"
railway up --service fcd1b7c2
```
Wait for deployment. Check Railway logs for startup errors.

- [ ] **Step 3: Deploy frontend to Netlify**

```bash
cd "Project Folder Claw/verify-deploy"
netlify deploy --prod --dir=.
```

- [ ] **Step 4: Run demo script against production**

Repeat the Step 1 test against the live Railway URL. Confirm all 12 steps work in production.

- [ ] **Step 5: Final commit + tag**

```bash
git add -A
git commit -m "feat: Verify+ trustee demo — complete build for Kristy Singal meeting"
git tag v2026-05-24-verify-plus-stable
```

---

## Demo Script for Kristy (Reference)

1. Open Verify+ — show branding, clean trustee interface
2. Create matter — "Real bankruptcy debtor, US Chapter 7, trustee mode"
3. Drop 11 Wells Fargo PDFs — "12 months of real statements"
4. Watch live feed — files processing, transactions appearing in real time
5. Verdict drops — PURSUE at XX/100
6. Walk Flagged tab — show signal groupings, explain each
7. Click ⤴ on a transaction — "that's chain of custody — every finding links to the original page"
8. Click ✏ — annotate a transaction — "forensic guys correct and sign off before court"
9. Generate Report → Trustee mode → show purpose banner, court-ready output
10. Show DB checks panel — "when fully integrated, ASIC, PPSR, PACER all fire automatically"
11. Close: *"Your platform gets the case. Our platform finds what's in it."*

---

*Plan written by Tina — Bartilotta AI agent.*
*Spec: `docs/superpowers/specs/2026-05-24-verify-plus-trustee-design.md`*
