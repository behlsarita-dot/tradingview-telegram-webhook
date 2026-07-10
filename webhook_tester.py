#!/usr/bin/env python3
"""Webhook Tester v2.0 - Paper Trading System v7.0"""

import os, json, time, requests
from datetime import datetime

BASE_URL = os.getenv("APP_URL", "http://localhost:5000")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not WEBHOOK_SECRET:
    from dotenv import load_dotenv
    load_dotenv()
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

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
    print("\n--- TEST: BUY SIGNAL ---")
    res = post({"webhook_secret": WEBHOOK_SECRET, "symbol": "NIFTY",
                "action": "BUY", "price": 24500, "quantity": 65,
                "sl": 24300, "tp": 24900})
    print(json.dumps(res, indent=2))
    return res.get("success", False)

def test_exit():
    print("\n--- TEST: EXIT SIGNAL ---")
    res = post({"webhook_secret": WEBHOOK_SECRET, "symbol": "NIFTY",
                "action": "EXIT", "price": 24650})
    print(json.dumps(res, indent=2))
    return res.get("success", False)

def test_invalid_secret():
    print("\n--- TEST: INVALID SECRET (should fail) ---")
    res = post({"webhook_secret": "wrong_secret", "symbol": "NIFTY",
                "action": "BUY", "price": 24500, "quantity": 65})
    ok = not res.get("success", True)
    print(f"Correctly rejected: {ok}")
    return ok

def test_invalid_action():
    print("\n--- TEST: INVALID ACTION (should fail) ---")
    res = post({"webhook_secret": WEBHOOK_SECRET, "symbol": "NIFTY",
                "action": "HOLD", "price": 24500, "quantity": 65})
    ok = not res.get("success", True)
    print(f"Correctly rejected: {ok}")
    return ok

def run_all():
    print(f"\nServer: {BASE_URL}")
    h = health()
    print(f"Health: {h.get('status', 'unknown')} | Mode: {h.get('mode', '?')}")

    results = {
        "Buy Signal":      test_buy(),
        "Exit Signal":     test_exit(),
        "Invalid Secret":  test_invalid_secret(),
        "Invalid Action":  test_invalid_action(),
    }

    print("\n--- RESULTS ---")
    passed = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        print(f"  {'OK' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(results)} tests passed")

if __name__ == "__main__":
    run_all()
