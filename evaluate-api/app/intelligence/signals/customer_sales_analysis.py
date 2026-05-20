"""Signal 56: Customer Sales Analysis — Revenue Concentration & Margin Anomalies.

Revenue concentration is the most common deal-killer that buyers don't model
properly. A business generating $2M revenue with 60% from one customer is not
a $2M business — it is a customer relationship with a business attached.

The Pareto principle (80/20) is the benchmark:
  Healthy: top customer < 20% of revenue; top 5 < 50%
  Elevated: top customer 20–35%
  High risk: top customer > 35%
  Critical: top customer > 50%

Gross margin by customer matters equally:
  A customer who generates high revenue but low margin may actually destroy
  value when overhead allocation is applied. Verify which customers are
  genuinely profitable.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""


def run(transactions: list[dict], pl_rows: list[dict] | None = None,
        loader=None, supplementary: dict | None = None) -> list[dict]:
    cs_rows = (supplementary or {}).get("customer_sales", [])
    if not cs_rows:
        return []

    results = []

    total_sales = sum(r.get("sales", 0) for r in cs_rows)
    if total_sales <= 0:
        return []

    total_customers = len(cs_rows)

    # Sort by sales descending (parser already does this, but be explicit)
    sorted_customers = sorted(cs_rows, key=lambda r: r.get("sales", 0), reverse=True)

    # ── Top customer concentration ─────────────────────────────────────────
    top1 = sorted_customers[0]
    top1_pct = top1["sales"] / total_sales

    if top1_pct > 0.50:
        results.append({
            "signal_type": "customer_sales_analysis",
            "severity": "red",
            "merchant": f"CRITICAL CONCENTRATION: {top1['customer']} = {top1_pct:.0%} of revenue",
            "amount": top1["sales"],
            "transaction_date": "",
            "description": (
                f"Top customer: {top1['customer']} — ${top1['sales']:,.0f} ({top1_pct:.0%} of total revenue ${total_sales:,.0f}). "
                f"Total customers: {total_customers}. "
                "This business is not a diversified operation — it is a single customer relationship. "
                "Loss of this customer would be an existential event. "
                "Buyer risk: (1) obtain the customer contract and verify term, renewal options, "
                "and change-of-control clauses — many contracts terminate automatically on business sale, "
                "(2) verify whether the seller has a personal relationship with the contact at this customer "
                "that does not transfer with the business, "
                "(3) model the deal assuming this customer exits — if the business cannot service "
                "acquisition debt without them, the deal structure is not viable, "
                "(4) require the seller to introduce the buyer to this customer and confirm continuity "
                "as a condition of close."
            ),
            "library_match": "CUSTOMER_CRITICAL_CONCENTRATION",
            "confidence_weight": 0.95,
        })
    elif top1_pct > 0.35:
        results.append({
            "signal_type": "customer_sales_analysis",
            "severity": "red",
            "merchant": f"HIGH CONCENTRATION: {top1['customer']} = {top1_pct:.0%} of revenue",
            "amount": top1["sales"],
            "transaction_date": "",
            "description": (
                f"Top customer: {top1['customer']} — ${top1['sales']:,.0f} ({top1_pct:.0%} of revenue). "
                "A single customer driving more than 35% of revenue is a material deal risk. "
                "Verify the contract, relationship transferability, and renewal history. "
                "Model the business with and without this customer — the spread between "
                "these two scenarios defines your deal floor."
            ),
            "library_match": "CUSTOMER_HIGH_CONCENTRATION",
            "confidence_weight": 0.85,
        })
    elif top1_pct > 0.20:
        results.append({
            "signal_type": "customer_sales_analysis",
            "severity": "amber",
            "merchant": f"ELEVATED CONCENTRATION: {top1['customer']} = {top1_pct:.0%} of revenue",
            "amount": top1["sales"],
            "transaction_date": "",
            "description": (
                f"Top customer: {top1['customer']} — ${top1['sales']:,.0f} ({top1_pct:.0%} of revenue). "
                "Above the 20% threshold that most acquisition due diligence considers elevated risk. "
                "Verify whether this is growing or shrinking as a share of revenue, "
                "and whether there are contractual protections in place."
            ),
            "library_match": "CUSTOMER_ELEVATED_CONCENTRATION",
            "confidence_weight": 0.70,
        })

    # ── Top 5 concentration ────────────────────────────────────────────────
    top5 = sorted_customers[:min(5, total_customers)]
    top5_sales = sum(r["sales"] for r in top5)
    top5_pct = top5_sales / total_sales if total_sales else 0

    if total_customers > 5 and top5_pct > 0.80:
        results.append({
            "signal_type": "customer_sales_analysis",
            "severity": "amber",
            "merchant": f"REVENUE CONCENTRATION: Top 5 customers = {top5_pct:.0%} of revenue",
            "amount": top5_sales,
            "transaction_date": "",
            "description": (
                f"Top 5 customers account for {top5_pct:.0%} of total revenue "
                f"(${top5_sales:,.0f} of ${total_sales:,.0f}) across {total_customers} total customers. "
                f"Top 5: {', '.join(r['customer'] for r in top5)}. "
                "Despite appearing diversified, revenue is effectively driven by a small cohort. "
                "Verify whether these top 5 have long-term contracts or are transactional. "
                "In a sale scenario, any of these relationships could be disrupted "
                "by the ownership change."
            ),
            "library_match": "CUSTOMER_TOP5_CONCENTRATION",
            "confidence_weight": 0.70,
        })

    # ── Gross margin anomalies ─────────────────────────────────────────────
    margin_rows = [r for r in cs_rows if r.get("gp_margin") is not None and r.get("cogs", 0) > 0]

    if margin_rows:
        avg_margin = sum(r["gp_margin"] for r in margin_rows) / len(margin_rows)

        # Flag outlier low-margin customers (more than 20pp below average)
        low_margin = [
            r for r in margin_rows
            if r["gp_margin"] < (avg_margin - 0.20) and r["sales"] / total_sales > 0.05
        ]
        if low_margin:
            for r in low_margin[:3]:
                results.append({
                    "signal_type": "customer_sales_analysis",
                    "severity": "amber",
                    "merchant": f"LOW-MARGIN CUSTOMER: {r['customer']} — {r['gp_margin']:.0%} GP vs {avg_margin:.0%} average",
                    "amount": r["sales"],
                    "transaction_date": "",
                    "description": (
                        f"{r['customer']}: revenue ${r['sales']:,.0f}, "
                        f"gross margin {r['gp_margin']:.0%} "
                        f"(average across reported customers: {avg_margin:.0%}). "
                        "This customer generates significantly below-average gross margin. "
                        "When overhead is allocated, this customer may be loss-making on a fully-loaded basis. "
                        "Verify: (1) is this a strategic account maintained at low margin? "
                        "(2) are the COGS correctly allocated to this customer? "
                        "(3) would exiting this customer improve overall business profitability?"
                    ),
                    "library_match": "CUSTOMER_LOW_MARGIN",
                    "confidence_weight": 0.65,
                })

        # Flag outlier negative-margin customers
        neg_margin = [r for r in margin_rows if r["gp_margin"] < 0 and r["sales"] / total_sales > 0.02]
        for r in neg_margin[:3]:
            results.append({
                "signal_type": "customer_sales_analysis",
                "severity": "red",
                "merchant": f"NEGATIVE MARGIN: {r['customer']} — loses money on every sale",
                "amount": r["sales"],
                "transaction_date": "",
                "description": (
                    f"{r['customer']}: revenue ${r['sales']:,.0f}, "
                    f"gross margin {r['gp_margin']:.0%} — cost of sales exceeds revenue. "
                    "This customer is loss-making at the gross profit level — before any overhead. "
                    "Verify whether this is a data entry error, a contract with incorrect pricing, "
                    "or a deliberate strategic decision (e.g., a loss-leader). "
                    "If intentional: understand what the seller expects the buyer to do with this account."
                ),
                "library_match": "CUSTOMER_NEGATIVE_MARGIN",
                "confidence_weight": 0.80,
            })

    # ── Customer count / business type signal ─────────────────────────────
    if total_customers <= 3:
        results.append({
            "signal_type": "customer_sales_analysis",
            "severity": "red",
            "merchant": f"MICRO CUSTOMER BASE: Only {total_customers} customers in revenue report",
            "amount": total_sales,
            "transaction_date": "",
            "description": (
                f"The customer revenue report shows only {total_customers} customer(s) generating "
                f"total revenue of ${total_sales:,.0f}. "
                "An extremely small customer base is a binary concentration risk — "
                "the loss of any single customer is a material revenue event. "
                "Verify: (1) whether additional customers exist but are not reflected in this report, "
                "(2) whether the business is genuinely this concentrated, "
                "(3) what contractual protections or recurring revenue arrangements exist with each customer."
            ),
            "library_match": "CUSTOMER_MICRO_BASE",
            "confidence_weight": 0.85,
        })

    return results
