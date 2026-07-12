#!/usr/bin/env python3
"""Trade Monitor v1.0 - Paper Trading System v7.0"""

import requests, sys, json
from datetime import datetime

BASE_URL = "http://localhost:5000"

def get(endpoint):
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", timeout=10)
        return r.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

def portfolio():
    print("\n=== PORTFOLIO ===")
    res = get("api/portfolio")
    if not res.get("success"):
        print("Could not fetch portfolio")
        return
    print(f"Capital:    Rs.{res['current_capital']:,.2f}")
    print(f"Total P&L:  Rs.{res['total_pnl']:+,.2f}")
    print(f"ROI:        {res['roi_pct']:+.2f}%")
    print(f"Daily P&L:  Rs.{res['daily_pnl']:+,.2f}")
    print(f"Open Pos:   {res['open_positions']}")
    print(f"Trades:     {res['total_trades']}")
    print(f"Win Rate:   {res['win_rate']:.1f}%")

def positions():
    print("\n=== OPEN POSITIONS ===")
    res = get("api/positions?status=OPEN&limit=20")
    if not res.get("success") or not res.get("positions"):
        print("No open positions")
        return
    for p in res["positions"]:
        print(f"  {p['action']} {p['symbol']} @ Rs.{p['entry_price']:,.2f} x {p['quantity']}")
        if p.get("stop_loss"):
            print(f"    SL: Rs.{p['stop_loss']:,.2f} | TP: Rs.{p.get('take_profit',0):,.2f}")

def trades(limit=10):
    print(f"\n=== LAST {limit} TRADES ===")
    res = get(f"api/positions?status=CLOSED&limit={limit}")
    if not res.get("success") or not res.get("positions"):
        print("No closed trades")
        return
    for p in res["positions"]:
        pnl = p.get("pnl", 0) or 0
        sign = "+" if pnl >= 0 else ""
        print(f"  {p['action']} {p['symbol']}: {sign}Rs.{pnl:,.2f} ({p.get('exit_reason','?')})")

def risk():
    print("\n=== RISK STATUS ===")
    res = get("api/risk/report")
    if not res.get("success"):
        print("Could not fetch risk report")
        return
    status = "ACTIVE" if res["can_trade"] else "HALTED"
    print(f"Status:     {status}")
    print(f"Drawdown:   {res['current_drawdown']:.2f}%")
    print(f"Heat:       {res['portfolio_heat']:.2f}%")
    print(f"Con.Losses: {res['consecutive_losses']}")
    print(f"Daily P&L:  Rs.{res['daily_pnl']:+,.2f}")
    if not res["can_trade"]:
        print(f"Reason:     {res['circuit_breaker_reason']}")

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("portfolio", "p"):
        portfolio()
    elif cmd in ("positions", "pos"):
        positions()
    elif cmd in ("trades", "t"):
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        trades(n)
    elif cmd in ("risk", "r"):
        risk()
    else:
        portfolio()
        positions()
        trades()
        risk()

if __name__ == "__main__":
    main()
