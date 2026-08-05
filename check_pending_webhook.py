#!/usr/bin/env python3
"""
check_pending_webhook.py - Look up every pending_webhooks row for a
specific option_symbol, to see exactly what your server received and
what happened to it - regardless of whether a position ever resulted.

Same connection convention as check_options.py / check_risk.py: reads
DATABASE_URL from .env via python-dotenv.

USAGE
-----
    python check_pending_webhook.py NIFTY260811P24300
    python check_pending_webhook.py NIFTY260811P24300 --mode PAPER
"""

import os
import sys
import argparse
from datetime import datetime
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env")
    sys.exit(1)


def _as_datetime(value):
    """Coerce a value to a datetime. psycopg2 normally returns native
    datetime objects for timestamp columns, but if these columns are
    stored as TEXT (or the value otherwise comes back as a str), parse
    it with fromisoformat instead of failing on 'str - str'."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _fmt_delta(start, end):
    """Format a timedelta between two datetimes as '+N.NNs', or '--'
    if either side is missing (e.g. claimed_at is still null because
    the row is stuck in the queue)."""
    start = _as_datetime(start)
    end = _as_datetime(end)
    if start is None or end is None:
        return "--"
    delta = (end - start).total_seconds()
    return f"+{delta:.2f}s"


def main():
    ap = argparse.ArgumentParser(description="Check pending_webhooks history for an option_symbol")
    ap.add_argument("option_symbol", help="e.g. NIFTY260811P24300")
    ap.add_argument("--mode", default=None, help="Filter by mode (e.g. PAPER) - omit to check all modes")
    args = ap.parse_args()

    conn = psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=8)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    query = """
        SELECT id, mode, action, symbol,
               payload->>'option_symbol' AS option_symbol,
               payload->>'premium' AS premium,
               payload->>'exit_reason' AS exit_reason,
               status, created_at, claimed_at, processed_at, error
        FROM pending_webhooks
        WHERE payload->>'option_symbol' = %s
    """
    params = [args.option_symbol]
    if args.mode:
        query += " AND mode = %s"
        params.append(args.mode)
    query += " ORDER BY created_at"

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\n=== pending_webhooks history for {args.option_symbol} "
          f"{'(mode=' + args.mode + ')' if args.mode else '(all modes)'} ===\n")

    if not rows:
        print("No rows found. This means your server's /api/webhook route")
        print("was NEVER called for this option_symbol - the desync originated")
        print("entirely on the Pine script / TradingView side (the alert either")
        print("never fired a real webhook call, or the payload's option_symbol")
        print("didn't match what you searched for - double check spelling).")
        return

    for r in rows:
        # NEW: queue latency timing - how long a row sat waiting to be
        # claimed off the queue, and how long it took to process once
        # claimed. Useful for spotting a stuck row (claimed_at never
        # set) or a processing time creeping back up toward
        # TradingView's 3-second webhook timeout.
        claim_wait = _fmt_delta(r["created_at"], r["claimed_at"])
        process_time = _fmt_delta(r["claimed_at"], r["processed_at"])
        total_time = _fmt_delta(r["created_at"], r["processed_at"])

        print(f"  id={r['id']}  mode={r['mode']}  action={r['action']}")
        print(f"    premium={r['premium']}  exit_reason={r['exit_reason']}")
        print(f"    status={r['status']}")
        print(f"    created_at={r['created_at']}  claimed_at={r['claimed_at']}  processed_at={r['processed_at']}")
        print(f"    queue latency: created->claimed {claim_wait}  |  "
              f"claimed->processed {process_time}  |  total {total_time}")
        if r.get("error"):
            print(f"    error={r['error']}")
        print()

    print("HOW TO READ THIS:")
    print("  - If a BUY_OPTION row shows status=PROCESSED but no matching")
    print("    position exists in option_positions (check via check_options.py),")
    print("    it was rejected downstream inside validate_new_trade() - check")
    print("    Telegram around this row's created_at for a 'Signal rejected' or")
    print("    'CIRCUIT BREAKER TRIGGERED' message.")
    print("  - If a BUY_OPTION row shows status=FAILED, see the error column above.")
    print("  - If there's NO BUY_OPTION row here at all for this symbol, the Pine")
    print("    script never actually sent one - the desync is Pine-side, not")
    print("    backend-side.")
    print("  - claimed_at=None with status still PENDING means the row is")
    print("    stuck in queue - check whether _webhook_worker is running")
    print("    (market-hours gating may have it correctly idle outside")
    print("    trading hours, or it may be genuinely stuck).")
    print("  - A 'claimed->processed' time approaching or exceeding 3s is worth")
    print("    watching - that's TradingView's webhook timeout window, though")
    print("    since processing is async via the queue, exceeding it here does")
    print("    NOT itself cause the timeout you saw in the pre-queue system.")
    print()


if __name__ == "__main__":
    main()