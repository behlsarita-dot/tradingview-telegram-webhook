#!/usr/bin/env python3
"""Emergency Position Closer - Paper Trading System v7.0

FIX (2026-08-01): the previous version only ever looked at
GET /api/positions?status=OPEN (the equity table) and closed positions
one at a time via action="EXIT" webhooks, asking the operator to type in
an exit price for each. Since this bot trades almost exclusively via
BUY_OPTION/EXIT_OPTION (see webhook_tester.py), real open positions
live in `option_positions`, which this script never looked at. In an
actual emergency, it would print "No open positions found" and exit
even while option positions sat open and unclosed - a false-safe result
from what's supposed to be the panic button.

The server already has a correct implementation of this at
POST /api/emergency-close (see app.py: api_emergency_close() ->
close_all_positions_eod()), which closes BOTH equity and option
positions in one call, using its own live/estimated price fetch for
each. This script now just displays what's open (both tables, for
visibility) and calls that endpoint instead of reimplementing
position-closing per-symbol over the equity-only route.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()
BASE_URL = os.getenv("APP_URL", "http://localhost:5000")
SECRET   = os.getenv("WEBHOOK_SECRET", "")

if not SECRET:
    print("ERROR: WEBHOOK_SECRET not set in .env")
    exit(1)


def get_open_equity():
    r = requests.get(f"{BASE_URL}/api/positions?status=OPEN", timeout=10)
    r.raise_for_status()
    return r.json().get("positions", [])


def get_open_options():
    r = requests.get(f"{BASE_URL}/api/options/positions", timeout=10)
    r.raise_for_status()
    return r.json().get("positions", [])


def emergency_close_all(reason: str = "Emergency Close - Manual (CLI)"):
    payload = {"webhook_secret": SECRET, "reason": reason}
    r = requests.post(f"{BASE_URL}/api/emergency-close", json=payload, timeout=30)
    return r.json()


def main():
    print(f"\nEmergency Closer | Server: {BASE_URL}")

    try:
        equity = get_open_equity()
        options = get_open_options()
    except requests.exceptions.RequestException as e:
        print(f"ERROR: could not reach server to list open positions: {e}")
        return

    total = len(equity) + len(options)
    if total == 0:
        print("No open positions found (equity or options).")
        return

    print(f"\nFound {total} open position(s):\n")
    for p in equity:
        print(f"  [EQUITY] {p['action']} {p['symbol']} @ Rs.{p['entry_price']:,.2f} x {p['quantity']}")
    for p in options:
        print(f"  [OPTION] {p.get('option_type', '?')} {p.get('option_symbol')} "
              f"@ Rs.{p.get('premium'):,.2f} x {p.get('quantity')}")

    confirm = input(
        f"\nThis will close ALL {total} position(s) above using the server's "
        f"own live/estimated pricing. Type CLOSE to confirm: "
    ).strip()
    if confirm != "CLOSE":
        print("Cancelled. Nothing was closed.")
        return

    try:
        res = emergency_close_all()
    except requests.exceptions.RequestException as e:
        print(f"ERROR: emergency-close request failed: {e}")
        return

    if res.get("success"):
        print(f"\nDone: {res.get('message', 'positions closed')}")
    else:
        print(f"\nFAILED: {res.get('message', 'unknown error')}")


if __name__ == "__main__":
    main()
    