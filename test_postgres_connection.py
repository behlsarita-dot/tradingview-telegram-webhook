#!/usr/bin/env python3
"""
Postgres Connection Smoke Test - run this BEFORE committing/pushing the
Postgres migration, to confirm DATABASE_URL actually works end-to-end:
connects, creates schema, opens/closes a test trade, verifies net-of-charges
math, then cleans up after itself. Safe to run against a fresh or existing
database - it never touches real PAPER account rows other than the
temporary TEST-mode ones it creates and deletes.

Usage:
  python test_postgres_connection.py                  (uses DATABASE_URL env var)
  python test_postgres_connection.py "postgresql://..." (pass URL directly)
"""

import os
import sys
import time

TEST_MODE = "SMOKETEST"  # isolated mode so this never touches real PAPER rows


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DATABASE_URL", "")

    print(f"=== Postgres Connection Smoke Test | {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    if not url:
        print("FAIL  DATABASE_URL not set and no URL passed as argument.")
        print("      Usage: python test_postgres_connection.py \"postgresql://user:pass@host/db\"")
        sys.exit(1)

    # -- 1. Raw connection -------------------------------------------------
    try:
        import psycopg2
    except ImportError:
        print("FAIL  psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    try:
        conn = psycopg2.connect(url, connect_timeout=10)
        conn.close()
        print("OK    Raw connection succeeds")
    except Exception as e:
        print(f"FAIL  Could not connect: {e}")
        sys.exit(1)

    # -- 2. DatabaseManager init / schema creation --------------------------
    try:
        from database import DatabaseManager
        # FIX (2026-08-01): was DatabaseManager(database_url=url), but
        # DatabaseManager.__init__ takes `db_url`, not `database_url`
        # (see database.py: `def __init__(self, db_url: str = DATABASE_URL)`).
        # That mismatch raised a TypeError on every run, so this
        # pre-deploy smoke test could never actually pass, regardless of
        # whether DATABASE_URL itself was correctly configured.
        db = DatabaseManager(db_url=url)
        print("OK    DatabaseManager initialised (tables created/verified)")
    except Exception as e:
        print(f"FAIL  DatabaseManager init failed: {e}")
        sys.exit(1)

    # -- 3. Account row exists for PAPER mode (created by _init_db) --------
    acc = db.get_account(mode="PAPER")
    if acc:
        print(f"OK    PAPER account present (current_capital=Rs.{acc['current_capital']:,.2f})")
    else:
        print("FAIL  No PAPER account row found after init")
        sys.exit(1)

    # -- 4. Round-trip a test option trade in an isolated TEST mode ---------
    # Values chosen so gross P&L and charges are unambiguous, and the
    # expected net figure is easy to hand-verify.
    entry_charges = 30.00
    exit_charges = 40.00
    gross_pnl = 500.00
    expected_net = gross_pnl - (entry_charges + exit_charges)  # 430.00

    pos_id = db.open_option_position(
        mode=TEST_MODE, underlying="NIFTY", option_symbol="SMOKETEST24000CE",
        option_type="CE", strike=24000.0, expiry="2099-01-01",
        premium=100.0, quantity=65, stop_loss=90.0, take_profit=110.0,
        entry_charges=entry_charges,
    )
    db.close_option_position(
        pos_id, exit_premium=107.69, exit_reason="Smoke test cleanup",
        pnl=gross_pnl, exit_charges=exit_charges,
    )
    print(f"OK    Test option trade opened and closed (id={pos_id[:8]})")

    # -- 5. Verify get_daily_pnl and get_trade_stats compute NET correctly --
    daily = db.get_daily_pnl(mode=TEST_MODE)
    stats = db.get_trade_stats(mode=TEST_MODE)

    daily_ok = abs(daily - expected_net) < 0.01
    stats_ok = abs(stats["total_pnl"] - expected_net) < 0.01

    print(f"{'OK' if daily_ok else 'FAIL'}    get_daily_pnl() = Rs.{daily:.2f} (expected Rs.{expected_net:.2f})")
    print(f"{'OK' if stats_ok else 'FAIL'}    get_trade_stats()['total_pnl'] = Rs.{stats['total_pnl']:.2f} (expected Rs.{expected_net:.2f})")

    # -- 6. Cleanup: remove the smoke-test row so it never lingers ---------
    with db.get_cursor() as c:
        c.execute("DELETE FROM option_positions WHERE id=%s", (pos_id,))
    print("OK    Smoke test row cleaned up")

    print("\n=== RESULT ===")
    if daily_ok and stats_ok:
        print("ALL CHECKS PASSED - DATABASE_URL is correctly configured.")
        print("Safe to commit/push and set DATABASE_URL in Render's dashboard.")
        sys.exit(0)
    else:
        print("SOME CHECKS FAILED - do not deploy yet, see failures above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
    
