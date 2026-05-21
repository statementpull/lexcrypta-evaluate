# LexCrypta Verify — Cloud Deployment Design
**Date:** 2026-05-21  
**Product:** LexCrypta Verify (bank statement forensics)  
**Approach:** A — Minimal fork of evaluate-api  
**Status:** Approved — ready for implementation planning

---

## 1. Product Context

Three LexCrypta cloud products, each with its own backend:

| Product | Purpose | Audience |
|---|---|---|
| **Verify** | Bank statement forensics | Lawyers, accountants |
| **Evaluate** | M&A due diligence | Deal teams, M&A lawyers |
| **Verify+** | Trustee yes/no signal + why | Bankruptcy trustees |

This spec covers **Verify** only. Evaluate already has a deployed backend. Verify+ is a teaser within Verify — not a separate build at this stage.

---

## 2. Architecture

### Repositories / Services

```
verify-api/              ← new Railway service (forked from evaluate-api)
  app/
    main.py              ← matters endpoints
    models.py            ← Matter, Document, Signal, LASResult, License
    config.py
    database.py          ← SQLite (dev) / Postgres (prod via Railway)
    encryption.py        ← unchanged from evaluate-api
    rate_limiter.py      ← unchanged
    parsers/
      bank_parser.py     ← keep (core engine)
      file_detector.py   ← keep
      normalizer_util.py ← keep
      [all accounting parsers removed — MYOB, QB, balance sheet, etc.]
    intelligence/
      las_score.py       ← keep unchanged
      library_loader.py  ← keep (loads signal libraries)
      narrative.py       ← keep (generates intel card text)
      signals/           ← keep 8 signals, remove M&A-only ones
        digital_asset.py
        hidden_assets.py
        behavioural.py
        aml_structuring.py
        cash_flow.py
        real_estate.py
        owner_compensation.py
        liability.py
        [all others removed]
    reconciliation/      ← removed entirely (multi-source M&A logic, not needed)
    report/
      generator.py       ← adapt for Verify report format
  Dockerfile             ← unchanged from evaluate-api
  railway.toml           ← updated service name to "verify"
  requirements.txt       ← unchanged

verify-deploy/           ← new Netlify site
  index.html             ← lexcrypta_verify_terminal.html, fully wired
  fonts/                 ← local font files (no Google Fonts CDN)
  _redirects             ← /* /index.html 200
  netlify.toml           ← publish = "."
```

### Deployment Targets

| Component | Platform | URL pattern |
|---|---|---|
| verify-api | Railway | `https://lexcrypta-verify-production.up.railway.app` |
| verify-deploy | Netlify | `verify.lexcryptaglobal.com` (or Netlify subdomain initially) |

---

## 3. License Key

Format: `LEXV-XXXX-XXXX-XXXXXXXX` (HMAC-signed, same mechanism as evaluate-api's `LEXA-` keys)

**Demo key**: One generic key is set as always-valid at deployment time via a Railway environment variable `DEMO_KEY` (e.g. `LEXV-DEMO-2026-BARTILOTTA`). If the submitted key matches `DEMO_KEY`, HMAC validation is bypassed. The actual key value is chosen at deployment — it just needs to match the `LEXV-` prefix format so the frontend input accepts it. This allows any invited prospect to activate the demo without distributing HMAC secrets.

```python
def _validate_license_key(key: str) -> bool:
    demo_key = os.getenv("DEMO_KEY", "")
    if demo_key and key.strip().upper() == demo_key.upper():
        return True
    # ... normal HMAC validation ...
```

The `DEMO_KEY` env var is set in Railway at deployment time.

---

## 4. API Endpoints

### Auth / License

```
GET  /health                    Railway health check — returns {"status": "ok"}
GET  /version                   Returns {"version": "v2026.05", "libraries": 8}
GET  /license-status            Returns {"activated": bool}
POST /activate-license          Body: {"key": "LEXV-..."} → {"activated": true} or 400
```

### Matters

```
POST /matters
  Body (form): subject, ref, type (bankruptcy|family_law|civil),
               matter_date, assigned_to, notes
  Returns: Matter object with id

GET  /matters
  Returns: list of Matter objects, each including las summary if run

DELETE /matters/{id}/purge
  Deletes matter, all documents, signals, and results
```

### Documents

```
POST /matters/{id}/upload
  Body (multipart): files[], zone (bank|cc|tax)
  Accepts: PDF, CSV up to 50MB per file
  Returns: {"uploaded": n, "file_ids": [...]}
```

### Analysis

```
POST /matters/{id}/run
  Triggers: extract transactions → run 8 signals → calculate LAS → build intel
  Returns: full result object (see Section 5)

GET  /matters/{id}/results
  Returns: same result object as /run, cached from last run
```

### Reports

```
GET  /reports/{id}             HTML forensic report for the matter
```

---

## 5. Result Object Shape

The `/run` and `/results` endpoints return this structure, designed to match the frontend exactly:

```json
{
  "matter_id": 1,
  "run_at": "2026-03-20T09:14:00Z",
  "exposure": "HIGH",
  "las": {
    "score": 84,
    "verdict": "DO FIRST",
    "verdict_cls": "high",
    "reason": "Crypto inflows in 90-day preference window · $165k income gap · High recovery probability via US subpoena.",
    "components": [
      {"label": "Signal Severity",    "val": 38, "max": 40},
      {"label": "Timing / Urgency",   "val": 22, "max": 25},
      {"label": "Financial Gap",      "val": 16, "max": 20},
      {"label": "Recovery Potential", "val":  8, "max": 15}
    ]
  },
  "signals": [
    {
      "name": "Crypto Exchange Activity",
      "cat": "ASSET · CONVERSION",
      "status": "detected",
      "count": 18,
      "amount": "$87,400"
    }
    // ... 15 more signal objects, status: detected | possible | none
  ],
  "intel": [
    {
      "cat": "ASSET · CONVERSION",
      "cat_cls": "asset",
      "title": "Crypto Exchange Activity",
      "narrative": "18 transactions totalling $87,400 to Coinbase (12, $52,100) and Kraken (6, $35,300). 14 transactions totalling $61,200 occurred within the 90-day preference window.",
      "rec": "High Recovery",
      "rec_cls": "high",
      "tier": "Tier 1 — Global Leaders",
      "path": "Coinbase: Standard US subpoena (18 U.S.C. § 2703). Well-documented compliance — expect KYC records, transaction history, wallet addresses. Est. 30–60 days."
    }
    // ... one intel card per detected signal
  ]
}
```

The frontend renders 16 signal types. The backend runs 8 signal libraries — the remaining 8 always return `status: "none", count: 0, amount: null`. This is correct behaviour: the frontend faithfully shows what was checked and found nothing. Fixed ordering must match the frontend's `SIGNALS` array exactly so index-based rendering is stable.

---

## 6. Demo Data

Seeded on startup if the database is empty. All matters are pre-analysed so the demo shows results immediately without needing to upload real files.

### Evaluate — 2 deals (already in evaluate-api, confirmed)

| Deal | Verdict | Key story |
|---|---|---|
| Coastal Packaging Pty Ltd | FAIL — Do Not Proceed | 4 breaches, $742k exposure. Creditors cleared pre-sale, $183k revenue gap, director withdrawals. Full HTML report. |
| Riverstone Logistics | PASS — Cleared to Proceed | All 6 signals clear. Demonstrates the system gives clean results. |

### Verify — 4 matters

| Matter | Type | LAS | Exposure | Story |
|---|---|---|---|---|
| Robert J. Henderson | Bankruptcy | 84 | HIGH | Coinbase + Kraken in 90-day window. Undisclosed Westpac mortgage. $165k income gap. **Showpiece — full 4 intel cards, Verify+ teaser** |
| Quantum Logistics Pty | Bankruptcy | 77 | HIGH | Structuring pattern. Cross-border transfers. Multiple exchange accounts. |
| Marchetti Family Trust | Family Law | 61 | MEDIUM | Hidden rental income. Mortgage not in disclosure. Missing statements flag. |
| Thornton v Thornton | Family Law | 22 | LOW | Clean result. Demonstrates no false positives. |

Henderson gets: all 16 signals rendered + 4 full intel cards + Verify+ teaser panel.

---

## 7. Verify+ Teaser

A locked preview panel rendered at the bottom of the matter results view for any HIGH-exposure matter. Built into the Verify frontend — not a separate product build.

### UI Behaviour
- Visible only when `exposure === "HIGH"` and matter is analysed
- Content is real but visually blurred via CSS (`filter: blur(5px)` + overlay)
- CTA links to `mailto:hello@lexcryptaglobal.com?subject=Verify+`

### Panel Content (Henderson, shown blurred)

```
VERIFY+  ·  TRUSTEE INTELLIGENCE LAYER
────────────────────────────────────────────────────────
Trustee Verdict       YES — PURSUE
Recovery Estimate     $247,400 identified
Priority Action       File preservation letter: Coinbase + Westpac subpoena

Verify+ adds trustee-grade signals: 90-day preference period analysis,
recovery probability scoring, and a single YES / NO / REFER verdict
with full documented reasoning for the trustee report.

[ Contact LexCrypta to unlock Verify+ → ]
```

### API
No new endpoint needed. The backend seeds Verify+ teaser data as a static JSON blob on the matter result object:

```json
"verify_plus_teaser": {
  "verdict": "YES — PURSUE",
  "recovery_estimate": "$247,400 identified",
  "priority_action": "File preservation letter: Coinbase · Westpac subpoena",
  "available": true
}
```

Frontend blurs it. Future Verify+ product simply removes the blur.

---

## 8. Frontend Wiring (verify-deploy)

Changes to `lexcrypta_verify_terminal.html`:

1. **Add `API_BASE` config** at top of script (same pattern as evaluate):
   ```js
   const CLOUD_API_URL = "https://lexcrypta-verify-production.up.railway.app";
   const API_BASE = CLOUD_API_URL || `http://${window.location.hostname}:8085`;
   ```

2. **Replace Google Fonts CDN** with local `fonts/fonts.css` (offline-safe, no CDN dependency)

3. **Wire `doLogin()`** — call `GET /license-status` on load, show license screen if not activated

4. **Wire `createMatter()`** — `POST /matters`

5. **Wire `loadDealsFromAPI()`** → renamed `loadMattersFromAPI()` — `GET /matters` on login

6. **Wire `runQueue()`** — for each queued matter, call `POST /matters/{id}/run`, poll until done, render result

7. **Wire `openMatter()`** — call `GET /matters/{id}/results` if matter has been run

8. **Add Verify+ teaser rendering** — in `renderIntel()`, after intel cards, check for `verify_plus_teaser` and render the blurred panel

9. **Remove demo simulation** — delete the `setInterval` fake progress in `runQueue()`, replace with real fetch + polling

---

## 9. Deployment Steps (in order)

1. Create `verify-api/` folder, fork evaluate-api into it
2. Strip reconciliation/, accounting parsers, M&A signals
3. Rename models/endpoints: deal → matter
4. Adapt `run_all_signals` for bank-only input — `las_score.py` is unchanged; `run_all_signals` calls only the 8 kept signal libraries
5. Seed demo data on startup
6. Set `DEMO_KEY` env var in Railway
7. Deploy to Railway, confirm `/health` responds
8. Create `verify-deploy/`, copy fonts, wire frontend
9. Update `API_BASE` to Railway URL
10. Deploy to Netlify, confirm end-to-end demo flow
11. Test: activate license → login → open Henderson → run → see results → Verify+ teaser

---

## 10. Out of Scope (this phase)

- Real Supabase auth (demo key is sufficient for launch)
- Stripe / payment gating
- Multi-firm / multi-tenant support
- Verify+ as a fully functional separate product
- PDF report for Verify (HTML report only initially)
