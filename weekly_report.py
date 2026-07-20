"""
weekly_report.py
=================
Pulls a full date-range summary directly from the Neon Postgres DB for
PAPER mode option trades: totals, win rate, P&L, daily breakdown, and
risk/drawdown context. Matches the reporting style of check_options.py /
check_pnl.py / check_risk.py, but works over a date range instead of a
fixed row limit.

Usage:
    python weekly_report.py                  # defaults to last 7 days
    python weekly_report.py --days 14         # last 14 days
    python weekly_report.py --start 2026-07-13 --end 2026-07-19

Reads DATABASE_URL from .env (same as database.py / reset_paper_account.py).
"""

import os
import sys
import argparse
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
TRADING_MODE_FILTER = "PAPER"  # weekly report is always against live PAPER data

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env")
    sys.exit(1)


def parse_args():
    p = argparse.ArgumentParser(description="Weekly trading report from Neon DB")
    p.add_argument("--days", type=int, default=7, help="Number of trailing days (default 7)")
    p.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (overrides --days)")
    p.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (defaults to today)")
    return p.parse_args()


def get_date_range(args):
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        if args.end:
            # include the full end day, not just 00:00:00 of that day
            end = datetime.strptime(args.end, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
        else:
            end = datetime.now()
    else:
        end = datetime.now()
        start = end - timedelta(days=args.days)
    return start, end


def fetch_trades(conn, start, end):
    # entry_time/exit_time are stored as text (ISO 8601), so lexicographic
    # string comparison works correctly for date-range filtering.
    query = """
        SELECT id, option_symbol, option_type, strike, expiry,
               premium AS entry_premium, exit_premium,
               quantity, entry_time, exit_time, status, exit_reason,
               pnl AS pnl_gross, entry_charges, exit_charges, total_charges
        FROM option_positions
        WHERE mode = %s
          AND entry_time >= %s
          AND entry_time <= %s
        ORDER BY entry_time ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute(query, (TRADING_MODE_FILTER, start.isoformat(), end.isoformat()))
        return c.fetchall()


def summarize(trades):
    closed = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
    open_ = [t for t in trades if str(t.get("status", "")).upper() == "OPEN"]

    def net(t):
        # Matches check_options.py: Net P&L = gross pnl - total charges
        gross = float(t.get("pnl_gross") or 0)
        total_charges = t.get("total_charges")
        if total_charges is None:
            total_charges = float(t.get("entry_charges") or 0) + float(t.get("exit_charges") or 0)
        else:
            total_charges = float(total_charges)
        return gross - total_charges

    total_net = sum(net(t) for t in closed)
    wins = [t for t in closed if net(t) > 0]
    losses = [t for t in closed if net(t) <= 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0

    gross_profit = sum(net(t) for t in wins)
    gross_loss = abs(sum(net(t) for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    best = max(closed, key=net, default=None)
    worst = min(closed, key=net, default=None)

    # Daily breakdown
    daily = {}
    for t in closed:
        entry_time = t.get("entry_time")
        if isinstance(entry_time, str):
            day = entry_time[:10]
        else:
            day = entry_time.strftime("%Y-%m-%d") if entry_time else "unknown"
        daily.setdefault(day, {"count": 0, "net": 0.0})
        daily[day]["count"] += 1
        daily[day]["net"] += net(t)

    return {
        "total_trades": len(trades),
        "closed_trades": len(closed),
        "open_trades": len(open_),
        "total_net_pnl": total_net,
        "win_rate": win_rate,
        "wins": len(wins),
        "losses": len(losses),
        "profit_factor": profit_factor,
        "best_trade": best,
        "worst_trade": worst,
        "daily": daily,
        "net_fn": net,
    }


def print_report(start, end, summary):
    print(f"\n=== Weekly Trading Report | {start.date()} to {end.date()} | Mode: {TRADING_MODE_FILTER} ===\n")

    print("-- Overview --")
    print(f"  Total trades (open+closed): {summary['total_trades']}")
    print(f"  Closed trades:              {summary['closed_trades']}")
    print(f"  Open trades:                {summary['open_trades']}")
    print(f"  Win rate:                   {summary['win_rate']:.1f}%  ({summary['wins']}W / {summary['losses']}L)")
    print(f"  Total net P&L:              Rs.{summary['total_net_pnl']:,.2f}")
    pf = summary["profit_factor"]
    print(f"  Profit factor:              {pf:.2f}" if pf != float("inf") else "  Profit factor:              inf (no losses)")

    net_fn = summary["net_fn"]
    if summary["best_trade"]:
        b = summary["best_trade"]
        print(f"\n  Best trade:  {b.get('option_symbol', '?')}  Net P&L: Rs.{net_fn(b):,.2f}")
    if summary["worst_trade"]:
        w = summary["worst_trade"]
        print(f"  Worst trade: {w.get('option_symbol', '?')}  Net P&L: Rs.{net_fn(w):,.2f}")

    print("\n-- Daily Breakdown --")
    for day in sorted(summary["daily"].keys()):
        d = summary["daily"][day]
        sign = "+" if d["net"] >= 0 else ""
        print(f"  {day}:  {d['count']} trade(s)   Net: {sign}Rs.{d['net']:,.2f}")

    print("\nRESULT: Report generated successfully.\n")


def main():
    args = parse_args()
    start, end = get_date_range(args)

    print(f"Connecting to Neon Postgres...")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=8)
    try:
        trades = fetch_trades(conn, start, end)
        if not trades:
            print(f"\nNo trades found for {TRADING_MODE_FILTER} mode between "
                  f"{start.date()} and {end.date()}.")
            return
        summary = summarize(trades)
        print_report(start, end, summary)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
    