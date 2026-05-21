"""
External API call wrapper with exponential backoff and retry on rate limit errors.
Applies to any urllib-based outbound call (Anthropic API, web scan sources, etc.).
"""
import time
import urllib.request
import urllib.error
import json
import threading

_cost_lock = threading.Lock()
_COST_LOG_PATH = None  # Set by main.py if cost logging is desired

# Sonnet 4 pricing per million tokens (USD)
_COST_PER_M_INPUT  = 3.00
_COST_PER_M_OUTPUT = 15.00


def call_with_backoff(
    req: urllib.request.Request,
    timeout: int = 60,
    max_retries: int = 3,
    operation: str = "unknown",
) -> dict:
    """
    Execute a urllib Request with exponential backoff on 429 / 529 responses.
    Returns the parsed JSON response body.
    Logs token usage + cost estimate to cost log if COST_LOG_PATH is configured.
    """
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            result = json.loads(body)
            _log_cost(result, operation)
            return result
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < max_retries:
                print(f"  [RateLimiter] {e.code} on attempt {attempt + 1} — retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
    raise RuntimeError("Max retries exceeded")  # unreachable but satisfies linter


def _log_cost(result: dict, operation: str) -> None:
    """Write token usage and cost estimate to the cost log."""
    if _COST_LOG_PATH is None:
        return
    usage = result.get("usage", {})
    input_tok  = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    cost_usd   = (input_tok / 1_000_000 * _COST_PER_M_INPUT) + (output_tok / 1_000_000 * _COST_PER_M_OUTPUT)
    model      = result.get("model", "unknown")
    entry = {
        "ts":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operation":   operation,
        "model":       model,
        "input_tok":   input_tok,
        "output_tok":  output_tok,
        "cost_usd":    round(cost_usd, 6),
    }
    try:
        with _cost_lock:
            with open(_COST_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def configure_cost_log(path: str) -> None:
    global _COST_LOG_PATH
    _COST_LOG_PATH = path
