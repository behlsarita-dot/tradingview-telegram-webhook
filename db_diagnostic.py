#!/usr/bin/env python3
"""DB Diagnostic - Paper Trading System v7.0 (Postgres)"""

import os
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "")


def run(database_url):
    if not database_url:
        print("DATABASE_URL not set. Pass it as an argument or set the env var.")
        return

    conn = psycopg2.connect(database_url, connect_timeout=10)
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print(f"\nDatabase: Postgres")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    print("\n=== ACCOUNT ===")
    c.execute("SELECT * FROM account WHERE mode='PAPER'")
    acc = c.fetchone()
    if acc:
        print(f"Capital:  Rs.{acc['current_capital']:,.2f}")
        print(f"P&L:      Rs.{acc['total_pnl']:+,.2f}")
        roi = ((acc['current_capital'] - acc['initial_capital']) / acc['initial_capital'] * 100)
        print(f"ROI:      {roi:+.2f}%")
    else:
        print("No PAPER account found")

    print("\n=== OPEN POSITIONS ===")
    c.execute("SELECT * FROM positions WHERE mode='PAPER' AND status='OPEN'")
    rows = c.fetchall()
    if rows:
        for r in rows:
            print(f"  #{r['id'][:8]} {r['action']} {r['symbol']} @ Rs.{r['entry_price']:,.2f} x {r['quantity']}")
    else:
        print("None")

    print("\n=== CLOSED POSITIONS (last 5) ===")
    c.execute("""
        SELECT * FROM positions WHERE mode='PAPER' AND status='CLOSED'
        ORDER BY exit_time DESC LIMIT 5
    """)
    rows = c.fetchall()
    if rows:
        for r in rows:
            gross = r['pnl'] or 0
            charges = r['total_charges'] or 0
            net = gross - charges
            print(f"  {r['action']} {r['symbol']}: Rs.{net:+,.2f} net ({r['exit_reason']})")
    else:
        print("None")

    # Net-of-charges fix: previously summed raw pnl (gross). Now subtracts
    # total_charges per row so Total P&L / Win Rate match account.total_pnl
    # and current_capital movement instead of reporting pre-charges figures.
    print("\n=== STATS ===")
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN (pnl - total_charges) > 0 THEN 1 ELSE 0 END) as wins,
               COALESCE(SUM(pnl - total_charges), 0) as total_pnl
        FROM positions WHERE mode='PAPER' AND status='CLOSED'
    """)
    s = c.fetchone()
    total = s['total'] or 0
    wins = s['wins'] or 0
    print(f"Total Trades: {total}")
    print(f"Win Rate:     {wins/total*100:.1f}%" if total > 0 else "Win Rate: N/A")
    print(f"Total P&L:    Rs.{s['total_pnl']:+,.2f}")

    c.close()
    conn.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DATABASE_URL
    run(url)
