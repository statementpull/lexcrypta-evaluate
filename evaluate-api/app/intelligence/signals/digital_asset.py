"""Signal 04: Digital Asset Exposure — crypto exchange and wallet transactions."""

CRYPTO_KEYWORDS = [
    "BITCOIN", "BTC", "ETHEREUM", "ETH", "CRYPTO", "COINBASE", "BINANCE",
    "KRAKEN", "GEMINI", "BLOCKCHAIN", "METAMASK", "LEDGER", "TREZOR",
    "USDT", "USDC", "STABLECOIN", "NFT", "DEFI", "WALLET",
]


def run(transactions: list[dict], loader=None) -> list[dict]:
    results = []
    for t in transactions:
        merchant = t["merchant"].upper()

        # Library match takes priority
        if loader:
            match = loader.match_crypto_exchange(merchant)
            if match:
                results.append({
                    "signal_type": "digital_asset",
                    "severity": "red",
                    "merchant": merchant,
                    "amount": t["amount"],
                    "transaction_date": t.get("transaction_date", ""),
                    "description": (
                        f"Crypto exchange transaction: {match.get('exchange_name', merchant)}. "
                        f"Jurisdiction: {match.get('jurisdiction', 'Unknown')}. "
                        "Undisclosed digital asset holding — obtain exchange records."
                    ),
                    "library_match": "crypto_exchange_library",
                    "confidence_weight": match.get("confidence_weight", 0.80),
                })
                continue

        # Keyword fallback
        if any(kw in merchant for kw in CRYPTO_KEYWORDS):
            results.append({
                "signal_type": "digital_asset",
                "severity": "amber",
                "merchant": merchant,
                "amount": t["amount"],
                "transaction_date": t.get("transaction_date", ""),
                "description": (
                    f"Possible digital asset transaction: '{merchant}'. "
                    "Keyword match — verify manually."
                ),
                "library_match": None,
                "confidence_weight": 0.55,
            })

    return results
