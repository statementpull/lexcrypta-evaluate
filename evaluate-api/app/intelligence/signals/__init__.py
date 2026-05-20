from .cash_flow import run as _cash_flow
from .intercompany import run as _intercompany
from .vendor import run as _vendor
from .digital_asset import run as _digital_asset
from .liability import run as _liability
from .behavioural import run as _behavioural


def run_all_signals(
    transactions: list[dict],
    pl_rows: list[dict] | None = None,
    disclosed_entities: list[str] | None = None,
    disclosed_vendors: list[str] | None = None,
    loader=None,
) -> list[dict]:
    results = []
    results += _cash_flow(transactions, pl_rows or [])
    results += _intercompany(transactions, disclosed_entities or [])
    results += _vendor(transactions, disclosed_vendors)
    results += _digital_asset(transactions, loader=loader)
    results += _liability(transactions)
    results += _behavioural(transactions)
    return results
