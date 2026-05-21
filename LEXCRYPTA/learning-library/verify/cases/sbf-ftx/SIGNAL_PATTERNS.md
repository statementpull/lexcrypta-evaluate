# SBF/FTX — Verified Signal Patterns for LexCrypta Verify
**Assessment date:** 2026-05-21
**Verdict:** 3 patterns implementable in Verify. 2 belong in Evaluate. 5 not detectable from bank statements alone.

---

## IMPLEMENT IN VERIFY

---

### SIGNAL_SBF_001 — Political Entity Payment Activity
**Category:** Hidden Asset Distribution
**Severity:** AMBER
**Detectable from bank statements:** YES — PAC names and political committee names appear directly in wire descriptions

**What it catches:**
Large or recurring payments from a business or personal account to political action committees, campaign funds, or political nonprofits. In the FTX case, $70M+ flowed from Alameda operating accounts through executive personal accounts to PACs. Each step showed as a normal wire.

**Why it matters in litigation:**
Political donations are a mechanism for extracting wealth from a business while appearing to make a public-interest payment. In divorce, bankruptcy, and civil asset tracing, money paid to a PAC is money that left the estate.

**Bank statement signals:**
- Large wires to known PAC names (Protect Our Future PAC, One Nation, Act Blue, Save America, House Majority PAC, Senate Majority PAC, NRSC, etc.)
- Payments to entities containing: PAC, POLITICAL, COMMITTEE, ELECT, CAMPAIGN, MAJORITY, LEADERSHIP FUND, ACTION NETWORK, SENATE FUND
- Multiple individuals at same address making near-identical contributions to same PAC (straw donor pattern — cross-matter signal if counterparty library shows same PAC appearing across matters)

**Keyword list for implementation:**
```
PROTECT OUR FUTURE, ACT BLUE, ACTBLUE, SAVE AMERICA, HOUSE MAJORITY PAC,
SENATE MAJORITY PAC, ONE NATION, AMERICAN ACTION NETWORK, SENATE LEADERSHIP FUND,
TEAM MCCONNELL, NRSC, EMILY'S LIST, GMI PAC, FF PAC, WINRED, DCCC, DSCC, NRCC,
FEDERAL ELECTION COMMISSION, FEC, POLITICAL ACTION, LEADERSHIP PAC,
CAMPAIGN COMMITTEE, ELECT, FOR CONGRESS, FOR SENATE, FOR GOVERNOR
```

**Flag threshold:** Any single payment > $1,000 to a known or probable political entity
**Status:** QUEUE — ready to implement

---

### SIGNAL_SBF_002 — Offshore Real Estate Cluster
**Category:** Hidden Assets / Wealth Extraction
**Severity:** AMBER
**Detectable from bank statements:** YES — cash real estate purchases show as large wires to title companies, escrow agents, and real estate law firms

**What it catches:**
Multiple large cash wire transfers to title companies, escrow agents, or real estate law firms within a 12–24 month window, particularly to offshore jurisdictions. FTX made 35 cash property purchases totalling $256M through FTX Property Holdings Ltd — every one was a wire to a Bahamas title company or escrow agent.

**Why it matters in litigation:**
Cash real estate purchases are a classic asset concealment method. The property doesn't appear on a bank balance but the cash is gone. In divorce and bankruptcy proceedings, identifying these wires is critical to the asset trace.

**Bank statement signals:**
- Large single wires (>$100K) to entities containing: TITLE, ESCROW, CONVEYANCING, SETTLEMENT, REALTY, PROPERTY, REAL ESTATE LAW
- Multiple such wires within 12 months to different title/escrow entities
- Offshore-jurisdiction real estate signals: wires to Bahamas, Cayman Islands, Belize, BVI, Panama, Turks and Caicos entities
- Wires to entities ending in "HOLDINGS LTD", "PROPERTY LTD", "REAL ESTATE LTD" (offshore property vehicle names)

**Flag threshold:** 2+ large wires to real estate/title entities within 12 months, OR any wire to an offshore real estate entity
**Status:** QUEUE — enhances existing real_estate signal, adds offshore dimension

---

### SIGNAL_SBF_003 — Insider Distribution Pattern (Loan-Disguised Extraction)
**Category:** Hidden Assets / Pass-Through
**Severity:** RED (when clustered)
**Detectable from bank statements:** PARTIAL — detectable when transactions are labeled "loan" or when the pattern (large round-number out to individual, no return) is present

**What it catches:**
Large transfers from business operating accounts to individuals (not businesses), either labeled as "loans" or appearing as unexplained round-number transfers. In FTX, $5B+ was extracted this way — SBF ($1B+), Singh ($543M), Salame ($35M). None were repaid.

**What makes it visible:**
- Transfers labeled "LOAN TO [NAME]", "PERSONAL LOAN", "EXEC LOAN"
- Large round-number outbound wires to what appear to be individual/personal accounts
- Pattern: large inbound (deposit-type credits) immediately followed by outbound to individuals
- No corresponding repayment inflows (loan should show repayment credits over time)

**Flag threshold:** Outbound wire > $10K to an individual name (not a business entity) from a business account, with no return credit within 90 days
**Status:** QUEUE — builds on existing pass-through detection

---

## EVALUATE PRODUCT (not Verify)

---

### SIGNAL_SBF_E01 — Circular Collateral / Native Token Lending
**Why not Verify:** Requires knowledge of what collateral was posted against which loans — this is a structured finance / balance sheet analysis question. Not visible in bank statements.
**Evaluate application:** In M&A due diligence, if a target company has loans collateralized by its own equity or tokens, flag as circular. Real case: Genesis lent Alameda billions with FTT (FTX's own token) as collateral.

### SIGNAL_SBF_E02 — Strategic Bank Acquisition by Operating Company
**Why not Verify:** The signal (a crypto/fintech firm buying a small bank at a wildly disproportionate price) is a regulatory/ownership pattern, not a bank transaction pattern.
**Evaluate application:** In M&A screening, flag if an acquirer has previously bought a financial institution at a price exceeding the institution's net worth — indicates the acquirer was seeking banking access outside normal compliance channels.

---

## NOT IMPLEMENTABLE (either product)

| Pattern | Why not implementable |
|---|---|
| `allow_negative` code backdoor | Internal software — not visible externally |
| Alternative balance sheets (Alt 7) | Requires comparison of multiple versions of the same document |
| Emoji approval system | Internal process — not visible in financial records |
| Straw donor cross-entity correlation | Requires matching individuals across matters by employer — possible only at platform scale |
| Deltec → Tether USDT conversion chain | Requires blockchain analysis, not bank statement analysis (covered by crypto signal) |

---

## Implementation Notes

**SIGNAL_SBF_001 (Political Payments)** is the highest-value addition. It's a completely new signal category not currently in Verify. Political donations as wealth extraction is a documented litigation pattern — not just FTX. Useful in:
- Divorce proceedings (spouse funneling money to PAC before filing)
- Bankruptcy (business owner draining estate to political causes)
- Civil fraud (redirecting investor funds)

**SIGNAL_SBF_002 (Offshore Real Estate)** enhances the existing `real_estate` signal module. Add the offshore-jurisdiction dimension and the clustering pattern.

**SIGNAL_SBF_003 (Insider Distribution)** is already partially covered by pass-through detection. The enhancement is: add "LOAN" keyword detection and the "outbound to individual name with no return" pattern.
