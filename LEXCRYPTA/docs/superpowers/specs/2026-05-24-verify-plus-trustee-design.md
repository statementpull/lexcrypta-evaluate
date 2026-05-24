# Verify+ Trustee Interface — Design Spec
**Date:** 2026-05-24
**Author:** Tina (Bartilotta AI agent)
**Target demo:** Kristy Singal meeting (1–2 weeks)
**Build target:** Cloud (Railway/Verify) — enhance existing Verify frontend

---

## Background

Kristy Singal owns a 23-year-old trustee software platform with ~1,200 trustees —
functionally the entire US Chapter 7 panel trustee market. She is facing Stretto AI
and needs an intelligence layer. The pitch: *"Your platform gets the case. Our platform
finds what's in it."*

The product she needs to see is **Verify+** — a trustee-specific interface on top of the
existing Verify engine showing bank statements parsed in real-time with a PURSUE/SKIP
verdict that saves trustees from spending hours on cases that will never pay out.

**Build philosophy:** Most pieces already exist. This is upgrades and additions, not a rewrite.

---

## Test Data

Two real datasets available immediately:

| Dataset | Location | Notes |
|---|---|---|
| Wells Fargo (US) | `LEXCRYPTA/Bank Statements/` | 11 monthly statements, Joe's own, WF parser live |
| Lombardo Westpac (AU) | `C:\COURT_CASE\10_PLAINTIFF_WESTPAC_STATEMENTS\` | 15 PDFs, 7 years, scored 100/100 |

Use Wells Fargo for primary demo testing (US trustee audience). Lombardo available for
AU jurisdiction demo and stress testing multi-file performance.

---

## Section 1 — Interface Changes

### Case Intake Header (new)
Added above the upload zone. Fields:
- Debtor name (text)
- Case number (text, e.g. `2024-BK-04467`)
- Jurisdiction dropdown: `US Chapter 7` / `AU Bankruptcy`
- Report tier dropdown (see Section 5)

These fields pre-fill the report header automatically. No analysis change — purely metadata.

### PURSUE/SKIP Verdict Block (new)
Fires after analysis completes. Positioned at the top of results, above signals.

- Large GREEN `PURSUE` or RED `SKIP` label
- Numeric score underneath (0–100, existing indicator score)
- Three auto-generated plain-English bullets — the top reasons to act or move on
- Example: *"105 digital asset transactions detected across Gemini and Binance —
  convertible assets likely present."*

**Threshold:** 50 and above = PURSUE. Below 50 = SKIP.

### Database Checks Panel (new)
Positioned below the verdict block, above the transaction table.
Six rows — one per database source:

| Database | Jurisdiction | Demo state |
|---|---|---|
| ASIC / ABN Lookup | AU | Pre-loaded demo result |
| PPSR | AU | Pre-loaded demo result |
| AFSA Bankruptcy Register | AU | Pre-loaded demo result |
| Property Title Search | AU/US | Pre-loaded demo result |
| Court Records | AU/US | Pre-loaded demo result |
| PACER | US | Pre-loaded demo result |

Each row shows: database name · status pill (HIT / CLEAR / PENDING) · one-line finding.
For Kristy's demo these show curated realistic results. Live integrations built post-demo.

---

## Section 2 — PURSUE/SKIP Scoring Logic

Uses the existing indicator score (0–100). Trustee mode applies weighted signal priorities:

| Signal | Weight | Reason |
|---|---|---|
| Crypto activity | HIGH | Convertible assets — highest recovery potential |
| Material transfers to related parties | HIGH | Preference claim targets |
| Round-trip cash flows | HIGH | Fraud indicator — clawback potential |
| Structuring / sub-threshold cash | HIGH | Concealment intent |
| OFAC / DFAT sanctions hit | CRITICAL | Stops everything — immediate escalation |
| Cross-border transfers | MEDIUM | Asset flight risk |
| Luxury spend | MEDIUM | Lifestyle vs claimed financial position |

**Auto-bullet generation:** Top 3 scoring signals generate plain-English sentences for
the verdict block. Templates pull from the existing `INTEL_TEMPLATES` dictionary with
trustee-specific language overlays.

**Score threshold:** 50+ = PURSUE. Below 50 = SKIP.
Threshold is configurable per matter — stored on the matter record for audit purposes.

---

## Section 3 — Real-Time Parsing + Transaction Hyperlinking

### Live Parsing Feed
Replaces the current generic scan overlay stages with a live feed per file:

```
LOADING 15 FILES — 12 MONTHS OF STATEMENTS
FILE 1 OF 15: Lombardo Westpac Jan–Jun 2017...
  READING PAGE 1 OF 12...
  EXTRACTING TRANSACTIONS...  ████████░░  34 found
  SIGNAL: 21 MAR 2017  GEMINI EXCHANGE  $4,200.00  ← CRYPTO
  SIGNAL: 15 APR 2017  MORNING MOON DEVELOPMENTS  $49,500.00  ← MATERIAL
FILE 2 OF 15: Lombardo Westpac Jul 2017–Feb 2018...
  ...
COMBINING 15 FILES — 847 TRANSACTIONS TOTAL
RUNNING 20 SIGNALS...
SANCTIONS SCREENING — OFAC + DFAT...
VERDICT READY
```

Transaction count increments live. Signals flash as they fire. By the time parsing
finishes the audience already knows what's coming before the verdict drops.

**Multi-file handling:** Progress tracked per file with a file counter. All files merge
into a single combined analysis at the end. Handles 12+ months (15+ PDFs, 800+
transactions) without timeout. Backend processes files sequentially; frontend polls `/analysis/progress/{matter_id}`
every 500ms and updates the live feed. Simple and reliable within the 1–2 week window.

### Transaction Hyperlinking to Source PDF
During parsing, pdfplumber provides page context. We add one field to every transaction:
`page_number` (integer).

**Backend change:** `bank_parser.py` — record `page_number` on every extracted
transaction row. Minor addition, ~5 lines per parser.

**Frontend change:** Each transaction row in the table gets a document icon (🔗).
Click it → PDF.js embedded viewer opens, jumping directly to `page_number` in the
source file.

**Multi-file:** Link resolves to the correct source file + page automatically using
`source_file` (already tracked) + `page_number`.

This is chain of custody built into the interface. Every finding is one click from
its source document.

---

## Section 4 — Transaction Segregation + Revision Workbench

### Four-Tab Transaction Table
The existing transaction table gains four tabs — same dataset, four views:

| Tab | Contents |
|---|---|
| **Flagged** | Transactions with a signal — grouped by signal type |
| **All Transactions** | Full chronological ledger from all files |
| **High Value** | Transactions above material threshold (default $600, tunable per matter) |
| **Analyst Revised** | Only transactions the analyst has corrected, annotated, or reclassified |

Default view on load: Flagged tab.

### Revision Workbench — Inline Edit on Every Row
Every transaction row has an edit icon. Click → inline panel opens with three sections:

**1. Correct (parser error fix)**
- Editable: date, merchant/description, amount, direction (debit/credit)
- Original machine value preserved alongside — never overwritten
- Corrected rows marked with pencil icon (✏) in all table views and in the report
- Correction writes to `transaction_revisions` table: matter_id, transaction_id, field,
  original_value, revised_value, analyst_id, timestamp

**2. Annotate (analyst commentary)**
- Free text note field
- Example: *"This is rent — not a related party transfer"*
- Note appears in the report under that transaction row
- Auto-stamped with analyst name + timestamp

**3. Reclassify (signal adjustment)**
- Signal category dropdown — change to any of the 20 signal types
- Severity selector: RED / AMBER / GREEN
- False positive toggle — excludes from score, stays visible in Analyst Revised tab
  with reason recorded
- Score recalculates immediately after any reclassification

**Report output:**
- Report footer shows: *"847 transactions machine-analysed. 12 analyst-revised.
  3 marked false positive."*
- Analyst Revised tab printable as a standalone exhibit

---

## Section 5 — Report Tiers (restored from v11)

Five modes restored from the original BOSGAME/v11 `_ATTY_LABELS` implementation.
Selected in the case intake dropdown:

| Mode | Label in Report | Signal weighting |
|---|---|---|
| `trustee` | Trustee in Bankruptcy | Preference transactions, insider transfers, estate assets |
| `divorce` | Family Law / Divorce | Asset dissipation, lifestyle spend, third-party transfers |
| `civil` | Civil Litigation | Transfers to defeat creditors, luxury vs claimed position |
| `criminal` | Criminal / Fraud Investigation | Maximum detail, nothing suppressed |
| `probate` | Estate / Probate | Income sources, closing balance history, asset transfers |

Each mode adds a purpose banner at the top of the report stating who it was prepared for
and what was prioritised. Same data — different professional lens.

For Kristy's demo: `trustee` mode pre-selected.

---

## Build Summary — What Exists vs What's New

| Feature | Status | Effort |
|---|---|---|
| Case intake header | New | Small — frontend fields only |
| PURSUE/SKIP verdict | New | Medium — threshold layer + auto-bullets |
| Database checks panel | New | Small — static demo data for Kristy |
| Live parsing feed (per-file) | Upgrade | Medium — replace overlay with SSE stream |
| Multi-file / 12 months | Exists | Minor — add file counter to progress |
| Transaction hyperlinking | New | Medium — page_number in parser + PDF.js |
| 4-tab segregation | Upgrade | Medium — tabs on existing table |
| Revision workbench | New | Large — DB table + inline edit UI + score recalc |
| Report tiers | Restore | Small — bring _ATTY_LABELS forward from v11 |

**Largest single build item:** Revision workbench (new DB table, inline UI, score recalc).
All other items are small-to-medium upgrades on top of a working engine.

---

## Demo Script for Kristy

1. Open Verify+ — show the Verify+ branding, clean trustee interface
2. Fill case intake — *"Let's take a real Chapter 7 debtor"*
3. Drop 11 Wells Fargo PDFs — *"12 months of statements, just uploaded"*
4. Watch live feed — transactions fire in real-time, signals flash
5. Verdict drops — PURSUE at 73/100 or equivalent
6. Walk through Flagged tab — show signal groupings
7. Click a transaction — PDF viewer opens at source page (*"that's chain of custody"*)
8. Open a transaction, annotate it — *"forensic guys can correct and sign off before court"*
9. Hit Generate Report — select Trustee mode — show the court-ready output
10. Show database checks panel — *"when fully integrated, these also fire on every case"*
11. Close: *"Your platform gets the case. Our platform finds what's in it."*

---

## UI Theme

Use the lighter navy/blue palette established in the recent Verify UI work — not the
original near-black `#030303` from the BOSGAME box. Day/night mode toggle included.

- **Day mode:** Light background (`#f4f7fb`), dark navy text, gold accents
- **Night mode:** Medium navy (`#0d1f3c` — not near-black), white text, gold accents
- Toggle persists to localStorage per user session
- Verify+ inherits this — no new design system, just the updated palette

---

## Out of Scope for Kristy Demo

- Live database integrations (ASIC, PPSR, PACER, property) — demo data only
- Australian jurisdiction for this demo — US Wells Fargo statements
- Full Verify+ product pricing / licensing model
- Integration API spec with Kristy's platform — next conversation

---

*Spec written by Tina — Bartilotta AI agent.*
*Next step: implementation plan via writing-plans skill.*
