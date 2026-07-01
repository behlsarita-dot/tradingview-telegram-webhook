#!/usr/bin/env python3
"""DB Diagnostic - Paper Trading System v7.0"""

import sqlite3, os, sys
from datetime import datetime

DB_PATH = os.getenv("DB_FILE", "trading_bot.db")

def run():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print(f"\nDatabase: {DB_PATH}")
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
    c.execute("SELECT * FROM positions WHERE mode='PAPER' AND status='CLOSED' ORDER BY exit_time DESC LIMIT 5")
    rows = c.fetchall()
    if rows:
        for r in rows:
            pnl = r['pnl'] or 0
            print(f"  {r['action']} {r['symbol']}: Rs.{pnl:+,.2f} ({r['exit_reason']})")
    else:
        print("None")

    print("\n=== STATS ===")
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               COALESCE(SUM(pnl), 0) as total_pnl
        FROM positions WHERE mode='PAPER' AND status='CLOSED'
    """)
    s = c.fetchone()
    total = s['total'] or 0
    wins  = s['wins'] or 0
    print(f"Total Trades: {total}")
    print(f"Win Rate:     {wins/total*100:.1f}%" if total > 0 else "Win Rate: N/A")
    print(f"Total P&L:    Rs.{s['total_pnl']:+,.2f}")

    conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        DB_PATH = sys.argv[1]
    run()
