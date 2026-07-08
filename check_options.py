#!/usr/bin/env python3
"""
Check Options - pulls real option trade history from the LIVE bot
(Render), not a local test database.

Run from anywhere (no need to be inside the repo, since this just calls
the deployed API over HTTP):

    python check_options.py                # last 10 trades, open + closed
    python check_options.py --status OPEN  # only open positions
    python check_options.py --status CLOSED --limit 20

Uses GET /api/options/history and /api/options/positions - both are
read-only, no webhook secret required.
"""

import argparse
import sys

import requests

BASE_URL = "https://tradingview-telegram-webhook-dpaj.onrender.com"
REQUEST_TIMEOUT = 15


def fetch(path, params=None):
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Request failed for {url}: {e}")
        sys.exit(1)


def fmt_row(p):
    symbol = p.get("option_symbol", p.get("symbol", "?"))
    status = p.get("status", "?")
    premium = p.get("premium")
    entry_charges = p.get("entry_charges")
    exit_charges = p.get("exit_charges")
    pnl = p.get("pnl")
    net_pnl = None
    if pnl is not None:
        tc = p.get("total_charges")
        net_pnl = pnl - tc if tc is not None else pnl

    lines = [f"{symbol}  [{status}]"]
    lines.append(f"  Type/Strike/Expiry: {p.get('option_type', '?')} {p.get('strike', '?')} {p.get('expiry', '?')}")
    lines.append(f"  Entry premium: {premium}   Qty: {p.get('quantity')}")
    lines.append(f"  SL: {p.get('stop_loss')}   TP: {p.get('take_profit')}")
    lines.append(f"  Entry time: {p.get('entry_time', '?')}")
    if status == "CLOSED":
        lines.append(f"  Exit premium: {p.get('exit_price', p.get('current_price', '?'))}   Exit time: {p.get('exit_time', '?')}")
        lines.append(f"  Exit reason: {p.get('exit_reason', '?')}")
        lines.append(f"  P&L (gross): {pnl}   Charges: entry {entry_charges} + exit {exit_charges}   Net P&L: {net_pnl}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", choices=["OPEN", "CLOSED"], default=None)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    print(f"=== Option Positions - Live Render Instance ===\n")

    if args.status == "OPEN":
        data = fetch("/api/options/positions")
        positions = data.get("positions", [])
        print(f"Open option positions: {len(positions)}\n")
    else:
        params = {"limit": args.limit}
        if args.status:
            params["status"] = args.status
        data = fetch("/api/options/history", params=params)
        positions = data.get("positions", [])
        label = args.status or "all (open + closed)"
        print(f"Option trades ({label}), showing up to {args.limit}: {len(positions)} found\n")

    if not positions:
        print("No option positions found for this filter.")
        return

    for p in positions:
        print(fmt_row(p))
        print()


if __name__ == "__main__":
    main()
    