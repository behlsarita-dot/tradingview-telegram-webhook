#!/usr/bin/env python3
"""
reconcile_alerts.py - Reconcile TradingView's own alert log export against
what actually landed in the Neon Postgres option_positions table.

WHY THIS EXISTS
----------------
TradingView's "Webhook status" column is reported on TradingView's own
client-side timer (roughly a 3s budget for DNS + TLS + routing to Render),
not on what your Render backend actually did with the signal. That cuts
both ways, and this script checks BOTH directions rather than just one:

  DIRECTION A - "Failed" but actually fine (confirmed noise)
    TradingView marks a delivery "failed" (its own round trip timed out)
    even though the webhook body reached your Flask app and the trade
    executed correctly. You've hand-confirmed this before (e.g.
    NIFTY260728P24100 on 2026-07-22). Not a real problem - just noisy
    status reporting on TradingView's side.

  DIRECTION B - "Successfully delivered" but nothing landed (the
  dangerous direction, and the one that actually bit you on 2026-08-03)
    TradingView's log says the webhook was delivered fine - and it WAS,
    the HTTP 200 came back - but the signal was then silently rejected
    downstream inside validate_new_trade() (kill switch, circuit
    breaker, R:R gate, max-positions gate, duplicate webhook_id guard,
    etc.) and no position was ever opened or closed. TradingView's UI
    has no way to show this at all - it only sees its own successful
    HTTP round trip, not what your app.py did afterwards. This is the
    one you only caught by manually cross-referencing Telegram history
    and check_options.py against the CE24400 BUY_OPTION at 09:20:01 IST
    this morning, which the stale-loss circuit-breaker bug rejected
    even though TradingView's log shows "Webhook successfully
    delivered."

Direction B has no other visibility path today, so this script surfaces
it as the higher-priority finding.

INPUT FORMAT
------------
This reads your ACTUAL TradingView alert log export as-is - the same
columns as TradingView_Alerts_Log_*.csv:

    Alert ID,Ticker,Name,Description,Time,Webhook status

The real action/symbol/premium/exit_reason data isn't in its own columns
- it's JSON embedded inside the Description field, e.g.:

    {"webhook_secret":"...","action":"BUY_OPTION","symbol":"NIFTY",
     "option_symbol":"NIFTY260811C24400","option_type":"CE","strike":24400,
     "expiry":"2026-08-11","premium":235.95,"quantity":65,
     "sl":225.95,"tp":245.95}

Rows whose Description isn't parseable JSON (e.g. the plain
"adx buy 24400 ce 5 min" preview alerts that fire alongside the real
webhook alerts but carry no payload) are skipped automatically - they
were never webhook deliveries in the first place.

Time is TradingView's UTC timestamp (e.g. "2026-08-03T03:50:01Z"). Your
DB's entry_time/exit_time columns are naive-clock IST strings (same
convention as the rest of this codebase - see database.py). This script
converts TradingView's UTC time to naive IST (+5:30) before comparing,
so windows actually line up instead of being silently off by 5.5 hours.

USAGE
-----
    python reconcile_alerts.py TradingView_Alerts_Log_2026-08-03.csv
    python reconcile_alerts.py TradingView_Alerts_Log_2026-08-03.csv --mode PAPER
    python reconcile_alerts.py TradingView_Alerts_Log_2026-08-03.csv --window-min 5 --price-tol 2.0

Options:
    --mode        account mode to check against (default: PAPER)
    --window-min  minutes of tolerance around the alert time when
                  searching the DB (default: 5)
    --price-tol   absolute premium tolerance for a price match
                  (default: 2.0)

CONFIGURATION
-------------
Reads the Postgres connection the same way your other check_*.py scripts
do. Tries, in order:
  1. `from config import DATABASE_URL`  (your existing config.py)
  2. DATABASE_URL environment variable
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. pip install psycopg2-binary --break-system-packages")
    sys.exit(1)

IST_OFFSET = timedelta(hours=5, minutes=30)

# Only these actions carry a real webhook payload worth reconciling.
# BUY/SELL preview-alert rows (no JSON body) are filtered out separately.
ACTION_TO_SIDE = {
    "BUY_OPTION": "OPEN",
    "SELL_OPTION": "OPEN",
    "EXIT_OPTION": "CLOSE",
}


def get_database_url() -> str:
    try:
        from config import DATABASE_URL  # your existing config.py
        if DATABASE_URL:
            return DATABASE_URL
    except ImportError:
        pass

    import os
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    print("ERROR: Could not find DATABASE_URL.")
    print("  Either run this script from your project folder (so it can")
    print("  `from config import DATABASE_URL`), or set the DATABASE_URL")
    print("  environment variable before running.")
    sys.exit(1)


def connect():
    url = get_database_url()
    return psycopg2.connect(url, connect_timeout=8)


def parse_tv_time(raw: str) -> datetime:
    """TradingView's Time column is UTC, ISO8601, with a trailing 'Z'
    (e.g. '2026-08-03T03:50:01Z'). Convert to a naive IST datetime so it
    lines up with entry_time/exit_time, which are stored as naive-clock
    IST strings via datetime.now().isoformat() everywhere in database.py
    - no tz-aware objects anywhere in that table."""
    raw = raw.strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    utc_dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    return utc_dt + IST_OFFSET


def classify_webhook_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if "successfully delivered" in s or s == "sent" or s == "delivered":
        return "delivered"
    if "failed" in s or "timed out" in s:
        return "failed"
    return "unknown"


def load_alert_log(path: str) -> list:
    """Parses the real TradingView export format:
    Alert ID,Ticker,Name,Description,Time,Webhook status

    The payload lives inside Description as JSON. Rows where Description
    isn't valid JSON (plain preview-alert text with no webhook body) are
    skipped - they were never webhook deliveries."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [h.strip() for h in (reader.fieldnames or [])]
        required = {"Alert ID", "Ticker", "Description", "Time", "Webhook status"}
        missing = required - set(fieldnames)
        if missing:
            print(f"ERROR: CSV is missing required column(s): {', '.join(sorted(missing))}")
            print(f"  Found columns: {reader.fieldnames}")
            sys.exit(1)

        for i, row in enumerate(reader, start=2):  # start=2: header is line 1
            desc_raw = (row.get("Description") or "").strip()
            try:
                payload = json.loads(desc_raw)
            except (json.JSONDecodeError, TypeError):
                continue  # not a webhook delivery row (preview alert text)

            action = str(payload.get("action", "")).upper()
            if action not in ACTION_TO_SIDE:
                continue  # not an option BUY/SELL/EXIT payload

            time_raw = (row.get("Time") or "").strip()
            try:
                t_ist = parse_tv_time(time_raw)
            except ValueError as e:
                print(f"  WARN: skipping line {i}, unrecognized time {time_raw!r} ({e})")
                continue

            rows.append({
                "line": i,
                "alert_id": row.get("Alert ID"),
                "time_ist": t_ist,
                "action": action,
                "side": ACTION_TO_SIDE[action],
                "option_symbol": payload.get("option_symbol") or payload.get("symbol"),
                "premium": payload.get("premium"),
                "exit_reason": payload.get("exit_reason"),
                "status_raw": (row.get("Webhook status") or "").strip(),
                "status": classify_webhook_status(row.get("Webhook status")),
            })
    return rows


def find_matching_position(cur, alert: dict, mode: str, window_min: int, price_tol: float):
    """Search option_positions (real schema: underlying, option_symbol,
    premium, exit_premium, entry_time, exit_time - all TEXT columns
    needing an explicit ::timestamp cast, same convention as
    get_daily_pnl()/_get_recent_closed_trades() in database.py)."""
    window = timedelta(minutes=window_min)
    lo, hi = alert["time_ist"] - window, alert["time_ist"] + window
    symbol = alert["option_symbol"] or ""

    if alert["side"] == "OPEN":
        cur.execute("""
            SELECT id, option_symbol, premium, entry_time, webhook_id
            FROM option_positions
            WHERE mode=%s
              AND option_symbol ILIKE %s
              AND entry_time::timestamp BETWEEN %s AND %s
            ORDER BY entry_time
        """, (mode, f"%{symbol}%", lo, hi))
        candidates = cur.fetchall()
        for c in candidates:
            if alert["premium"] is None or abs(float(c["premium"]) - alert["premium"]) <= price_tol:
                return c
        return candidates[0] if candidates else None

    else:  # CLOSE
        cur.execute("""
            SELECT id, option_symbol, exit_premium, exit_time, exit_reason, webhook_id
            FROM option_positions
            WHERE mode=%s
              AND option_symbol ILIKE %s
              AND exit_time IS NOT NULL
              AND exit_time::timestamp BETWEEN %s AND %s
            ORDER BY exit_time
        """, (mode, f"%{symbol}%", lo, hi))
        candidates = cur.fetchall()
        for c in candidates:
            if alert["premium"] is None or c["exit_premium"] is None:
                continue
            if abs(float(c["exit_premium"]) - alert["premium"]) <= price_tol:
                return c
        return candidates[0] if candidates else None


def main():
    ap = argparse.ArgumentParser(
        description="Reconcile a TradingView alert log export against real Neon DB positions"
    )
    ap.add_argument("csv_path", help="Path to your exported TradingView_Alerts_Log_*.csv")
    ap.add_argument("--mode", default="PAPER", help="Account mode to check against (default: PAPER)")
    ap.add_argument("--window-min", type=int, default=5, help="Minutes of tolerance for time matching")
    ap.add_argument("--price-tol", type=float, default=2.0, help="Absolute premium tolerance for price matching")
    args = ap.parse_args()

    print(f"Loading alert log: {args.csv_path}")
    alerts = load_alert_log(args.csv_path)
    print(f"Found {len(alerts)} option webhook alert(s) (BUY_OPTION/SELL_OPTION/EXIT_OPTION with a parseable payload)\n")

    if not alerts:
        print("Nothing to reconcile.")
        return

    conn = connect()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    confirmed_noise = []       # status=failed, but DB shows it landed -> harmless
    real_failures = []         # status=failed, and genuinely missing -> real gap
    silent_rejections = []     # status=delivered, but nothing in DB -> DANGEROUS, no other visibility
    confirmed_delivered = []   # status=delivered, and DB matches -> all good, not printed in detail

    for alert in alerts:
        match = find_matching_position(cur, alert, args.mode, args.window_min, args.price_tol)
        if alert["status"] == "failed":
            (confirmed_noise if match else real_failures).append((alert, match))
        elif alert["status"] == "delivered":
            (confirmed_delivered if match else silent_rejections).append((alert, match))
        else:
            # unknown/blank status - still worth flagging like a real failure
            (confirmed_noise if match else real_failures).append((alert, match))

    cur.close()
    conn.close()

    def fmt(alert, match):
        base = (f"  [line {alert['line']}] {alert['time_ist']} IST  {alert['option_symbol']}  "
                f"{alert['action']}  (premium {alert['premium']})  status='{alert['status_raw']}'")
        if match:
            base += f"  -> matched position id={match.get('id')}"
        return base

    print("=" * 78)
    print("RECONCILIATION RESULT")
    print("=" * 78)

    if silent_rejections:
        print(f"\n*** SILENT REJECTIONS ({len(silent_rejections)}) - TradingView says delivered, "
              f"but NO matching position exists in the DB. ***")
        print("    These are invisible in TradingView's own UI - the webhook truly was")
        print("    delivered (HTTP 200), but was rejected downstream (kill switch, circuit")
        print("    breaker, R:R gate, max positions, duplicate webhook_id, etc.) and no")
        print("    trade was ever opened or closed. Investigate each one via Telegram")
        print("    history and check_options.py / check_risk.py for what was active at")
        print("    that exact timestamp.")
        for alert, match in silent_rejections:
            print(fmt(alert, match))
    else:
        print("\nNo silent rejections found - every 'delivered' alert has a matching DB position.")

    if confirmed_noise:
        print(f"\nCONFIRMED NOISE ({len(confirmed_noise)}) - TradingView marked this failed/unknown, "
              f"but the DB shows the trade executed fine. Harmless, TradingView-side timing only:")
        for alert, match in confirmed_noise:
            print(fmt(alert, match))

    if real_failures:
        print(f"\nREAL FAILURES ({len(real_failures)}) - marked failed/unknown, and genuinely "
              f"no matching position found. Worth investigating:")
        for alert, match in real_failures:
            print(fmt(alert, match))

    print(f"\n(Also confirmed normal: {len(confirmed_delivered)} alert(s) marked delivered with a matching DB position - not listed individually.)")
    print()


if __name__ == "__main__":
    main()