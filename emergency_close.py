#!/usr/bin/env python3
"""Emergency Position Closer - Paper Trading System v7.0"""

import os, requests, json
from dotenv import load_dotenv

load_dotenv()
BASE_URL = os.getenv("APP_URL", "http://localhost:5000")
SECRET   = os.getenv("WEBHOOK_SECRET", "")

if not SECRET:
    print("ERROR: WEBHOOK_SECRET not set in .env")
    exit(1)

def get_open():
    r = requests.get(f"{BASE_URL}/api/positions?status=OPEN", timeout=10)
    return r.json().get("positions", [])

def close(symbol, price):
    payload = {"webhook_secret": SECRET, "symbol": symbol,
               "action": "EXIT", "price": price}
    r = requests.post(f"{BASE_URL}/api/webhook", json=payload, timeout=10)
    return r.json()

def main():
    print(f"\nEmergency Closer | Server: {BASE_URL}")
    positions = get_open()

    if not positions:
        print("No open positions found.")
        return

    print(f"\nFound {len(positions)} open position(s):\n")
    for i, p in enumerate(positions, 1):
        print(f"  {i}. {p['action']} {p['symbol']} @ Rs.{p['entry_price']:,.2f} x {p['quantity']}")

    print("\nEnter exit prices to close each position.")
    print("Press Enter to skip a position.\n")

    for p in positions:
        price_input = input(f"Exit price for {p['action']} {p['symbol']} (current entry Rs.{p['entry_price']:,.2f}): ").strip()
        if not price_input:
            print("  Skipped")
            continue
        try:
            price = float(price_input)
            res = close(p["symbol"], price)
            if res.get("success"):
                print(f"  Closed! P&L: Rs.{res.get('net_pnl', 0):+,.2f}")
            else:
                print(f"  Failed: {res.get('message', 'Unknown error')}")
        except ValueError:
            print("  Invalid price, skipped")

if __name__ == "__main__":
    main()
