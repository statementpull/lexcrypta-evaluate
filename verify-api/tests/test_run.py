from app.intelligence.signals import run_signals, build_verify_result

SAMPLE_TRANSACTIONS = [
    {"merchant": "COINBASE", "amount": -500.00, "transaction_date": "2024-01-15",
     "description": "COINBASE INC", "type": "debit"},
    {"merchant": "WESTPAC MORTGAGE", "amount": -2100.00, "transaction_date": "2024-01-20",
     "description": "WESTPAC HOME LOAN", "type": "debit"},
    {"merchant": "COLES SUPERMARKET", "amount": -85.00, "transaction_date": "2024-01-22",
     "description": "COLES 4032", "type": "debit"},
]


def test_run_signals_returns_list():
    signals = run_signals(SAMPLE_TRANSACTIONS)
    assert isinstance(signals, list)


def test_run_signals_detects_crypto():
    signals = run_signals(SAMPLE_TRANSACTIONS)
    types = [s.get("signal_type", s.get("type", "")) for s in signals]
    assert any("digital" in t or "crypto" in t.lower() or "asset" in t.lower() for t in types)


def test_build_verify_result_has_16_signals():
    raw_signals = run_signals(SAMPLE_TRANSACTIONS)
    result = build_verify_result(matter_id=1, raw_signals=raw_signals,
                                  transactions=SAMPLE_TRANSACTIONS, exposure="HIGH")
    assert len(result["signals"]) == 16


def test_build_verify_result_shape():
    raw_signals = run_signals(SAMPLE_TRANSACTIONS)
    result = build_verify_result(matter_id=1, raw_signals=raw_signals,
                                  transactions=SAMPLE_TRANSACTIONS, exposure="HIGH")
    assert "las" in result
    assert "signals" in result
    assert "intel" in result
    assert result["las"]["score"] >= 0
    sig = result["signals"][0]
    assert "name" in sig and "cat" in sig and "status" in sig


def test_verify_plus_teaser_on_high_exposure():
    raw_signals = run_signals(SAMPLE_TRANSACTIONS)
    result = build_verify_result(matter_id=1, raw_signals=raw_signals,
                                  transactions=SAMPLE_TRANSACTIONS, exposure="HIGH")
    assert "verify_plus_teaser" in result
    assert result["verify_plus_teaser"]["available"] is True


def test_no_verify_plus_teaser_on_low_exposure():
    result = build_verify_result(matter_id=1, raw_signals=[],
                                  transactions=[], exposure="LOW")
    assert result["verify_plus_teaser"]["available"] is False
