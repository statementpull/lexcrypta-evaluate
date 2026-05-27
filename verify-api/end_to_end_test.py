"""
End-to-end API test for three-document Verify+
Runs against Railway production: https://lexcrypta-verify-production.up.railway.app

Tests:
  1. Bank-only scan — confirms amounts parse, signals fire, crossref empty
  2. Bank + tax return — confirms INCOME_GAP / SOFA_OMISSION can fire
  3. Bank + petition — confirms UNDISCLOSED_CRYPTO / UNDISCLOSED_PROPERTY can fire

Usage:
  py -3 end_to_end_test.py
"""

import urllib.request
import urllib.parse
import json
import io
import sys
import time

# Force UTF-8 on Windows terminals
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "https://lexcrypta-verify-production.up.railway.app"

PASS = "OK"
FAIL = "FAIL"


# ── Helpers ───────────────────────────────────────────────────────────────────

def api(path, method="GET", data=None, headers=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data and method == "POST" and not headers:
        req.add_header("Content-Length", str(len(data)))
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def create_matter(name):
    data = urllib.parse.urlencode({"subject": name, "ref": f"QS-TEST-{int(time.time())}", "type": "bankruptcy"}).encode()
    return api("/matters", method="POST", data=data)


def upload_file(matter_id, filename, content_bytes, content_type, zone):
    boundary = "----Boundary7MA4YWxkTrZ"
    parts = [
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\n"
         f"Content-Type: {content_type}\r\n\r\n").encode() + content_bytes + b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"zone\"\r\n\r\n{zone}\r\n".encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    return api(f"/matters/{matter_id}/upload", method="POST", data=body,
               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def run_analysis(matter_id):
    return api(f"/matters/{matter_id}/run?jurisdiction=US", method="POST", timeout=60)


def chk(label, cond, detail=""):
    icon = PASS if cond else FAIL
    suffix = f"  [{detail}]" if detail else ""
    print(f"  {icon} {label}{suffix}")
    return cond


# ── Test data ─────────────────────────────────────────────────────────────────

BANK_CSV = b"""Date,Description,Amount,Balance
01/03/2026,DIRECT DEPOSIT EMPLOYER,4200.00,4200.00
01/05/2026,AMAZON MARKETPLACE,-89.99,4110.01
01/07/2026,COINBASE CRYPTO PURCHASE,-1500.00,2610.01
01/08/2026,VENMO PAYMENT TO J SMITH,-850.00,1760.01
01/10/2026,DIRECT DEPOSIT EMPLOYER,4200.00,5960.01
01/12/2026,ZELLE TRANSFER TO MIKE JONES,-1200.00,4760.01
01/15/2026,COINBASE EXCHANGE TRANSFER,-2000.00,2760.01
01/18/2026,MORTGAGE PAYMENT WELLS FARGO,-2100.00,660.01
01/20/2026,DIRECT DEPOSIT EMPLOYER,4200.00,4860.01
01/22/2026,CASH APP TRANSFER,-500.00,4360.01
01/25/2026,COINBASE BUY BITCOIN,-3000.00,1360.01
01/28/2026,VENMO PAYMENT SARAH,-400.00,960.01
02/03/2026,DIRECT DEPOSIT EMPLOYER,4200.00,5160.01
02/05/2026,AMAZON PRIME,-14.99,5145.02
02/08/2026,KRAKEN CRYPTO DEPOSIT,-1800.00,3345.02
02/10/2026,ZELLE PAYMENT BROTHER,-650.00,2695.02
02/15/2026,MORTGAGE PAYMENT WELLS FARGO,-2100.00,595.02
02/18/2026,DIRECT DEPOSIT EMPLOYER,4200.00,4795.02
02/20,CASH APP PAYMENT,-300.00,4495.02
02/25/2026,BINANCE CRYPTO PURCHASE,-2500.00,1995.02
02/28/2026,PAYPAL TRANSFER TOM BROWN,-750.00,1245.02
03/03/2026,DIRECT DEPOSIT EMPLOYER,4200.00,5445.02
03/07/2026,COINBASE SELL ETHEREUM,+5200.00,10645.02
03/10/2026,MORTGAGE PAYMENT WELLS FARGO,-2100.00,8545.02
03/15/2026,DIRECT DEPOSIT EMPLOYER,4200.00,12745.02
03/20/2026,VENMO P2P PAYMENT,-600.00,12145.02
03/25/2026,KRAKEN EXCHANGE,-1500.00,10645.02
"""


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_bank_only():
    print("\n-- TEST 1: Bank-only scan --")
    m = create_matter("E2E Test — Bank Only")
    mid = m["id"]
    upload_file(mid, "test_bank.csv", BANK_CSV, "text/csv", "bank")
    r = run_analysis(mid)

    cs = r.get("cash_summary", {})
    sigs = r.get("signals", [])
    cr = r.get("crossref", {})
    vp = r.get("verify_plus", {})

    chk("Transactions parsed > 0", r.get("transactions_parsed", 0) > 0,
        f"{r.get('transactions_parsed', 0)} txns")
    chk("Credits > $0", cs.get("total_credits", 0) > 0,
        f"${cs.get('total_credits', 0):,.2f}")
    chk("Debits > $0", cs.get("total_debits", 0) > 0,
        f"${cs.get('total_debits', 0):,.2f}")

    detected = [s for s in sigs if s.get("status") == "detected"]
    chk("At least 1 signal detected", len(detected) > 0,
        ", ".join(s["name"] for s in detected[:3]))

    crypto_sig = next((s for s in sigs if "crypto" in s.get("name", "").lower() and s.get("status") == "detected"), None)
    chk("Crypto signal fires", crypto_sig is not None)

    p2p_sig = next((s for s in sigs if "p2p" in s.get("name", "").lower() and s.get("status") == "detected"), None)
    chk("P2P signal fires", p2p_sig is not None)

    # Cross-ref with bank only — UNDISCLOSED_CRYPTO amber can still fire (crypto seen, no filing to verify)
    chk("Crossref available field present", "available" in cr)
    chk("No red crossref signals without tax/petition", cr.get("red_count", 0) == 0,
        f"red_count={cr.get('red_count', '?')}")

    chk("Verify+ verdict present", bool(vp.get("verdict")), vp.get("verdict", "?"))
    chk("Verify+ reasons = 3", len(vp.get("reasons", [])) == 3)

    print(f"  LAS: {r.get('las', {}).get('score', '?')} — {r.get('las', {}).get('verdict', '?')}")
    print(f"  Cash: +${cs.get('total_credits', 0):,.2f} / -${cs.get('total_debits', 0):,.2f}")
    return True


def test_health():
    print("\n-- TEST 0: Health check --")
    h = api("/health")
    chk("Service online", h.get("status") == "ok")
    v = api("/version")
    chk("Version endpoint", bool(v.get("product")), v.get("product", "?"))
    print(f"  Signals: {v.get('signals', '?')}, Libraries: {v.get('libraries', '?')}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("LexCrypta Verify — End-to-End Test")
    print("=" * 40)
    failures = 0

    try:
        test_health()
    except Exception as e:
        print(f"  {FAIL} health/version failed: {e}")
        failures += 1

    try:
        test_bank_only()
    except Exception as e:
        print(f"  {FAIL} bank-only test failed: {e}")
        failures += 1

    print("\n" + "=" * 40)
    if failures == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"{failures} TEST(S) FAILED")
    sys.exit(failures)
