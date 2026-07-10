#!/usr/bin/env python3
"""Webhook Tester v2.1 - Paper Trading System v7.0

CHANGED (2026-07-10): BUY/EXIT tests previously used action="BUY"/"EXIT"
with a raw "price" field, which routes through the EQUITY path
(_handle_open / _handle_exit in app.py). That path locks the full
notional (entry_price * quantity) against current_capital via
PortfolioManager.get_summary()'s locked_equity calculation - correct
for real equity, but not what this bot actually trades. The live system
only ever places BUY_OPTION/EXIT_OPTION trades (premium-based), so
running the old equity-style test left a stray equity-path position
sitting in the `positions` table, inflating locked_in_positions to the
full strike notional (~Rs.15.9L for one 65-qty lot at strike 24500) and
driving available_capital deeply negative - not a real bug in the
capital math, just the test exercising a code path this bot doesn't
actually use. Tests now use BUY_OPTION/EXIT_OPTION with a synthetic
option_symbol/premium, matching what the real DTS5 Pine script sends.
"""
import os, json, time, requests
from datetime import datetime

BASE_URL = os.getenv("APP_URL", "http://localhost:5000")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
if not WEBHOOK_SECRET:
    from dotenv import load_dotenv
    load_dotenv()
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Synthetic test contract - format matches syminfo.ticker style used by
# the real DTS5 script: <underlying><YYMMDD><C|P><strike>
TEST_OPTION_SYMBOL = "NIFTY260714C24500"
TEST_STRIKE = 24500
TEST_EXPIRY = "2026-07-14"


def post(payload):
    try:
        r = requests.post(f"{BASE_URL}/api/webhook", json=payload,
                          headers={"Content-Type": "application/json"}, timeout=25)
        return r.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def health():
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        return r.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_buy():
    print("\n--- TEST: BUY_OPTION SIGNAL ---")
    res = post({
        "webhook_secret": WEBHOOK_SECRET,
        "action": "BUY_OPTION",
        "symbol": "NIFTY",
        "option_symbol": TEST_OPTION_SYMBOL,
        "option_type": "CE",
        "strike": TEST_STRIKE,
        "expiry": TEST_EXPIRY,
        "premium": 228.25,
        "quantity": 65,
        "sl": 200.00,
        "tp": 280.00,
    })
    print(json.dumps(res, indent=2))
    return res.get("success", False)


def test_exit():
    print("\n--- TEST: EXIT_OPTION SIGNAL ---")
    res = post({
        "webhook_secret": WEBHOOK_SECRET,
        "action": "EXIT_OPTION",
        "symbol": "NIFTY",
        "option_symbol": TEST_OPTION_SYMBOL,
        "premium": 247.78,
        "exit_reason": "Signal Exit",
    })
    print(json.dumps(res, indent=2))
    return res.get("success", False)


def test_invalid_secret():
    print("\n--- TEST: INVALID SECRET (should fail) ---")
    res = post({
        "webhook_secret": "wrong_secret",
        "action": "BUY_OPTION",
        "symbol": "NIFTY",
        "option_symbol": TEST_OPTION_SYMBOL,
        "option_type": "CE",
        "strike": TEST_STRIKE,
        "expiry": TEST_EXPIRY,
        "premium": 228.25,
        "quantity": 65,
    })
    ok = not res.get("success", True)
    print(f"Correctly rejected: {ok}")
    return ok


def test_invalid_action():
    print("\n--- TEST: INVALID ACTION (should fail) ---")
    res = post({
        "webhook_secret": WEBHOOK_SECRET,
        "action": "HOLD",
        "symbol": "NIFTY",
        "option_symbol": TEST_OPTION_SYMBOL,
        "premium": 228.25,
        "quantity": 65,
    })
    ok = not res.get("success", True)
    print(f"Correctly rejected: {ok}")
    return ok


def run_all():
    print(f"\nServer: {BASE_URL}")
    h = health()
    print(f"Health: {h.get('status', 'unknown')} | Mode: {h.get('mode', '?')}")
    results = {
        "Buy Option Signal":  test_buy(),
        "Exit Option Signal": test_exit(),
        "Invalid Secret":     test_invalid_secret(),
        "Invalid Action":     test_invalid_action(),
    }
    print("\n--- RESULTS ---")
    passed = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        print(f"  {'OK' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(results)} tests passed")


if __name__ == "__main__":
    run_all()
    