"""Build the Forensic Intelligence Report as print-ready HTML."""
from datetime import datetime, timezone

from app.config import verdict as get_verdict
from app.intelligence.narrative import generate_narrative

SEVERITY_COLOUR = {"red": "#7a1515", "amber": "#7a5200", "green": "#1a4d2e"}
SEVERITY_DOT_COLOUR = {"red": "#b03020", "amber": "#c47a08", "green": "#3a9968"}


def build_report(
    deal_name: str,
    deal_ref: str,
    deal_value: float,
    analysis_period_months: int,
    signals: list[dict],
    las_score: float,
    band: str,
) -> str:
    passed = band == "clear"
    verdict_text = get_verdict(passed)
    generated = datetime.now(timezone.utc).strftime("%d %b %Y")

    red = [s for s in signals if s["severity"] == "red"]
    amber = [s for s in signals if s["severity"] == "amber"]
    green = [s for s in signals if s["severity"] == "green"]

    findings_html = ""
    for s in red + amber + green:
        dot = SEVERITY_DOT_COLOUR.get(s["severity"], "#6b7a8d")
        narrative = generate_narrative(s)
        findings_html += f"""
        <div style="display:flex;gap:12px;padding:12px 0;border-bottom:1px solid rgba(8,8,16,0.08);">
          <div style="width:6px;height:6px;border-radius:50%;background:{dot};flex-shrink:0;margin-top:5px;"></div>
          <div style="color:#3a4454;font-size:12px;line-height:1.7;">
            <strong style="color:#0f1923;">{s['merchant']}</strong><br>{narrative}
            <span style="font-size:10px;color:#6b7a8d;margin-left:8px;">
              {s.get('transaction_date', '')} &nbsp;·&nbsp; ${abs(s.get('amount', 0)):,.0f}
            </span>
          </div>
        </div>"""

    verdict_colour = "#1a4d2e" if passed else "#7a1515"
    no_findings = (
        '<div style="padding:20px;color:#6b7a8d;font-style:italic;">'
        "No signals detected across all six intelligence categories."
        "</div>"
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LexCrypta Evaluate — {deal_name} — Forensic Intelligence Report</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500&family=DM+Sans:wght@300;400;500&family=DM+Mono:wght@300;400&display=swap');
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'DM Sans',sans-serif; background:#fff; color:#3a4454; font-size:13px; line-height:1.65; }}
  @media print {{ body {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
  .wrap {{ max-width:860px; margin:0 auto; padding:48px 40px; }}
  .hdr {{ display:flex; justify-content:space-between; align-items:center; padding-bottom:24px; border-bottom:3px solid #c8a96e; margin-bottom:32px; }}
  .logo {{ font-family:'Cormorant Garamond',serif; font-size:20px; color:#080810; letter-spacing:3px; }}
  .logo span {{ color:#c8a96e; }}
  .cls {{ font-size:8px; letter-spacing:2px; text-transform:uppercase; background:#c8a96e; color:#080810; padding:5px 12px; }}
  .deal-title {{ font-family:'Cormorant Garamond',serif; font-size:36px; font-weight:300; color:#080810; margin-bottom:4px; }}
  .deal-sub {{ font-size:13px; color:#6b7a8d; font-style:italic; margin-bottom:24px; }}
  .meta {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:rgba(8,8,16,0.1); border:1px solid rgba(8,8,16,0.1); margin-bottom:32px; }}
  .mc {{ background:#fff; padding:14px 18px; }}
  .ml {{ font-size:8px; letter-spacing:2px; color:#6b7a8d; margin-bottom:5px; text-transform:uppercase; font-family:'DM Mono',monospace; }}
  .mv {{ font-family:'Cormorant Garamond',serif; font-size:18px; color:#0f1923; font-weight:500; }}
  .mv.vd {{ color:{verdict_colour}; font-size:22px; }}
  .sl {{ font-size:8px; letter-spacing:3px; text-transform:uppercase; color:#c8a96e; margin-bottom:12px; font-family:'DM Mono',monospace; }}
  .fb {{ border:1px solid rgba(8,8,16,0.1); padding:0 20px; margin-bottom:32px; }}
  .las {{ background:rgba(8,8,16,0.04); padding:20px; margin-bottom:32px; display:flex; align-items:center; gap:20px; }}
  .lnum {{ font-family:'Cormorant Garamond',serif; font-size:48px; color:#0f1923; line-height:1; }}
  .foot {{ border-top:1px solid rgba(8,8,16,0.1); padding-top:20px; font-size:10px; color:#6b7a8d; display:flex; justify-content:space-between; }}
</style>
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
    <div class="mc"><div class="ml">Lexi Verdict</div><div class="mv vd">{verdict_text}</div></div>
  </div>
  <div class="las">
    <div class="lnum">{las_score:.0f}</div>
    <div>
      <div style="font-weight:500;color:#0f1923;font-size:12px;">LAS Score ({band.upper()})</div>
      <div style="font-size:11px;color:#6b7a8d;">Signal Severity · Timing · Financial Gap · Recovery Complexity</div>
    </div>
  </div>
  <div class="sl">Key Findings · {len(red)} Critical · {len(amber)} Elevated · {len(green)} Clear</div>
  <div class="fb">{findings_html or no_findings}</div>
  <div class="foot">
    <div>LexCrypta Evaluate · Forensic Intelligence Terminal · {deal_ref}</div>
    <div>Generated {generated} · Lexi Intelligence Libraries v2026.04</div>
  </div>
</div>
</body>
</html>"""
