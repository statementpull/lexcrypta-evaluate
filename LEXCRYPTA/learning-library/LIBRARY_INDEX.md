# LexCrypta Signal Learning Library
**Read this first. Everything we learn from cases, books, and SEC filings lives here.**

---

## How This Works

Every case study, book, or SEC filing we research gets assessed before anything touches code.
Assessment answers one question: **can a bank statement actually show this?**

If yes → `verify/queue/` or `verify/implemented/`
If no but relevant to deal forensics → `evaluate/queue/`
If unclear → stays in `cases/` raw until we have enough to decide

---

## Verify Signal Library
*Bank statement analysis — transaction-level detection*

### Implemented (live in production)
| Signal | Source | Added |
|---|---|---|
| Crypto exchange detection | Tracers in the Dark (Greenberg) | Pre-library |
| Mortgage servicer activity | Internal / US servicer list | 2026-05-21 |
| Pass-through / commingling | Internal | 2026-05-21 |
| Cross-border transfers | Internal | 2026-05-21 |
| Structuring / smurfing | AML standards | Pre-library |
| PDF metadata integrity | Internal | 2026-05-21 |
| OFAC SDN screening | US Treasury SDN list | 2026-05-21 |

See `verify/implemented/` for detail on each.

### Queue (assessed, ready to implement)
| Pattern | Source | Priority |
|---|---|---|
| Political entity payments | SBF/FTX case | HIGH |
| Offshore real estate cluster | SBF/FTX case | MEDIUM |
| Insider distribution pattern | SBF/FTX case | MEDIUM |

See `verify/queue/` for specs.

### Cases (raw research, patterns extracted)
| Case | Files | Status |
|---|---|---|
| SBF / FTX (2022–2023) | `verify/cases/sbf-ftx/` | Patterns extracted → queue |
| Tracers in the Dark | Pre-library (crypto signals) | Implemented |

---

## Evaluate Signal Library
*Deal forensics — financial statement analysis (P&L, balance sheet, management accounts)*

### Queue (assessed, ready to implement)
| Pattern | Source | Priority |
|---|---|---|
| Cash flow quality divergence | Agilent SEC filings | HIGH |
| Serial acquisition goodwill impairment | Agilent SEC filings | HIGH |
| Serial non-recurring charges | Agilent SEC filings | MEDIUM |
| Variable effective tax rate | Agilent SEC filings | MEDIUM |
| Senior officer cluster departure | Agilent SEC filings | MEDIUM |
| Debt-funded buybacks during revenue decline | Agilent SEC filings | MEDIUM |
| China revenue concentration | Agilent SEC filings | MEDIUM |
| Executive pay vs non-GAAP gap | Agilent SEC filings | MEDIUM |
| Deferred revenue rapid expansion | Agilent SEC filings | LOW |
| CP / short-term debt dependency | Agilent SEC filings | LOW |

See `evaluate/queue/agilent-sec/EVALUATE_QUEUE.md` for full specs.

### Cases
| Case | Files | Status |
|---|---|---|
| Agilent Technologies (SEC EDGAR) | `evaluate/queue/agilent-sec/` | Queued |

---

## Future Learning Pipeline

**Books to process:**
- [ ] *Bad Blood* — Elizabeth Holmes / Theranos (corporate fraud, false statements)
- [ ] *Den of Thieves* — Ivan Boesky / Michael Milken (insider trading, junk bonds)
- [ ] *The Smartest Guys in the Room* — Enron (round-trip transactions, SPEs)
- [ ] *Billion Dollar Whale* — 1MDB / Jho Low (sovereign fund fraud, offshore flows)

**Cases to research:**
- [ ] Bernie Madoff — Ponzi scheme, bank statement patterns
- [ ] Allen Stanford — CD fraud, Antigua offshore banking
- [ ] Wirecard — phantom escrow, round-trip transactions
- [ ] 1MDB — sovereign wealth fund misappropriation

**Data feeds to add:**
- [ ] DFAT Consolidated Sanctions List (Australian equivalent of OFAC)
- [ ] ASIC Banned & Disqualified Persons register
- [ ] AUSTRAC typologies

---

## Rules for This Library

1. **Never implement without assessment.** Every pattern gets a "can bank statements show this?" check first.
2. **Wrong product = not wasted.** Patterns that don't fit Verify go to Evaluate queue. Nothing is thrown away.
3. **Source everything.** Every signal needs a real case or data source behind it.
4. **Agilent rule.** If a pattern requires financial statements (OCF, goodwill, ETR) → Evaluate, not Verify.
