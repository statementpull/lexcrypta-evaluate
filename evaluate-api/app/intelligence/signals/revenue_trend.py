"""Signal 21: Revenue Trend & Business Trajectory.

Analyses month-over-month cash inflow trends to detect:
  - Declining revenue trajectory (sellers often exit at peak)
  - Accelerating growth (premium valuation may be justified)
  - Cliff-edge drop (sudden revenue loss — major deal risk)
  - Volatility / unpredictability (low-quality revenue)
  - Peak-and-plateau pattern (growth has stalled)

The most dangerous acquisition scenario: a business that looked great 12 months
ago but has been quietly declining. Sellers time exits to maximise price.
Bank data tells the real story — declared revenue in financials may reflect
the good years, not the current trajectory.

Methodology:
  - Extract monthly inflow totals from transaction dates
  - Compute trailing 3-month vs prior 3-month (momentum)
  - Compute linear regression slope for trend direction
  - Flag cliff drops (any month >35% below prior month average)
  - Flag volatility (coefficient of variation > 0.4)

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict
from datetime import datetime
import re


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _linear_slope(values: list[float]) -> float:
    """Simple OLS slope — positive = growing, negative = declining."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    # Build monthly inflow totals
    monthly: dict[str, float] = defaultdict(float)
    for t in transactions:
        if t["amount"] <= 0:
            continue
        d = _parse_date(t.get("transaction_date", ""))
        if not d:
            continue
        key = f"{d.year}-{d.month:02d}"
        monthly[key] += t["amount"]

    if len(monthly) < 3:
        return []

    sorted_months = sorted(monthly.keys())
    values = [monthly[m] for m in sorted_months]
    n = len(values)

    avg_monthly = sum(values) / n
    if avg_monthly <= 0:
        return []

    results = []

    # ── Trend direction (linear regression slope) ─────────────────────────────
    slope = _linear_slope(values)
    slope_pct_per_month = slope / avg_monthly  # as % of average

    # ── Momentum: last 3 months vs prior 3 months ────────────────────────────
    if n >= 6:
        recent = values[-3:]
        prior = values[-6:-3]
        recent_avg = sum(recent) / 3
        prior_avg = sum(prior) / 3
        momentum_pct = (recent_avg - prior_avg) / prior_avg if prior_avg > 0 else 0
    else:
        recent_avg = values[-1]
        prior_avg = values[0]
        momentum_pct = (recent_avg - prior_avg) / prior_avg if prior_avg > 0 else 0

    # ── Volatility (coefficient of variation) ─────────────────────────────────
    variance = sum((v - avg_monthly) ** 2 for v in values) / n
    std_dev = variance ** 0.5
    cv = std_dev / avg_monthly

    # ── Cliff drop detection ──────────────────────────────────────────────────
    cliff_months = []
    for i in range(1, n):
        if values[i - 1] > 0:
            drop_pct = (values[i] - values[i - 1]) / values[i - 1]
            if drop_pct < -0.35:
                cliff_months.append((sorted_months[i], drop_pct, values[i], values[i - 1]))

    # ── Peak analysis ─────────────────────────────────────────────────────────
    peak_value = max(values)
    peak_month = sorted_months[values.index(peak_value)]
    latest_value = values[-1]
    peak_decay = (latest_value - peak_value) / peak_value if peak_value > 0 else 0

    # ── Annual run rate ───────────────────────────────────────────────────────
    trailing_3_annualised = recent_avg * 12
    full_period_annualised = avg_monthly * 12

    # ── Generate signals ──────────────────────────────────────────────────────

    # Declining trend
    if slope_pct_per_month < -0.03 and momentum_pct < -0.10:
        sev = "red" if momentum_pct < -0.20 else "amber"
        results.append({
            "signal_type": "revenue_trend",
            "severity": sev,
            "merchant": f"DECLINING REVENUE TRAJECTORY: {momentum_pct:.0%} momentum (last 3 vs prior 3 months)",
            "amount": trailing_3_annualised,
            "transaction_date": sorted_months[-1],
            "description": (
                f"Revenue is declining: last 3-month average ${recent_avg:,.0f}/month "
                f"vs prior 3-month average ${prior_avg:,.0f}/month ({momentum_pct:+.0%} momentum). "
                f"Linear trend: {slope_pct_per_month:.1%}/month. "
                f"Trailing 3-month annualised run rate: ${trailing_3_annualised:,.0f} "
                f"vs full-period annualised: ${full_period_annualised:,.0f}. "
                "Sellers frequently time exits at revenue peaks. Verify whether the asking price "
                "is based on trailing 12 months (which may include higher-revenue periods) "
                "vs the current run rate. Request the most recent 3 months of bank statements "
                "if not included, and rebase valuation to current trajectory."
            ),
            "library_match": "REVENUE_DECLINING",
            "confidence_weight": 0.80 if momentum_pct < -0.20 else 0.65,
        })

    # Strong growth
    elif slope_pct_per_month > 0.04 and momentum_pct > 0.15:
        results.append({
            "signal_type": "revenue_trend",
            "severity": "amber",
            "merchant": f"STRONG GROWTH TRAJECTORY: {momentum_pct:+.0%} momentum",
            "amount": trailing_3_annualised,
            "transaction_date": sorted_months[-1],
            "description": (
                f"Revenue shows strong upward trajectory: last 3-month average ${recent_avg:,.0f}/month "
                f"vs prior 3-month ${prior_avg:,.0f}/month ({momentum_pct:+.0%} momentum). "
                f"Trailing 3-month annualised run rate: ${trailing_3_annualised:,.0f}. "
                "High growth may justify premium multiples — verify growth is sustainable "
                "and not driven by one-time contracts, pre-acquisition pull-forward, "
                "or temporary market conditions. Validate growth drivers in customer interviews."
            ),
            "library_match": "REVENUE_GROWING",
            "confidence_weight": 0.55,
        })

    # Cliff drops
    if cliff_months:
        for month, drop_pct, after, before in cliff_months[:2]:
            results.append({
                "signal_type": "revenue_trend",
                "severity": "red",
                "merchant": f"REVENUE CLIFF DROP: {drop_pct:.0%} in {month}",
                "amount": after - before,
                "transaction_date": month,
                "description": (
                    f"Sudden revenue cliff: {month} inflows dropped {drop_pct:.0%} "
                    f"from ${before:,.0f} to ${after:,.0f}. "
                    "Single-month drops of this magnitude typically indicate: "
                    "(1) lost major customer, (2) seasonal low (verify with prior year), "
                    "(3) operational disruption (equipment failure, key employee departure), "
                    "(4) external shock (market, regulatory, competitive). "
                    "Verify the cause and whether revenue recovered in subsequent months."
                ),
                "library_match": "REVENUE_CLIFF",
                "confidence_weight": 0.80,
            })

    # Peak decay — currently well below historical peak
    if peak_decay < -0.30 and peak_month != sorted_months[-1]:
        results.append({
            "signal_type": "revenue_trend",
            "severity": "amber",
            "merchant": f"PEAK DECAY: current revenue {abs(peak_decay):.0%} below historical peak ({peak_month})",
            "amount": latest_value * 12,
            "transaction_date": sorted_months[-1],
            "description": (
                f"Business is operating {abs(peak_decay):.0%} below its peak monthly revenue. "
                f"Peak: ${peak_value:,.0f}/month in {peak_month}. "
                f"Current: ${latest_value:,.0f}/month ({sorted_months[-1]}). "
                "If the asking price was set when revenue was at peak, the valuation may no longer "
                "reflect current earning power. Rebase EBITDA and SDE to current run rate, "
                "not the peak period. Request explanation for the revenue decline since {peak_month}."
            ),
            "library_match": "REVENUE_PEAK_DECAY",
            "confidence_weight": 0.70,
        })

    # High volatility
    if cv > 0.40 and avg_monthly > 5000:
        results.append({
            "signal_type": "revenue_trend",
            "severity": "amber",
            "merchant": f"HIGH REVENUE VOLATILITY: CV {cv:.2f} (coefficient of variation)",
            "amount": 0,
            "transaction_date": "",
            "description": (
                f"Revenue volatility is high: coefficient of variation {cv:.2f} "
                f"(std dev ${std_dev:,.0f} on average ${avg_monthly:,.0f}/month). "
                f"Monthly range: ${min(values):,.0f} – ${max(values):,.0f}. "
                "High volatility indicates unpredictable cash flows — "
                "a key risk factor for debt serviceability and working capital planning. "
                "Identify the source: seasonal business (acceptable if predictable), "
                "project-based revenue (high renewal risk), or erratic sales (operational risk)."
            ),
            "library_match": "REVENUE_VOLATILITY",
            "confidence_weight": 0.55,
        })

    # Always add a trajectory summary if we have enough data
    if n >= 4 and not results:
        # Flat / stable — reassuring, still worth surfacing
        results.append({
            "signal_type": "revenue_trend",
            "severity": "amber",
            "merchant": f"REVENUE TRAJECTORY: {n}-month trend — {slope_pct_per_month:+.1%}/month",
            "amount": trailing_3_annualised,
            "transaction_date": sorted_months[-1],
            "description": (
                f"Revenue trend over {n} months: slope {slope_pct_per_month:+.1%}/month "
                f"({'growing' if slope > 0 else 'flat/declining'}). "
                f"Average monthly inflow: ${avg_monthly:,.0f}. "
                f"Trailing 3-month annualised: ${trailing_3_annualised:,.0f}. "
                f"Momentum (last 3 vs prior 3 months): {momentum_pct:+.0%}. "
                f"Monthly range: ${min(values):,.0f} – ${max(values):,.0f}. "
                f"Volatility (CV): {cv:.2f}."
            ),
            "library_match": "REVENUE_TREND_SUMMARY",
            "confidence_weight": 0.45,
        })

    return results
