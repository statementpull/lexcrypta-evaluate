"""Web intelligence orchestrator.

Runs all public-data source checks in parallel for a given business.
Returns a unified list of findings in the same dict structure as signals,
ready to be stored as WebFinding rows and merged into reports.

Each source module is fully isolated — a failure in one never affects others.
All findings follow the same structure as signal dicts for seamless report integration.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from .web_sources import (
    ppp_check, epa_check, sec_check, dol_check,
    ofac_check, news_check, court_check, trademark_check,
)

log = logging.getLogger("evaluate.web_scanner")

SOURCES = [
    ("PPP Database",    lambda name, state, owners: ppp_check.analyse(name, state)),
    ("EPA ECHO",        lambda name, state, owners: epa_check.analyse(name, state)),
    ("SEC EDGAR",       lambda name, state, owners: sec_check.analyse(name, state)),
    ("DOL Enforcement", lambda name, state, owners: dol_check.analyse(name, state)),
    ("OFAC",            lambda name, state, owners: ofac_check.analyse(name, owners, state)),
    ("News",            lambda name, state, owners: news_check.analyse(name, state)),
    ("Court Records",   lambda name, state, owners: court_check.analyse(name, state)),
    ("USPTO Trademark", lambda name, state, owners: trademark_check.analyse(name, state)),
]


def run_web_scan(
    business_name: str,
    state: str = "",
    owner_names: list[str] | None = None,
    timeout_per_source: int = 20,
) -> list[dict]:
    """Run all web sources and return unified findings list."""
    owner_names = owner_names or []
    findings: list[dict] = []

    def _run(label, fn):
        try:
            return label, fn(business_name, state, owner_names)
        except Exception as e:
            log.warning("Web source %s failed: %s", label, e)
            return label, []

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {pool.submit(_run, label, fn): label for label, fn in SOURCES}
        for future in as_completed(futures, timeout=timeout_per_source + 5):
            try:
                label, results = future.result(timeout=timeout_per_source)
                findings.extend(results)
                log.info("Web source %s: %d finding(s)", label, len(results))
            except TimeoutError:
                log.warning("Web source %s timed out", futures[future])
            except Exception as e:
                log.warning("Web source %s error: %s", futures[future], e)

    return findings
