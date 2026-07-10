#!/usr/bin/env python3
"""
Reset Paper Trading Account - fresh start back to INITIAL_CAPITAL
====================================================================

WARNING: THIS IS DESTRUCTIVE. It permanently deletes:
  - all rows in positions
  - all rows in option_positions
  - all rows in risk_metrics
  - all rows in circuit_breakers
And resets the account row (mode='PAPER') back to:
  - current_capital = 500000
  - initial_capital  = 500000
  - peak_capital     = 500000
  - total_pnl / daily_pnl = 0
  - total_trades / winning_trades / losing_trades = 0
  - trading_enabled = 1, kill_switch_reason = NULL

There is NO UNDO once this runs. Make sure you actually want to wipe
your trade history (including today's two live trades) before running.

Run locally:
    python reset_paper_account.py

It will ask for a typed confirmation before touching anything.
"""

import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
NEW_CAPITAL = 500000.0

def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not found in environment (.env).")
        sys.exit(1)

    print("=" * 60)
    print("PAPER TRADING ACCOUNT RESET")
    print("=" * 60)
    print(f"This will WIPE all positions/option_positions/risk_metrics/")
    print(f"circuit_breakers rows, and reset current_capital, peak_capital,")
    print(f"and initial_capital to Rs.{NEW_CAPITAL:,.2f}.")
    print()
    confirm = input("Type RESET to confirm (anything else cancels): ")
    if confirm.strip() != "RESET":
        print("Cancelled. Nothing was changed.")
        sys.exit(0)

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()
    try:
        cur.execute("TRUNCATE TABLE positions")
        cur.execute("TRUNCATE TABLE option_positions")
        cur.execute("TRUNCATE TABLE risk_metrics")
        cur.execute("TRUNCATE TABLE circuit_breakers")

        now = datetime.now().isoformat()
        cur.execute("""
            UPDATE account
            SET current_capital = %s,
                initial_capital = %s,
                peak_capital = %s,
                total_pnl = 0,
                daily_pnl = 0,
                open_positions = 0,
                total_trades = 0,
                winning_trades = 0,
                losing_trades = 0,
                trading_enabled = 1,
                kill_switch_reason = NULL,
                updated_at = %s
            WHERE mode = 'PAPER'
        """, (NEW_CAPITAL, NEW_CAPITAL, NEW_CAPITAL, now))

        conn.commit()
        print()
        print("Done. Account reset to Rs.{:,.2f}. All trade history cleared.".format(NEW_CAPITAL))
    except Exception as e:
        conn.rollback()
        print(f"ERROR - rolled back, nothing was changed: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
    