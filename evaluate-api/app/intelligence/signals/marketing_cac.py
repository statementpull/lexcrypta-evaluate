"""Signal 38: Marketing Spend & Customer Acquisition Cost (CAC) Analysis.

Marketing spend efficiency is one of the most revealing metrics in an
acquisition. A business that grows only by spending heavily on paid
marketing has a very different risk profile than one that grows organically.

What we detect:
  CAC trend: If marketing spend is rising faster than revenue, the business
    is buying growth at increasing cost — a sustainability red flag.
  Paid vs organic dependency: Heavy reliance on paid advertising means
    revenue stops the moment the ad budget is cut.
  Platform concentration: If all marketing is on one platform (Google, Meta),
    algorithm changes or policy changes can destroy revenue overnight.
  Marketing as % of revenue: Industry benchmarks vary widely.
    SaaS: 30–50% of revenue (normal for growth stage)
    Retail: 5–15%
    Professional services: 3–8%
    B2B: 5–12%
  Pre-sale marketing pullback: Reduced marketing spend pre-sale to inflate
    EBITDA — revenue will likely decline post-close as the marketing gap
    catches up.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
import re
from collections import defaultdict
from datetime import datetime


DIGITAL_AD_KW = [
    "GOOGLE ADS", "GOOGLE ADWORDS", "GOOGLE ADVERTISING",
    "FACEBOOK ADS", "META ADS", "INSTAGRAM ADS",
    "LINKEDIN ADS", "LINKEDIN ADVERTISING",
    "TWITTER ADS", "X ADS", "TIKTOK ADS",
    "YOUTUBE ADS", "BING ADS", "MICROSOFT ADS",
    "PINTEREST ADS", "SNAPCHAT ADS",
]

MARKETING_PLATFORM_KW = [
    "MAILCHIMP", "KLAVIYO", "HUBSPOT MARKETING", "MARKETO",
    "CONSTANT CONTACT", "ACTIVECAMPAIGN", "CONVERTKIT",
    "HOOTSUITE", "SPROUT SOCIAL", "BUFFER ",
    "SEMRUSH", "AHREFS", "MOZ ", "SCREAMING FROG",
]

PR_AGENCY_KW = [
    "PR AGENCY", "PUBLIC RELATIONS", "MARKETING AGENCY",
    "ADVERTISING AGENCY", "CREATIVE AGENCY", "DIGITAL AGENCY",
    "MEDIA BUY", "MEDIA PLACEMENT", "AD SPEND",
]

TRADITIONAL_AD_KW = [
    "RADIO AD", "TV COMMERCIAL", "TELEVISION AD", "BILLBOARD",
    "PRINT AD", "NEWSPAPER AD", "MAGAZINE AD",
    "DIRECT MAIL", "POSTCARD CAMPAIGN", "MAILER",
    "TRADE SHOW", "CONFERENCE BOOTH", "EXHIBITION FEE",
]


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _sum_rows_revenue(pl_rows) -> float:
    if not pl_rows:
        return 0.0
    for r in pl_rows:
        acc = str(r.get("account", "")).upper()
        if any(kw in acc for kw in ["REVENUE", "SALES", "NET SALES"]):
            for key in ("ytd", "amount", "value", "this_month"):
                v = r.get(key)
                if v is not None:
                    try:
                        val = float(re.sub(r"[,$\s%]", "", str(v)))
                        if val != 0:
                            return val
                    except (ValueError, TypeError):
                        pass
    return 0.0


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    digital_txns, platform_txns, pr_txns, traditional_txns = [], [], [], []
    platform_spend: dict[str, float] = defaultdict(float)

    for t in transactions:
        if t["amount"] >= 0:
            continue
        merchant = t["merchant"].upper()
        hit = False
        if any(kw in merchant for kw in DIGITAL_AD_KW):
            digital_txns.append(t); hit = True
            key = next((kw for kw in DIGITAL_AD_KW if kw in merchant), merchant[:30])
            platform_spend[key] += abs(t["amount"])
        if any(kw in merchant for kw in MARKETING_PLATFORM_KW):
            platform_txns.append(t); hit = True
        if any(kw in merchant for kw in PR_AGENCY_KW):
            pr_txns.append(t); hit = True
        if any(kw in merchant for kw in TRADITIONAL_AD_KW):
            traditional_txns.append(t); hit = True

    all_mktg = digital_txns + platform_txns + pr_txns + traditional_txns
    if not all_mktg:
        return []

    total_mktg = sum(abs(t["amount"]) for t in all_mktg)
    revenue = _sum_rows_revenue(pl_rows)
    mktg_pct = total_mktg / revenue if revenue > 0 else 0

    # ── Platform concentration ────────────────────────────────────────────────
    top_platform_note = ""
    if platform_spend:
        top = max(platform_spend.items(), key=lambda x: x[1])
        top_pct = top[1] / total_mktg
        if top_pct > 0.70:
            top_platform_note = (
                f" PLATFORM CONCENTRATION: {top_pct:.0%} of ad spend on '{top[0][:30]}'. "
                "Single-platform dependency means algorithm changes, policy violations, "
                "or account suspension can eliminate growth channel overnight."
            )

    # ── Pre-sale marketing pullback detection ─────────────────────────────────
    pullback_note = ""
    monthly_mktg: dict[str, float] = defaultdict(float)
    for t in all_mktg:
        d = _parse_date(t.get("transaction_date", ""))
        if d:
            monthly_mktg[f"{d.year}-{d.month:02d}"] += abs(t["amount"])
    if len(monthly_mktg) >= 4:
        months = sorted(monthly_mktg.keys())
        vals = [monthly_mktg[m] for m in months]
        recent = sum(vals[-2:]) / 2
        prior = sum(vals[:-2]) / max(len(vals) - 2, 1)
        if prior > 0 and recent < prior * 0.60:
            pullback_note = (
                f" PRE-SALE PULLBACK: recent marketing spend ${recent:,.0f}/month "
                f"is {recent/prior:.0%} of prior average ${prior:,.0f}/month. "
                "Sellers frequently cut marketing 6–12 months pre-sale to inflate EBITDA. "
                "Post-close revenue may decline as the marketing gap catches up. "
                "Normalize EBITDA by adding back the deficit to historical marketing levels."
            )

    severity = "red" if pullback_note or (revenue > 0 and mktg_pct > 0.30 and "SAAS" not in str(pl_rows)) else "amber"

    description = (
        f"Total marketing spend: ${total_mktg:,.0f} "
        f"{'(' + str(round(mktg_pct * 100, 1)) + '% of revenue)' if revenue > 0 else ''}. "
        f"Digital ads: ${sum(abs(t['amount']) for t in digital_txns):,.0f} | "
        f"Agencies/PR: ${sum(abs(t['amount']) for t in pr_txns):,.0f} | "
        f"Platforms: ${sum(abs(t['amount']) for t in platform_txns):,.0f}. "
        f"{top_platform_note}{pullback_note} "
        "Marketing DD: (1) verify whether revenue is organic or paid — ask for CAC and LTV data, "
        "(2) confirm marketing spend is normalized in EBITDA calculation, "
        "(3) request channel-by-channel ROI data, "
        "(4) for digital businesses, confirm Google Analytics access to verify traffic trends."
    )

    results.append({
        "signal_type": "marketing_cac",
        "severity": severity,
        "merchant": f"MARKETING & CAC: ${total_mktg:,.0f} spend{' | ' + str(round(mktg_pct*100,1)) + '% of revenue' if revenue > 0 else ''}",
        "amount": -total_mktg,
        "transaction_date": all_mktg[0].get("transaction_date", ""),
        "description": description[:1500],
        "library_match": "MARKETING_CAC_ANALYSIS",
        "confidence_weight": 0.60,
    })

    return results
