from app.intelligence.signals.cash_flow import run as cash_flow_signal
from app.intelligence.signals.intercompany import run as intercompany_signal
from app.intelligence.signals.vendor import run as vendor_signal
from app.intelligence.signals.digital_asset import run as digital_asset_signal
from app.intelligence.signals.liability import run as liability_signal
from app.intelligence.signals.behavioural import run as behavioural_signal
from app.intelligence.las_score import calculate_las, severity_band

SAMPLE_TRANSACTIONS = [
    {"merchant": "ACME HOLDINGS PTY LTD", "amount": -15000.0, "transaction_date": "01/04/2026"},
    {"merchant": "ACME HOLDINGS PTY LTD", "amount": -15000.0, "transaction_date": "01/05/2026"},
    {"merchant": "ACME HOLDINGS PTY LTD", "amount": -15000.0, "transaction_date": "01/06/2026"},
    {"merchant": "COINBASE INC", "amount": -5000.0, "transaction_date": "10/04/2026"},
    {"merchant": "SPORTSBET", "amount": -200.0, "transaction_date": "12/04/2026"},
    {"merchant": "UNKNOWN VENDOR 99", "amount": -380000.0, "transaction_date": "15/04/2026"},
    {"merchant": "SALARY PAYMENT", "amount": 50000.0, "transaction_date": "30/04/2026"},
    {"merchant": "SALARY PAYMENT", "amount": 50000.0, "transaction_date": "31/05/2026"},
    {"merchant": "SALARY PAYMENT", "amount": 50000.0, "transaction_date": "30/06/2026"},
]


def test_intercompany_detects_recurring_transfers():
    results = intercompany_signal(SAMPLE_TRANSACTIONS, disclosed_entities=[])
    assert any(r["signal_type"] == "intercompany" for r in results)


def test_intercompany_no_signal_for_2_transactions():
    txns = [
        {"merchant": "ACME PTY LTD", "amount": -10000.0, "transaction_date": "01/04/2026"},
        {"merchant": "ACME PTY LTD", "amount": -10000.0, "transaction_date": "01/05/2026"},
    ]
    results = intercompany_signal(txns, disclosed_entities=[])
    assert len(results) == 0  # needs >= 3 identical transfers


def test_digital_asset_detects_coinbase(mock_loader):
    results = digital_asset_signal(SAMPLE_TRANSACTIONS, loader=mock_loader)
    assert any("COINBASE" in r["merchant"] for r in results)
    assert all(r["severity"] == "red" for r in results if "COINBASE" in r["merchant"])


def test_digital_asset_keyword_fallback():
    txns = [{"merchant": "BITCOIN WALLET TRANSFER", "amount": -1000.0, "transaction_date": "01/04/2026"}]
    results = digital_asset_signal(txns, loader=None)
    assert len(results) == 1
    assert results[0]["severity"] == "amber"


def test_vendor_detects_large_unknown_entity():
    txns = [{"merchant": "SHADOW ADVISORY PTY LTD", "amount": -75000.0, "transaction_date": "01/04/2026"}]
    results = vendor_signal(txns)
    assert len(results) == 1
    assert results[0]["severity"] == "red"


def test_vendor_ignores_small_amounts():
    txns = [{"merchant": "UNKNOWN CONSULTING PTY LTD", "amount": -4999.0, "transaction_date": "01/04/2026"}]
    results = vendor_signal(txns)
    assert len(results) == 0


def test_behavioural_detects_large_outflow():
    txns = [{"merchant": "UNKNOWN ENTITY", "amount": -500000.0, "transaction_date": "01/04/2026"}]
    results = behavioural_signal(txns)
    assert any(r["severity"] == "red" for r in results)


def test_cash_flow_runs_without_error():
    results = cash_flow_signal(SAMPLE_TRANSACTIONS, pl_rows=[])
    assert isinstance(results, list)


def test_liability_runs_without_error():
    results = liability_signal(SAMPLE_TRANSACTIONS)
    assert isinstance(results, list)


def test_las_no_signals():
    assert calculate_las([], total_transaction_volume=100000) == 0.0


def test_las_red_signals_above_threshold():
    signals = [
        {"severity": "red", "confidence_weight": 0.95, "amount": -50000, "signal_type": "digital_asset"},
        {"severity": "red", "confidence_weight": 0.85, "amount": -15000, "signal_type": "intercompany"},
    ]
    score = calculate_las(signals, total_transaction_volume=200000)
    assert score > 20


def test_severity_band_clear():
    assert severity_band(15) == "clear"


def test_severity_band_elevated():
    assert severity_band(35) == "elevated"


def test_severity_band_refer():
    assert severity_band(60) == "refer"
