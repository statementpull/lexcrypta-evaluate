"""Signal 27: Technology & SaaS Dependency Risk.

Modern businesses rely on SaaS and cloud infrastructure that may not
automatically transfer to a new owner. Mission-critical software agreements
often contain: assignment restrictions, per-seat pricing that changes at
scale, data portability limitations, and vendor right-to-terminate clauses.

Additional risks:
  - Undisclosed technical debt: very low IT spend suggests deferred upgrades
  - Over-dependence on a single platform: if one platform fails, the business stops
  - Cybersecurity under-investment: no security tooling = buyer inherits breach risk
  - Outdated legacy systems: migration costs not reflected in the asking price

SaaS/cloud costs as a % of revenue:
  < 1% = possible under-investment (tech-dependent business)
  1–5% = typical for most SMEs
  > 10% = high tech cost structure — may compress margins post-acquisition

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict

CLOUD_KEYWORDS = [
    "AMAZON WEB SERVICES", "AWS ", "GOOGLE CLOUD", "MICROSOFT AZURE", "AZURE ",
    "DIGITALOCEAN", "LINODE", "VULTR", "HEROKU", "RACKSPACE", "CLOUDFLARE",
    "FASTLY", "VERCEL", "NETLIFY",
]

CRM_ERP_KEYWORDS = [
    "SALESFORCE", "HUBSPOT", "ZOHO", "PIPEDRIVE", "MONDAY.COM",
    "SAP ", "ORACLE ", "NETSUITE", "DYNAMICS 365", "SAGE ", "EPICOR",
]

PAYMENT_PLATFORM_KEYWORDS = [
    "STRIPE", "SQUARE ", "PAYPAL ", "BRAINTREE", "ADYEN", "AUTHORIZE.NET",
    "SHOPIFY", "WOOCOMMERCE", "BIGCOMMERCE",
]

PRODUCTIVITY_KEYWORDS = [
    "MICROSOFT 365", "OFFICE 365", "GOOGLE WORKSPACE", "G SUITE",
    "SLACK ", "ZOOM ", "TEAMS ", "DOCUSIGN", "DROPBOX", "BOX.COM",
]

SECURITY_KEYWORDS = [
    "CROWDSTRIKE", "SENTINELONE", "CARBONBLACK", "SOPHOS", "WEBROOT",
    "MALWAREBYTES", "PALO ALTO", "FORTINET", "CISCO SECURITY",
    "KNOWBE4", "PROOFPOINT", "MIMECAST", "BARRACUDA",
    "CYBER SECURITY", "CYBERSECURITY", "ENDPOINT SECURITY",
    "BACKUP SERVICE", "BACKBLAZE", "ACRONIS", "VEEAM", "DATTO",
]

ACCOUNTING_KEYWORDS = [
    "QUICKBOOKS", "XERO ", "FRESHBOOKS", "WAVE ACCOUNTING",
    "SAGE ACCOUNTING", "ZOHO BOOKS",
]

ALL_SAAS_KEYWORDS = (
    CLOUD_KEYWORDS + CRM_ERP_KEYWORDS + PAYMENT_PLATFORM_KEYWORDS +
    PRODUCTIVITY_KEYWORDS + SECURITY_KEYWORDS + ACCOUNTING_KEYWORDS
)


def _sum_rows_revenue(pl_rows) -> float:
    if not pl_rows:
        return 0.0
    rev_kw = ["REVENUE", "SALES", "INCOME", "NET SALES"]
    total = 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        if any(kw in acc for kw in rev_kw):
            for key in ("ytd", "amount", "value", "this_month"):
                v = r.get(key)
                if v is not None:
                    try:
                        val = float(re.sub(r"[,$\s%]", "", str(v)))
                        if val != 0:
                            total += val
                            break
                    except (ValueError, TypeError):
                        pass
    return total


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    cloud_txns, crm_txns, payment_txns = [], [], []
    security_txns, productivity_txns, accounting_txns = [], [], []
    platform_spend: dict[str, float] = defaultdict(float)

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()

        hit = False
        if any(kw in merchant for kw in CLOUD_KEYWORDS):
            cloud_txns.append(t); hit = True
        if any(kw in merchant for kw in CRM_ERP_KEYWORDS):
            crm_txns.append(t); hit = True
        if any(kw in merchant for kw in PAYMENT_PLATFORM_KEYWORDS):
            payment_txns.append(t); hit = True
        if any(kw in merchant for kw in SECURITY_KEYWORDS):
            security_txns.append(t); hit = True
        if any(kw in merchant for kw in PRODUCTIVITY_KEYWORDS):
            productivity_txns.append(t); hit = True
        if any(kw in merchant for kw in ACCOUNTING_KEYWORDS):
            accounting_txns.append(t); hit = True

        if hit:
            key = merchant[:40].strip()
            platform_spend[key] += abs(t["amount"])

    all_tech = cloud_txns + crm_txns + payment_txns + security_txns + productivity_txns + accounting_txns
    if not all_tech:
        return []

    total_tech = sum(abs(t["amount"]) for t in all_tech)
    revenue = _sum_rows_revenue(pl_rows)
    tech_pct = total_tech / revenue if revenue > 0 else 0

    # Top platforms
    top_platforms = sorted(platform_spend.items(), key=lambda x: -x[1])[:6]
    platform_text = " | ".join(f"{p[:25]} ${v:,.0f}" for p, v in top_platforms)

    # Concentration: single platform > 60% of tech spend
    if top_platforms:
        top_pct = top_platforms[0][1] / total_tech
        concentration_note = ""
        if top_pct > 0.60:
            concentration_note = (
                f" PLATFORM CONCENTRATION: '{top_platforms[0][0][:25]}' = {top_pct:.0%} of tech spend — "
                "single platform dependency creates operational risk if relationship changes at acquisition."
            )

    # Security check
    security_note = ""
    if not security_txns:
        security_note = (
            " NO CYBERSECURITY SPEND DETECTED — business may lack endpoint protection, backup, "
            "or security monitoring. Buyer inherits breach risk and remediation costs. "
            "Cyber due diligence assessment recommended before closing."
        )

    # Assignment risk
    assignment_note = (
        "SaaS agreement assignment risk: enterprise software (Salesforce, SAP, Oracle, NetSuite) "
        "typically requires vendor consent for transfer. Cloud contracts (AWS, Azure) "
        "are generally transferable but may require new account setup. "
        "Inventory all SaaS agreements and confirm transferability before closing."
    )

    severity = "red" if not security_txns and total_tech > 10000 else "amber"

    description = (
        f"Technology/SaaS spend: ${total_tech:,.0f} "
        f"{'(' + str(round(tech_pct*100,1)) + '% of revenue)' if revenue > 0 else ''}. "
        f"Platforms identified: {platform_text}. "
        f"{concentration_note if top_platforms else ''}"
        f"{security_note}"
        f" {assignment_note}"
    )

    results.append({
        "signal_type": "technology_risk",
        "severity": severity,
        "merchant": f"TECHNOLOGY DEPENDENCY: {len(platform_spend)} platforms | ${total_tech:,.0f}",
        "amount": -total_tech,
        "transaction_date": all_tech[0].get("transaction_date", ""),
        "description": description[:1500],
        "library_match": "TECHNOLOGY_SAAS_RISK",
        "confidence_weight": 0.60,
    })

    return results
