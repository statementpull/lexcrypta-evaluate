MIN_ESCALATING_GAP = 10000.0  # Minimum income gap before escalating signal fires (Carmen)


def run_pass2_in_memory(
    declarations: list[dict],
    bank_rows: list[dict],
) -> list[dict]:
    sorted_decls = sorted(declarations, key=lambda d: d["tax_year"])
    gaps = []
    prior_income_gap = None

    for decl in sorted_decls:
        period_start = decl["period_start"]
        period_end = decl["period_end"]

        credits = [
            float(r.get("amount") or 0)
            for r in bank_rows
            if r.get("direction") == "credit"
            and r.get("transaction_date") is not None
            and period_start <= r["transaction_date"] <= period_end
        ]
        debits = [
            float(r.get("amount") or 0)
            for r in bank_rows
            if r.get("direction") == "debit"
            and r.get("transaction_date") is not None
            and period_start <= r["transaction_date"] <= period_end
        ]

        total_credits = sum(credits)
        total_debits = sum(debits)
        declared_income = float(decl.get("declared_income") or 0)
        declared_expenses = float(decl.get("declared_expenses") or 0)

        income_gap = total_credits - declared_income
        expense_gap = total_debits - declared_expenses

        is_escalating = (
            prior_income_gap is not None
            and income_gap > prior_income_gap
            and income_gap >= MIN_ESCALATING_GAP
        )

        gaps.append({
            "tax_year": decl["tax_year"],
            "period_start": period_start,
            "period_end": period_end,
            "bank_total_credits": total_credits,
            "declared_income": declared_income,
            "income_gap": income_gap,
            "bank_total_debits": total_debits,
            "declared_expenses": declared_expenses,
            "expense_gap": expense_gap,
            "is_escalating": is_escalating,
        })

        prior_income_gap = income_gap

    return gaps
