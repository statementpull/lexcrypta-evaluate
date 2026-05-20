import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_loader():
    loader = MagicMock()
    loader.match_gambling.return_value = None
    loader.match_crypto_exchange.side_effect = lambda m: (
        {
            "exchange_name": "Coinbase",
            "jurisdiction": "USA",
            "recovery_difficulty_score": 0.4,
            "signal_strength": "Strong",
            "confidence_weight": 0.85,
            "_source_library": "crypto_exchange_library",
        }
        if "COINBASE" in m.upper()
        else None
    )
    loader.match_any.return_value = []
    return loader
