"""Build Forensic Intelligence Reports as print-ready HTML."""
from datetime import datetime, timezone

from app.config import verdict as get_verdict

SEVERITY_DOT_COLOUR = {"red": "#b03020", "amber": "#c47a08", "green": "#3a9968"}


def _base_css() -> str:
    return """
  @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500&family=DM+Sans:wght@300;400;500&family=DM+Mono:wght@300;400&display=swap');
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'DM Sans',sans-serif; background:#fff; color:#3a4454; font-size:13px; line-height:1.65; }
  @media print { body { -webkit-print-color-adjust:exact; print-color-adjust:exact; } }
  .wrap { max-width:860px; margin:0 auto; padding:48px 40px; }
  .hdr { display:flex; justify-content:space-between; align-items:center; padding-bottom:24px; border-bottom:3px solid #c8a96e; margin-bottom:32px; }
  .logo { font-family:'Cormorant Garamond',serif; font-size:20px; color:#080810; letter-spacing:3px; }
  .logo span { color:#c8a96e; }
  .cls { font-size:8px; letter-spacing:2px; text-transform:uppercase; background:#c8a96e; color:#080810; padding:5px 12px; }
  .deal-title { font-family:'Cormorant Garamond',serif; font-size:36px; font-weight:300; color:#080810; margin-bottom:4px; }
  .deal-sub { font-size:13px; color:#6b7a8d; font-style:italic; margin-bottom:24px; }
  .meta { display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:rgba(8,8,16,0.1); border:1px solid rgba(8,8,16,0.1); margin-bottom:32px; }
  .mc { background:#fff; padding:14px 18px; }
  .ml { font-size:8px; letter-spacing:2px; color:#6b7a8d; margin-bottom:5px; text-transform:uppercase; font-family:'DM Mono',monospace; }
  .mv { font-family:'Cormorant Garamond',serif; font-size:18px; color:#0f1923; font-weight:500; }
  .sl { font-size:8px; letter-spacing:3px; text-transform:uppercase; color:#c8a96e; margin-bottom:12px; font-family:'DM Mono',monospace; }
  .fb { border:1px solid rgba(8,8,16,0.1); padding:0 20px; margin-bottom:32px; }
  .las { background:rgba(8,8,16,0.04); padding:20px; margin-bottom:32px; display:flex; align-items:center; gap:20px; }
  .lnum { font-family:'Cormorant Garamond',serif; font-size:48px; color:#0f1923; line-height:1; }
  .foot { border-top:1px solid rgba(8,8,16,0.1); padding-top:20px; font-size:10px; color:#6b7a8d; display:flex; justify-content:space-between; }
"""


def _signal_rows_html(signals: list[dict]) -> str:
    red = [s for s in signals if s["severity"] == "red"]
    amber = [s for s in signals if s["severity"] == "amber"]
    green = [s for s in signals if s["severity"] == "green"]
    html = ""
    for s in red + amber + green:
        dot = SEVERITY_DOT_COLOUR.get(s["severity"], "#6b7a8d")
        desc = s.get("description", "")
        html += f"""
        <div style="display:flex;gap:12px;padding:12px 0;border-bottom:1px solid rgba(8,8,16,0.08);">
          <div style="width:6px;height:6px;border-radius:50%;background:{dot};flex-shrink:0;margin-top:5px;"></div>
          <div style="color:#3a4454;font-size:12px;line-height:1.7;">
            <strong style="color:#0f1923;">{s.get('merchant','')}</strong><br>{desc}
            <span style="font-size:10px;color:#6b7a8d;margin-left:8px;">
              {s.get('transaction_date', '')} &nbsp;·&nbsp; ${abs(s.get('amount', 0)):,.0f}
            </span>
          </div>
        </div>"""
    return html or '<div style="padding:20px;color:#6b7a8d;font-style:italic;">No signals detected.</div>'


def build_report(
    deal_name: str,
    deal_ref: str,
    deal_value: float,
    analysis_period_months: int,
    signals: list[dict],
    las_score: float,
    band: str,
    contradictions_html: str = "",
) -> str:
    passed = band == "clear"
    verdict_text = get_verdict(passed)
    generated = datetime.now(timezone.utc).strftime("%d %b %Y")
    red = [s for s in signals if s["severity"] == "red"]
    amber = [s for s in signals if s["severity"] == "amber"]
    green = [s for s in signals if s["severity"] == "green"]
    verdict_colour = "#1a4d2e" if passed else "#7a1515"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LexCrypta Evaluate — {deal_name}</title>
<style>{_base_css()}</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div class="logo">Lex<span>Crypta</span> · Evaluate</div>
    <div class="cls">Confidential · Deal Intelligence</div>
  </div>
  <div style="font-family:'DM Mono',monospace;font-size:8px;letter-spacing:3px;color:#c8a96e;margin-bottom:8px;">FORENSIC INTELLIGENCE REPORT</div>
  <div class="deal-title">{deal_name}</div>
  <div class="deal-sub">Deal forensics — M&amp;A target assessment · {deal_ref}</div>
  <div class="meta">
    <div class="mc"><div class="ml">Generated</div><div class="mv">{generated}</div></div>
    <div class="mc"><div class="ml">Deal Value</div><div class="mv">${deal_value:,.0f}</div></div>
    <div class="mc"><div class="ml">Analysis Period</div><div class="mv">{analysis_period_months} months</div></div>
    <div class="mc"><div class="ml">Lexi Verdict</div><div class="mv" style="color:{verdict_colour};font-size:22px;">{verdict_text}</div></div>
  </div>
  <div class="las">
    <div class="lnum">{las_score:.0f}</div>
    <div>
      <div style="font-weight:500;color:#0f1923;font-size:12px;">LAS Score ({band.upper()})</div>
      <div style="font-size:11px;color:#6b7a8d;">Liability · Anomaly · Severity composite</div>
    </div>
  </div>
  <div class="sl">Key Findings · {len(red)} Critical · {len(amber)} Elevated · {len(green)} Clear</div>
  <div class="fb">{_signal_rows_html(signals)}</div>
  {contradictions_html}
  <div class="foot">
    <div>LexCrypta Evaluate · {deal_ref}</div>
    <div>Generated {generated} · Lexi Intelligence Libraries v2026.05</div>
  </div>
</div>
</body>
</html>"""


def build_deal_summary(
    deal_name: str,
    deal_ref: str,
    deal_value: float,
    signals: list[dict],
    las_score: float,
    band: str,
    contradictions_html: str = "",
) -> str:
    red = [s for s in signals if s["severity"] == "red"]
    amber = [s for s in signals if s["severity"] == "amber"]
    generated = datetime.now(timezone.utc).strftime("%d %b %Y")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LexCrypta — {deal_name} — Deal Summary</title>
<style>{_base_css()}</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div class="logo">Lex<span>Crypta</span> · Evaluate</div>
    <div class="cls">Deal Summary</div>
  </div>
  <div class="deal-title">{deal_name}</div>
  <div class="deal-sub">{deal_ref} · ${deal_value:,.0f} · {generated}</div>
  <div class="meta">
    <div class="mc"><div class="ml">LAS Score</div><div class="mv">{las_score:.0f}</div></div>
    <div class="mc"><div class="ml">Band</div><div class="mv">{band.upper()}</div></div>
    <div class="mc"><div class="ml">Critical</div><div class="mv" style="color:#b03020;">{len(red)}</div></div>
    <div class="mc"><div class="ml">Elevated</div><div class="mv" style="color:#c47a08;">{len(amber)}</div></div>
  </div>
  <div class="sl">Signal Summary</div>
  <div class="fb">{_signal_rows_html(signals)}</div>
  {contradictions_html}
  <div class="foot">
    <div>LexCrypta Evaluate · Deal Summary · {deal_ref}</div>
    <div>{generated}</div>
  </div>
</div>
</body>
</html>"""


def build_lawyer_summary(
    deal_name: str,
    deal_ref: str,
    signals: list[dict],
    las_score: float,
    band: str,
    contradictions_html: str = "",
    breaches: list[dict] = None,
    gaps: list[dict] = None,
) -> str:
    red = [s for s in signals if s["severity"] == "red"]
    amber = [s for s in signals if s["severity"] == "amber"]
    generated = datetime.now(timezone.utc).strftime("%d %b %Y")
    breaches = breaches or []
    gaps = gaps or []

    breach_rows = ""
    for b in breaches:
        dot = SEVERITY_DOT_COLOUR.get(b.get("severity", "amber"), "#c47a08")
        breach_rows += f"""
        <div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid rgba(8,8,16,0.06);">
          <div style="width:6px;height:6px;border-radius:50%;background:{dot};flex-shrink:0;margin-top:4px;"></div>
          <div style="font-size:12px;color:#3a4454;">{b.get('description','')}</div>
        </div>"""

    gap_rows = ""
    for g in gaps:
        gap_rows += f"""
        <div style="padding:10px 0;border-bottom:1px solid rgba(8,8,16,0.06);font-size:12px;color:#3a4454;">
          <strong>{g.get('tax_year','')}</strong> — declared ${g.get('declared_income',0):,.0f} vs
          bank ${g.get('bank_total_credits',0):,.0f} — gap ${abs(g.get('income_gap',0)):,.0f}
          {"&nbsp;<span style='color:#b03020;font-size:10px;'>ESCALATING</span>" if g.get('is_escalating') else ""}
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LexCrypta — {deal_name} — Legal Summary</title>
<style>{_base_css()}</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div class="logo">Lex<span>Crypta</span> · Evaluate</div>
    <div class="cls">Legal Practitioner Summary</div>
  </div>
  <div class="deal-title">{deal_name}</div>
  <div class="deal-sub">{deal_ref} · LAS {las_score:.0f} · {band.upper()} · {generated}</div>
  <div class="meta">
    <div class="mc"><div class="ml">Critical Signals</div><div class="mv" style="color:#b03020;">{len(red)}</div></div>
    <div class="mc"><div class="ml">Elevated Signals</div><div class="mv" style="color:#c47a08;">{len(amber)}</div></div>
    <div class="mc"><div class="ml">Reconciliation Breaches</div><div class="mv">{len(breaches)}</div></div>
    <div class="mc"><div class="ml">Revenue Gap Years</div><div class="mv">{len(gaps)}</div></div>
  </div>
  <div class="sl">Intelligence Signals</div>
  <div class="fb">{_signal_rows_html(signals)}</div>
  {f'<div class="sl">Reconciliation Breaches</div><div class="fb">{breach_rows}</div>' if breach_rows else ''}
  {f'<div class="sl">Revenue Gap Analysis</div><div class="fb">{gap_rows}</div>' if gap_rows else ''}
  {contradictions_html}
  <div class="foot">
    <div>LexCrypta Evaluate · Legal Summary · {deal_ref}</div>
    <div>Confidential · {generated}</div>
  </div>
</div>
</body>
</html>"""


def build_reconciliation_report(
    deal_name: str,
    deal_ref: str,
    deal_value: float,
    pass1: dict,
    pass2: dict,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%d %b %Y")
    breaches = pass1.get("breaches", [])
    red = [b for b in breaches if b.get("severity") == "red"]
    amber = [b for b in breaches if b.get("severity") == "amber"]
    total_income_gap = pass2.get("total_income_gap", 0)
    gaps = pass2.get("gaps", [])

    breach_rows = ""
    for b in breaches:
        dot = SEVERITY_DOT_COLOUR.get(b.get("severity", "amber"), "#c47a08")
        breach_rows += f"""
        <div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid rgba(8,8,16,0.06);">
          <div style="width:6px;height:6px;border-radius:50%;background:{dot};flex-shrink:0;margin-top:4px;"></div>
          <div style="font-size:12px;color:#3a4454;">{b.get('description','')}</div>
        </div>"""

    gap_rows = ""
    for g in gaps:
        gap_rows += f"""
        <div style="padding:10px 0;border-bottom:1px solid rgba(8,8,16,0.06);font-size:12px;">
          <strong>{g.get('tax_year','')}</strong> — bank ${g.get('bank_total_credits',0):,.0f}
          vs declared ${g.get('declared_income',0):,.0f}
          — gap <strong style="color:#b03020;">${abs(g.get('income_gap',0)):,.0f}</strong>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LexCrypta — {deal_name} — Reconciliation Report</title>
<style>{_base_css()}</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div class="logo">Lex<span>Crypta</span> · Evaluate</div>
    <div class="cls">Reconciliation Report</div>
  </div>
  <div class="deal-title">{deal_name}</div>
  <div class="deal-sub">{deal_ref} · ${deal_value:,.0f} · {generated}</div>
  <div class="meta">
    <div class="mc"><div class="ml">Critical Breaches</div><div class="mv" style="color:#b03020;">{len(red)}</div></div>
    <div class="mc"><div class="ml">Elevated Breaches</div><div class="mv" style="color:#c47a08;">{len(amber)}</div></div>
    <div class="mc"><div class="ml">Total Income Gap</div><div class="mv">${total_income_gap:,.0f}</div></div>
    <div class="mc"><div class="ml">Gap Years</div><div class="mv">{len(gaps)}</div></div>
  </div>
  <div class="sl">Pass 1 — Reconciliation Breaches</div>
  <div class="fb">{breach_rows or '<div style="padding:20px;color:#6b7a8d;font-style:italic;">No breaches detected.</div>'}</div>
  <div class="sl">Pass 2 — Revenue Gap Analysis</div>
  <div class="fb">{gap_rows or '<div style="padding:20px;color:#6b7a8d;font-style:italic;">No revenue gaps detected.</div>'}</div>
  <div class="foot">
    <div>LexCrypta Evaluate · Reconciliation Report · {deal_ref}</div>
    <div>{generated}</div>
  </div>
</div>
</body>
</html>"""


def build_contradiction_section(breaches: list[dict], gaps: list[dict]) -> str:
    if not breaches and not gaps:
        return ""

    rows = ""
    for b in breaches:
        dot = SEVERITY_DOT_COLOUR.get(b.get("severity", "amber"), "#c47a08")
        rows += f"""
        <div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid rgba(8,8,16,0.06);">
          <div style="width:6px;height:6px;border-radius:50%;background:{dot};flex-shrink:0;margin-top:4px;"></div>
          <div style="font-size:12px;color:#3a4454;">{b.get('description','')}</div>
        </div>"""

    for g in gaps:
        if abs(g.get("income_gap", 0)) > 5000:
            rows += f"""
        <div style="padding:10px 0;border-bottom:1px solid rgba(8,8,16,0.06);font-size:12px;color:#3a4454;">
          <strong>{g.get('tax_year','')} Income Gap</strong> — declared
          ${g.get('declared_income',0):,.0f} vs bank ${g.get('bank_total_credits',0):,.0f}
          — gap <strong style="color:#b03020;">${abs(g.get('income_gap',0)):,.0f}</strong>
        </div>"""

    if not rows:
        return ""

    return f"""
  <div class="sl" style="margin-top:24px;">Contradictions Detected</div>
  <div class="fb">{rows}</div>"""
