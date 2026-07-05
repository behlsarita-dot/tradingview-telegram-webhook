#!/usr/bin/env python3
"""
EOD Report - Paper Trading System v7.0
Run this at end of day (or any time) to pull current portfolio summary
and full trade history from the live bot, and save it locally.

This exists because the bot's SQLite database is on ephemeral storage
(BOT-01) - it gets wiped on every Render restart/redeploy. Running this
before you stop trading for the day means you keep a permanent record
even if the DB resets before you check it next.

Now also sends the JSON+CSV snapshot to Telegram automatically, so the
record survives even if this machine or the eod_reports/ folder is lost.

Usage:
    python eod_report.py

Output:
    Creates a folder "eod_reports/" (if missing) and saves:
      - eod_report_YYYY-MM-DD_HHMM.json   (full raw data, for archiving)
      - eod_report_YYYY-MM-DD_HHMM.csv    (trade log, for Excel/QA log)
    Also prints a summary to the console and sends both files to Telegram.
"""

import requests
import json
import csv
import os
from datetime import datetime

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    except ImportError:
        pass


def send_to_telegram(filepath: str, caption: str = "") -> bool:
    """Send a file to Telegram directly via HTTP API - independent of the Flask app."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  [WARN] Telegram not configured, skipping upload of {filepath}")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(filepath, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": f},
                timeout=30
            )
        if resp.status_code == 200:
            print(f"  [OK] Sent {filepath} to Telegram")
            return True
        else:
            print(f"  [WARN] Telegram upload failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"  [WARN] Telegram upload error: {e}")
        return False


BASE_URL = "https://tradingview-telegram-webhook-dpaj.onrender.com"
OUTPUT_DIR = "eod_reports"


def fetch(path: str) -> dict:
    """GET a JSON endpoint, return {} on failure instead of raising."""
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [WARN] Failed to fetch {path}: {e}")
        return {}


def main():
    print("=" * 60)
    print("EOD REPORT - Paper Trading System v7.0")
    print("=" * 60)

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H%M")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\nFetching data from live bot...")
    portfolio        = fetch("/api/portfolio")
    equity_closed     = fetch("/api/positions?status=CLOSED&limit=500")
    equity_open        = fetch("/api/positions?status=OPEN&limit=500")
    options_closed     = fetch("/api/options/history?status=CLOSED&limit=500")
    options_open        = fetch("/api/options/positions")
    health              = fetch("/health")

    # ---------------------------------------------------------------- #
    # Console summary
    # ---------------------------------------------------------------- #
    print("\n" + "-" * 60)
    print("PORTFOLIO SUMMARY")
    print("-" * 60)
    if portfolio.get("success"):
        print(f"  Mode:               {portfolio.get('mode')}")
        print(f"  Initial Capital:    Rs.{portfolio.get('initial_capital', 0):,.2f}")
        print(f"  Current Capital:    Rs.{portfolio.get('current_capital', 0):,.2f}")
        print(f"  Total P&L:          Rs.{portfolio.get('total_pnl', 0):,.2f}")
        print(f"  ROI %:              {portfolio.get('roi_pct', 0)}%")
        print(f"  Daily P&L:          Rs.{portfolio.get('daily_pnl', 0):,.2f}")
        print(f"  Total Trades:       {portfolio.get('total_trades', 0)}")
        print(f"  Win Rate:           {portfolio.get('win_rate', 0)}%")
        print(f"  Profit Factor:      {portfolio.get('profit_factor', 0)}")
        print(f"  Best Trade:         Rs.{portfolio.get('best_trade', 0):,.2f}")
        print(f"  Worst Trade:        Rs.{portfolio.get('worst_trade', 0):,.2f}")
        print(f"  Total Charges:      Rs.{portfolio.get('total_charges', 0):,.2f}")
        print(f"  Open Positions:     {portfolio.get('open_positions', 0)}")
    else:
        print("  [FAILED to fetch portfolio summary]")

    eq_closed_list  = equity_closed.get("positions", [])
    eq_open_list    = equity_open.get("positions", [])
    opt_closed_list = options_closed.get("positions", [])
    opt_open_list   = options_open.get("positions", [])

    print("\n" + "-" * 60)
    print(f"CLOSED TRADES: {len(eq_closed_list)} equity, {len(opt_closed_list)} options")
    print("-" * 60)
    for p in opt_closed_list:
        print(f"  [OPTION] {p.get('option_symbol')}: "
              f"{p.get('premium')} -> {p.get('exit_premium')} "
              f"| P&L: Rs.{p.get('pnl', 0):,.2f} | {p.get('exit_reason')}")
    for p in eq_closed_list:
        print(f"  [EQUITY] {p.get('symbol')} {p.get('action')}: "
              f"{p.get('entry_price')} -> {p.get('exit_price')} "
              f"| P&L: Rs.{p.get('pnl', 0):,.2f} | {p.get('exit_reason')}")

    print("\n" + "-" * 60)
    print(f"OPEN POSITIONS: {len(eq_open_list)} equity, {len(opt_open_list)} options")
    print("-" * 60)
    for p in opt_open_list:
        print(f"  [OPTION] {p.get('option_symbol')} @ {p.get('premium')} "
              f"| SL: {p.get('stop_loss')} TP: {p.get('take_profit')}")
    for p in eq_open_list:
        print(f"  [EQUITY] {p.get('symbol')} {p.get('action')} @ {p.get('entry_price')} "
              f"| SL: {p.get('stop_loss')} TP: {p.get('take_profit')}")

    if health.get("status") != "healthy":
        print("\n  [WARN] Bot health check did not return 'healthy' - check manually.")

    # ---------------------------------------------------------------- #
    # Save raw JSON (full archive)
    # ---------------------------------------------------------------- #
    json_path = os.path.join(OUTPUT_DIR, f"eod_report_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at":    now.isoformat(),
            "portfolio":       portfolio,
            "equity_closed":   eq_closed_list,
            "equity_open":     eq_open_list,
            "options_closed":  opt_closed_list,
            "options_open":    opt_open_list,
            "health":          health,
        }, f, indent=2)

    # ---------------------------------------------------------------- #
    # Save CSV trade log (Excel-friendly, for your QA log workbook)
    # ---------------------------------------------------------------- #
    csv_path = os.path.join(OUTPUT_DIR, f"eod_report_{timestamp}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "type", "symbol", "action_or_type", "entry", "exit",
            "quantity", "pnl", "charges", "exit_reason",
            "entry_time", "exit_time", "status"
        ])
        for p in opt_closed_list:
            writer.writerow([
                "OPTION", p.get("option_symbol"), p.get("option_type"),
                p.get("premium"), p.get("exit_premium"), p.get("quantity"),
                p.get("pnl"), p.get("total_charges"), p.get("exit_reason"),
                p.get("entry_time"), p.get("exit_time"), "CLOSED"
            ])
        for p in opt_open_list:
            writer.writerow([
                "OPTION", p.get("option_symbol"), p.get("option_type"),
                p.get("premium"), "", p.get("quantity"),
                "", p.get("entry_charges"), "",
                p.get("entry_time"), "", "OPEN"
            ])
        for p in eq_closed_list:
            writer.writerow([
                "EQUITY", p.get("symbol"), p.get("action"),
                p.get("entry_price"), p.get("exit_price"), p.get("quantity"),
                p.get("pnl"), p.get("total_charges"), p.get("exit_reason"),
                p.get("entry_time"), p.get("exit_time"), "CLOSED"
            ])
        for p in eq_open_list:
            writer.writerow([
                "EQUITY", p.get("symbol"), p.get("action"),
                p.get("entry_price"), "", p.get("quantity"),
                "", p.get("entry_charges"), "",
                p.get("entry_time"), "", "OPEN"
            ])

    print("\n" + "=" * 60)
    print(f"Saved: {json_path}")
    print(f"Saved: {csv_path}")
    print("=" * 60)

    print("\nSending snapshot to Telegram...")
    total_trades = len(eq_closed_list) + len(opt_closed_list)
    send_to_telegram(json_path, caption=f"EOD Snapshot {timestamp} (JSON, {total_trades} closed trades)")
    send_to_telegram(csv_path, caption=f"EOD Snapshot {timestamp} (CSV, {total_trades} closed trades)")


if __name__ == "__main__":
    main()
    