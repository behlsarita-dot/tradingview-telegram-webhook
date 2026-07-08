#!/usr/bin/env python3
"""
Check Risk - pulls the LIVE risk-manager state from Render via
GET /api/risk/report.

This is the real, backend-enforced picture - separate from whatever the
Pine script's own on-chart "Trades Today X/Y" counter shows. The two are
independent: Pine's counter is chart-side and cosmetic/pre-emptive, this
is what actually decides whether the next BUY_OPTION gets accepted or
rejected with a 400.

Run from anywhere (no need to be inside the repo):

    python check_risk.py
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
    print("=== Risk Manager State - Live Render Instance ===\n")
    data = fetch("/api/risk/report")

    if not data.get("success", True) and "error" in data:
        print(f"Error: {data['error']}")
        return

    def line(label, value):
        print(f"  {label:<28} {value}")

    print("-- Circuit Breaker / Kill Switch --")
    line("Can trade:", data.get("can_trade"))
    line("Kill switch active:", data.get("kill_switch_active"))
    if data.get("circuit_breaker_active"):
        line("Circuit breaker reason:", data.get("circuit_breaker_reason"))

    print("\n-- Trade Counters (backend-enforced) --")
    line("Trades today:", data.get("trades_today"))
    line("Open positions:", f"{data.get('open_positions')} / {data.get('max_open_positions')}")
    line("Portfolio heat:", f"{data.get('portfolio_heat')}%")

    print("\n-- Capital / Drawdown --")
    line("Current capital:", f"Rs.{data.get('current_capital'):,.2f}" if data.get("current_capital") is not None else "?")
    line("Peak capital:", f"Rs.{data.get('peak_capital'):,.2f}" if data.get("peak_capital") is not None else "?")
    line("Current drawdown:", f"{data.get('current_drawdown')}% (max {data.get('max_drawdown')}%)")
    line("Daily P&L:", f"Rs.{data.get('daily_pnl'):,.2f}" if data.get("daily_pnl") is not None else "?")
    line("Consecutive losses:", data.get("consecutive_losses"))
    line("Recovery mode:", data.get("recovery_mode"))
    line("Position size multiplier:", data.get("position_size_multiplier"))
    line("Sizing method:", data.get("sizing_method"))

    print()
    if not data.get("can_trade", True):
        print(f"RESULT: Trading is BLOCKED - {data.get('circuit_breaker_reason', 'reason unknown')}")
    else:
        print("RESULT: No backend block active. New entries can be accepted (subject to Pine-side signal logic).")


if __name__ == "__main__":
    main()
    