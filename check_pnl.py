#!/usr/bin/env python3
"""
Check PNL - pulls the account/portfolio summary from Render via
GET /api/portfolio (falls back to /api/account, same route).

Cross-checks against:
  - check_risk.py's "Daily P&L" (from risk_manager's db.get_daily_pnl())
  - check_options.py's per-trade net P&L totals

If these numbers don't agree, that's worth chasing down - it usually
means one path is counting gross vs net, or realized-today vs a
different window, or equity trades vs option trades separately.

Run from anywhere:

    python check_pnl.py
"""

import sys

import requests

BASE_URL = "https://tradingview-telegram-webhook-dpaj.onrender.com"
REQUEST_TIMEOUT = 15


def fetch(path):
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Request failed for {url}: {e}")
        sys.exit(1)


def main():
    print("=== Portfolio / P&L Summary - Live Render Instance ===\n")
    data = fetch("/api/portfolio")

    if not data.get("success", True):
        print(f"Error: {data.get('message', 'unknown error')}")
        return

    print("-- Account --")
    for key in ("current_capital", "initial_capital", "realized_pnl", "unrealized_pnl",
                "total_pnl", "daily_pnl", "total_charges"):
        if key in data:
            val = data[key]
            if isinstance(val, (int, float)):
                print(f"  {key:<20} Rs.{val:,.2f}")
            else:
                print(f"  {key:<20} {val}")

    # Print anything else returned that we didn't explicitly label above,
    # in case the route includes fields not anticipated here.
    known = {"success", "current_capital", "initial_capital", "realized_pnl",
              "unrealized_pnl", "total_pnl", "daily_pnl", "total_charges"}
    extras = {k: v for k, v in data.items() if k not in known}
    if extras:
        print("\n-- Other fields returned --")
        for k, v in extras.items():
            print(f"  {k:<20} {v}")

    print("\nCompare this against:")
    print("  - check_risk.py 'Daily P&L' (from risk_manager.get_daily_pnl())")
    print("  - check_options.py per-trade Net P&L sum")
    print("If they disagree, one path is likely counting a different window or gross vs net.")


if __name__ == "__main__":
    main()
    