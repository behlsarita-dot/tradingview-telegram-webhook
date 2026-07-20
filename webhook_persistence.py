"""
test_webhook_persistence.py
============================
Verifies the new pending_webhooks queue (app.py + database.py, updated
2026-07-20) actually does what it's supposed to: survive a process dying
mid-flight without losing the trade.

Two things are tested, as two separate commands:

  1. --basic     Sends a real webhook via HTTP (like webhook_tester.py),
                  then polls pending_webhooks directly to confirm the row
                  goes PENDING -> PROCESSING -> PROCESSED within a few
                  seconds. Proves the normal (non-crash) path works.

  2. --setup-stale
                  Inserts a FAKE row directly into pending_webhooks with
                  status='PROCESSING' and claimed_at set far enough in the
                  past to look stale (default: 90s ago, past the 60s
                  threshold in recover_stuck_webhooks()). This
                  deterministically simulates "a previous process claimed
                  this webhook and then died before finishing" WITHOUT
                  needing to race the timing of actually killing app.py
                  mid-flight.

                  After running this, (re)start app.py locally. On
                  startup it calls db.recover_stuck_webhooks(), which
                  should find this row, reset it to PENDING, log
                  "Recovered 1 webhook(s)...", and send the Telegram
                  alert. The worker thread will then pick it up and
                  process it as BUY_OPTION SMOKETEST... (harmless -
                  uses the TEST mode account only).

  3. --verify     Run this AFTER restarting app.py. Checks that the fake
                  row from --setup-stale is now PROCESSED (or at least
                  no longer stuck in PROCESSING), proving the recovery
                  path worked end-to-end.

Usage:
    python test_webhook_persistence.py --basic
    python test_webhook_persistence.py --setup-stale
    # ... now Ctrl+C and restart app.py locally, watch its startup logs ...
    python test_webhook_persistence.py --verify
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
LOCAL_URL = "http://127.0.0.1:5000"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
TEST_MODE = "TEST"  # matches local app.py's TRADING_MODE=TEST

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env")
    sys.exit(1)
if not WEBHOOK_SECRET:
    print("ERROR: WEBHOOK_SECRET not found in .env")
    sys.exit(1)


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=8)


def check_table_exists():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'pending_webhooks'
                )
            """)
            exists = c.fetchone()[0]
    if not exists:
        print("ERROR: pending_webhooks table does not exist yet.")
        print("Start app.py at least once with the new database.py so _init_db() creates it.")
        sys.exit(1)


def cmd_basic():
    check_table_exists()
    print("Sending test BUY_OPTION webhook to local server...")

    payload = {
        "webhook_secret": WEBHOOK_SECRET,
        "action": "BUY_OPTION",
        "symbol": "NIFTY",
        "option_symbol": "PERSISTTEST24000CE",
        "option_type": "CE",
        "strike": 24000,
        "expiry": "2026-07-31",
        "premium": 50.0,
        "quantity": 65,
    }

    resp = requests.post(f"{LOCAL_URL}/api/webhook", json=payload, timeout=5)
    print(f"HTTP response: {resp.status_code} {resp.json()}")

    if resp.status_code != 200:
        print("FAILED: webhook did not return 200, aborting test.")
        sys.exit(1)

    print("\nPolling pending_webhooks for this row's status (up to 10s)...")
    deadline = time.time() + 10
    last_status = None
    while time.time() < deadline:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
                c.execute("""
                    SELECT id, status, created_at, claimed_at, processed_at
                    FROM pending_webhooks
                    WHERE mode=%s AND symbol='NIFTY' AND payload->>'option_symbol'='PERSISTTEST24000CE'
                    ORDER BY id DESC LIMIT 1
                """, (TEST_MODE,))
                row = c.fetchone()
        if row:
            if row["status"] != last_status:
                print(f"  status={row['status']}  claimed_at={row['claimed_at']}  processed_at={row['processed_at']}")
                last_status = row["status"]
            if row["status"] == "PROCESSED":
                print("\nPASSED: row reached PROCESSED status. Normal path works.")
                return
            if row["status"] == "FAILED":
                print(f"\nFAILED: row marked FAILED. error={row.get('error')}")
                sys.exit(1)
        time.sleep(0.5)

    print(f"\nTIMEOUT: row never reached PROCESSED within 10s (last status: {last_status}).")
    print("Check that app.py's worker thread is running and polling.")
    sys.exit(1)


def cmd_setup_stale(stale_seconds: int):
    check_table_exists()
    now = datetime.now()
    fake_claimed_at = (now - timedelta(seconds=stale_seconds)).isoformat()
    fake_created_at = (now - timedelta(seconds=stale_seconds + 5)).isoformat()

    payload = {
        "webhook_secret": WEBHOOK_SECRET,
        "action": "BUY_OPTION",
        "symbol": "NIFTY",
        "option_symbol": "SMOKETESTRECOVERY24000CE",
        "option_type": "CE",
        "strike": 24000,
        "expiry": "2026-07-31",
        "premium": 42.0,
        "quantity": 65,
    }

    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO pending_webhooks
                    (mode, action, symbol, payload, status, created_at, claimed_at)
                VALUES (%s, 'BUY_OPTION', 'NIFTY', %s, 'PROCESSING', %s, %s)
                RETURNING id
            """, (TEST_MODE, json.dumps(payload), fake_created_at, fake_claimed_at))
            new_id = c.fetchone()[0]
        conn.commit()

    print(f"Inserted fake stale PROCESSING row (id={new_id}), claimed_at={stale_seconds}s ago.")
    print(f"This simulates a process that claimed the webhook and died before finishing.")
    print()
    print("NEXT STEPS:")
    print("  1. If app.py is currently running locally, stop it (Ctrl+C).")
    print("  2. Start it again: python app.py")
    print("  3. Watch the startup log for a line like:")
    print(f"       [WARNING] __main__: Recovered 1 webhook(s) stuck in PROCESSING...")
    print("     and check Telegram for the matching alert.")
    print(f"  4. Then run: python test_webhook_persistence.py --verify --row-id {new_id}")


def cmd_verify(row_id: int):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("""
                SELECT id, status, created_at, claimed_at, processed_at, error
                FROM pending_webhooks WHERE id=%s
            """, (row_id,))
            row = c.fetchone()

    if not row:
        print(f"ERROR: no row with id={row_id} found.")
        sys.exit(1)

    print(f"Row {row_id}: status={row['status']}")
    print(f"  created_at:   {row['created_at']}")
    print(f"  claimed_at:   {row['claimed_at']}")
    print(f"  processed_at: {row['processed_at']}")
    if row.get("error"):
        print(f"  error:        {row['error']}")

    if row["status"] == "PROCESSED":
        print("\nPASSED: the stale row was recovered and successfully processed.")
        print("recover_stuck_webhooks() + claim_next_pending_webhook() worked end-to-end.")
    elif row["status"] == "PENDING":
        print("\nPARTIAL: row was reset to PENDING (recovery ran) but not yet processed.")
        print("Wait a few seconds for the worker thread to pick it up, then re-run --verify.")
    elif row["status"] == "PROCESSING":
        print("\nFAILED: row is still PROCESSING with the OLD stale claimed_at.")
        print("This means recover_stuck_webhooks() did NOT run, or did not find this row.")
        print("Check: did app.py actually restart? Is TRADING_MODE=TEST locally? "
              "Check the timezone note in the patch review before assuming this is broken.")
    elif row["status"] == "FAILED":
        print(f"\nFAILED: row processed but marked FAILED. error={row.get('error')}")


def main():
    p = argparse.ArgumentParser(description="Test the persisted webhook queue")
    p.add_argument("--basic", action="store_true", help="Test normal enqueue -> process path")
    p.add_argument("--setup-stale", action="store_true", help="Insert a fake stale PROCESSING row")
    p.add_argument("--stale-seconds", type=int, default=90,
                   help="How old to make the fake claimed_at (default 90, past the 60s threshold)")
    p.add_argument("--verify", action="store_true", help="Check outcome after restarting app.py")
    p.add_argument("--row-id", type=int, help="Row id to check (printed by --setup-stale)")
    args = p.parse_args()

    if args.basic:
        cmd_basic()
    elif args.setup_stale:
        cmd_setup_stale(args.stale_seconds)
    elif args.verify:
        if not args.row_id:
            print("ERROR: --verify requires --row-id <id> (printed by --setup-stale)")
            sys.exit(1)
        cmd_verify(args.row_id)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
    