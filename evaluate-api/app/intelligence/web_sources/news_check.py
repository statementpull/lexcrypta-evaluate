"""Google News / RSS news check via public search feeds.

Searches for recent news mentions of the business name using
publicly available RSS/JSON news search endpoints.
Flags: lawsuits mentioned in press, regulatory actions, data breaches,
executive departures, fraud allegations, BBB complaints in press.

No API key required — uses public search feeds.
"""
import logging
import re
import httpx
from urllib.parse import quote_plus

log = logging.getLogger("evaluate.web.news")
TIMEOUT = 12

ALERT_KEYWORDS = [
    "lawsuit", "sued", "fraud", "investigation", "indicted", "charged",
    "data breach", "hack", "recall", "fine", "penalty", "violation",
    "bankruptcy", "bankrupt", "shutdown", "closed", "layoff", "layoffs",
    "class action", "settlement", "judgment", "lien", "seized",
    "arrested", "convicted", "fdic", "irs audit", "osha",
]


def search(business_name: str) -> list[dict]:
    try:
        query = quote_plus(f'"{business_name}"')
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        resp = httpx.get(url, timeout=TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0 LexCrypta-Evaluate/1.0"})
        if resp.status_code != 200:
            return []
        # Parse RSS manually — simple regex to avoid dependency on xml parser
        items = []
        titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", resp.text)
        links = re.findall(r"<link>(https://news\.google\.com[^<]+)</link>", resp.text)
        dates = re.findall(r"<pubDate>(.*?)</pubDate>", resp.text)
        for i, title in enumerate(titles[1:], 0):  # skip feed title
            items.append({
                "title": title,
                "link": links[i] if i < len(links) else "",
                "date": dates[i] if i < len(dates) else "",
            })
        return items[:10]
    except Exception as e:
        log.warning("News search failed: %s", e)
        return []


def analyse(business_name: str, state: str = "") -> list[dict]:
    items = search(business_name)
    if not items:
        return []

    flagged = []
    neutral = []

    for item in items:
        title_lower = item["title"].lower()
        matched_kw = [kw for kw in ALERT_KEYWORDS if kw in title_lower]
        if matched_kw:
            flagged.append({**item, "keywords": matched_kw})
        else:
            neutral.append(item)

    findings = []

    if flagged:
        kw_summary = list({kw for item in flagged for kw in item["keywords"]})[:6]
        headlines = [f["title"] for f in flagged[:4]]
        findings.append({
            "source_name": "Google News",
            "source_type": "web_news",
            "severity": "red" if any(k in ["fraud", "indicted", "convicted", "bankruptcy"] for k in kw_summary) else "amber",
            "title": f"NEWS ALERTS: {len(flagged)} negative article(s) — {', '.join(kw_summary[:3])}",
            "description": (
                f"News search: {len(flagged)} article(s) mentioning {business_name!r} "
                f"with alert keywords: {', '.join(kw_summary)}. "
                f"Recent headlines: {' | '.join(headlines)}. "
                "Negative press coverage signals legal, regulatory, or operational issues "
                "that may not appear in financial statements. "
                "Review each article for: (1) ongoing or concluded litigation, "
                "(2) regulatory enforcement actions, (3) data breaches or security incidents, "
                "(4) employee relations issues, (5) product/service recalls."
            ),
            "confidence": 0.70,
            "raw": flagged,
        })
    elif neutral:
        findings.append({
            "source_name": "Google News",
            "source_type": "web_news",
            "severity": "green",
            "title": f"NEWS CLEAR: {len(neutral)} article(s) — no alert keywords",
            "description": (
                f"News search: {len(neutral)} article(s) found for {business_name!r}. "
                "No alert keywords detected in headlines. "
                "Review manually for context — automated keyword scan may miss nuanced issues."
            ),
            "confidence": 0.55,
            "raw": neutral,
        })

    return findings
