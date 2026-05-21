# Case Study: United States v. Bankman-Fried (FTX / Alameda Research)
**Convicted:** November 2023 — 7 counts including wire fraud, securities fraud, money laundering
**Sentenced:** March 2024 — 25 years federal prison
**Financial loss:** ~$9 billion in customer funds; $11.3B identified by forensic accountant Peter Easton

---

## What Made This Case Different for Bank Statement Analysis

Most fraud cases involve hiding money. FTX involved *spending* customer money openly through legitimate-looking transactions — political donations, real estate purchases, VC investments, celebrity endorsements. Every outflow looked like a normal business transaction. The fraud was in the *source* of funds, not the transactions themselves.

This means bank statements from FTX-affiliated accounts showed:
- Normal-looking wire transfers to real PACs, real title companies, real VC funds
- Large round-number inbound wires from thousands of individuals
- Rapid depletion — inflows immediately followed by outflows in multiple directions
- Entity names that appeared legitimate (North Dimension Inc. looked like a tech company)

---

## The Core Mechanism (simplified)

1. FTX customers were instructed to wire deposits to **North Dimension Inc.** (an Alameda shell)
2. North Dimension held the money in Silvergate Bank accounts
3. Alameda treated these as its own operating funds — no segregation
4. Alameda's FTX trading account had a secret `allow_negative` flag — unlimited overdraft against customer funds
5. By collapse: $9 billion gap between what customers deposited and what existed

---

## Key Entities (appeared on bank transactions)

| Entity Name | Role |
|---|---|
| North Dimension Inc. | Primary shell; received customer wire deposits at Silvergate Bank |
| Alameda Research LLC | Operating entity; main account holder |
| Alameda Research Ltd. | Cayman Islands entity |
| FTX Trading Ltd. | Exchange entity (Antigua & Barbuda) |
| FTX Digital Markets Ltd. | Bahamas subsidiary |
| West Realm Shires Services Inc. | FTX US (domestic) |
| FTX Property Holdings Ltd | Real estate purchases |
| Emergent Fidelity Technologies Ltd. | Robinhood stake acquisition (Antigua) |
| Protect Our Future PAC | SBF's primary political vehicle |
| One Nation | Conservative nonprofit receiving Salame/Alameda donations |

---

## Banks Involved

| Bank | Role | Outcome |
|---|---|---|
| Silvergate Bank | ~20 accounts across FTX/Alameda; North Dimension held 2 accounts | Collapsed March 2023 |
| Signature Bank | FTX client | Collapsed March 2023 |
| Deltec Bank & Trust (Bahamas) | 17+ accounts: 9 Alameda, 7 FTX US, 1 FTX Trading | Still operating |
| Moonstone Bank (WA State) | Alameda invested $11.5M — 2x the bank's entire net worth | Wound down by Fed |
| Prime Trust | 7 wires totalling $47M from Alameda to SBF personal account | Collapsed 2023 |

---

## Money Flows (by category)

### Political Donations — $70–133M total
- **SBF personal**: ~$40M public, ~$47M dark money
- **Ryan Salame**: $23.7M (Republican) — sentenced 7.5 years for straw donor conspiracy
- **Nishad Singh**: $8.3M (Democratic) — funded by $543M "loan" from Alameda
- Source chain: Alameda Silvergate → personal accounts → PACs

### Real Estate — $256M across 35 Bahamas properties
- Entity: FTX Property Holdings Ltd
- All cash purchases (wire transfers, no mortgage)
- 7 units at Albany Resort including $30M penthouse
- SBF parents received $16.4M Bahamas property

### VC Investments — ~$5.3B across 250+ holdings
- Anthropic: $500M (FTX estate later sold for $1.3B)
- Genesis Digital Assets: $1.15B
- K5 Global (Michael Kives): $700M
- Robinhood (via Emergent Fidelity): $546–648M
- Sequoia: $200M

### Sports Sponsorships
- Miami Heat arena: $135M / 19-year deal
- Tom Brady: $55M
- Stephen Curry: $35M
- TSM esports: $210M naming rights

### Insider "Loans" — $5B+ total
- SBF personal: $1B+
- Nishad Singh: $543M
- Ryan Salame: $35M
- Caroline Ellison: $1.3M
- SBF parents: ~$26M combined

---

## Trial Evidence (Bank Records Presented)

- $16M in wires: Alameda Silvergate → SBF personal accounts (Aug–Oct 2022)
- $47M in 7 wires: Alameda Prime Trust → SBF personal Prime Trust account
- $546M promissory notes: Alameda → Emergent Fidelity (Robinhood purchase)
- North Dimension Silvergate account records — customer deposits received and immediately deployed
- Seven alternative Alameda balance sheets ("Alt 7" omitted $9.9B customer borrowing)
- FBI Agent Paige Owens analyzed thousands of pages of bank statements to trace political donation chains
