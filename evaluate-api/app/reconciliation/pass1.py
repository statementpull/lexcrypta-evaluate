from collections import defaultdict
from datetime import timedelta
from typing import Optional

from .normalizer import description_similarity


def find_match(
    bank_txn: dict,
    accounting_rows: list[dict],
    matched_ids: set,
    date_window_days: int = 3,
    near_tolerance: float = 1.0,
) -> tuple[Optional[dict], str]:
    bank_date = bank_txn["transaction_date"]
    bank_amount = float(bank_txn.get("amount") or 0)
    bank_norm = bank_txn.get("description_norm", "")

    candidates = []
    for row in accounting_rows:
        if row["id"] in matched_ids:
            continue
        row_date = row.get("transaction_date")
        if row_date is None:
            continue
        if abs((row_date - bank_date).days) > date_window_days:
            continue
        acct_amount = float(row.get("amount") or 0)
        amount_diff = abs(bank_amount - acct_amount)
        sim = description_similarity(bank_norm, row.get("description_norm", ""))
        candidates.append((row, amount_diff, sim))

    if not candidates:
        return None, "none"

    exact = [(r, d, s) for r, d, s in candidates if d < 0.005]
    if exact:
        best = max(exact, key=lambda x: x[2])
        return best[0], "exact"

    near = [(r, d, s) for r, d, s in candidates if d <= near_tolerance]
    if near:
        best = min(near, key=lambda x: x[1])
        return best[0], "near"

    return None, "none"


def run_pass1_in_memory(bank_rows: list[dict], acct_rows: list[dict]) -> list[dict]:
    matched_acct_ids = set()
    breaches = []

    for bank in bank_rows:
        match, match_type = find_match(bank, acct_rows, matched_acct_ids)

        if match_type == "exact":
            matched_acct_ids.add(match["id"])

        elif match_type == "near":
            matched_acct_ids.add(match["id"])
            gap = abs(float(bank.get("amount") or 0) - float(match.get("amount") or 0))
            breaches.append({
                "breach_type": "amount_mismatch",
                "bank_transaction_id": bank["id"],
                "accounting_transaction_id": match["id"],
                "bank_amount": float(bank.get("amount") or 0),
                "accounting_amount": float(match.get("amount") or 0),
                "gap_amount": gap,
                "transaction_date": bank.get("transaction_date"),
                "description": bank.get("description", ""),
                "severity": "amber",
                "library_signal": None,
                "library_source": None,
            })

        else:
            breaches.append({
                "breach_type": "unmatched_bank",
                "bank_transaction_id": bank["id"],
                "accounting_transaction_id": None,
                "bank_amount": float(bank.get("amount") or 0),
                "accounting_amount": None,
                "gap_amount": float(bank.get("amount") or 0),
                "transaction_date": bank.get("transaction_date"),
                "description": bank.get("description", ""),
                "severity": "red",
                "library_signal": None,
                "library_source": None,
            })

    for acct in acct_rows:
        if acct["id"] not in matched_acct_ids:
            breaches.append({
                "breach_type": "unmatched_accounting",
                "bank_transaction_id": None,
                "accounting_transaction_id": acct["id"],
                "bank_amount": None,
                "accounting_amount": float(acct.get("amount") or 0),
                "gap_amount": float(acct.get("amount") or 0),
                "transaction_date": acct.get("transaction_date"),
                "description": acct.get("description", ""),
                "severity": "amber",
                "library_signal": None,
                "library_source": None,
            })

    # Signal 6: Creditor Concentration Collapse
    breaches.extend(detect_creditor_collapse(acct_rows))

    return breaches


def detect_creditor_collapse(
    accounting_rows: list[dict],
    min_occurrences: int = 3,
    gap_months: int = 3,
) -> list[dict]:
    if not accounting_rows:
        return []

    dates = [r["transaction_date"] for r in accounting_rows if r.get("transaction_date")]
    if not dates:
        return []

    end_date = max(dates)
    cutoff = end_date - timedelta(days=gap_months * 30)

    by_desc: dict = defaultdict(list)
    for row in accounting_rows:
        if (row.get("direction") == "debit"
                and row.get("description_norm")
                and row.get("transaction_date")):
            by_desc[row["description_norm"]].append(row)

    signals = []
    for desc, rows in by_desc.items():
        if len(rows) < min_occurrences:
            continue
        before = [r for r in rows if r["transaction_date"] < cutoff]
        after = [r for r in rows if r["transaction_date"] >= cutoff]
        if len(before) >= min_occurrences and len(after) == 0:
            last_payment = max(r["transaction_date"] for r in before)
            avg_amount = sum(float(r.get("amount") or 0) for r in before) / len(before)
            signals.append({
                "breach_type": "creditor_collapse",
                "bank_transaction_id": None,
                "accounting_transaction_id": None,
                "bank_amount": None,
                "accounting_amount": avg_amount,
                "gap_amount": avg_amount,
                "transaction_date": last_payment,
                "description": (
                    f"CREDITOR COLLAPSE: {desc} — {len(before)} regular payments "
                    f"ceased {gap_months}+ months before period end"
                ),
                "severity": "amber",
                "library_signal": None,
                "library_source": None,
            })

    return signals


def enrich_breaches_with_intelligence(breaches: list[dict], loader) -> list[dict]:
    if loader is None:
        return breaches

    for breach in breaches:
        if breach.get("breach_type") != "unmatched_bank":
            continue
        desc = breach.get("description", "")
        if not desc:
            continue

        match = loader.match_gambling(desc)
        if not match:
            match = loader.match_crypto_exchange(desc)

        if match:
            breach["library_signal"] = (
                match.get("platform_name")
                or match.get("exchange_name")
                or match.get("name")
                or "Unknown"
            )
            breach["library_source"] = match.get("_source_library", "")

    return breaches
