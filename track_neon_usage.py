#!/usr/bin/env python3
"""
track_neon_usage.py - Log Neon compute-hour usage readings over time and
see the delta since your last reading, so you don't have to do the
subtraction by hand every time you check the dashboard.

Neon doesn't expose a simple API for this figure without extra project
setup, so this just stores whatever number YOU read off the console
(the "Compute X / 100CU-hrs" line) - it's a manual log, not a live pull.

Data is stored in neon_usage_log.json in the same directory as this
script, so it persists across runs (and survives git-ignoring it if you
don't want the numbers in version control).

USAGE
-----
    python track_neon_usage.py 10.7
        Logs a new reading of 10.7 CU-hrs at the current timestamp,
        and prints the delta + implied daily rate since your last entry.

    python track_neon_usage.py
        No new reading - just shows the log history and deltas so far.

    python track_neon_usage.py --reset
        Wipes the log (e.g. if you want to start fresh after a new
        billing period begins, since Neon's own total resets monthly).
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

LOG_PATH = Path(__file__).parent / "neon_usage_log.json"


def load_log():
    if not LOG_PATH.exists():
        return []
    try:
        with open(LOG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"WARNING: {LOG_PATH} is unreadable/corrupt - starting a fresh log.")
        return []


def save_log(entries):
    with open(LOG_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def print_history(entries):
    if not entries:
        print("No readings logged yet. Run: python track_neon_usage.py <current_cu_hrs>")
        return

    print(f"\n=== Neon compute-hour usage log ({LOG_PATH.name}) ===\n")
    prev = None
    for e in entries:
        ts = datetime.fromisoformat(e["timestamp"])
        cu = e["cu_hours"]
        line = f"  {ts.strftime('%Y-%m-%d %H:%M')}  {cu:>6.2f} CU-hrs"
        if prev is not None:
            prev_ts, prev_cu = prev
            delta_cu = cu - prev_cu
            delta_hours = (ts - prev_ts).total_seconds() / 3600
            if delta_hours > 0:
                rate_per_day = (delta_cu / delta_hours) * 24
                line += f"   (+{delta_cu:.2f} over {delta_hours:.1f}h -> ~{rate_per_day:.2f} CU-hrs/day)"
            else:
                line += f"   (+{delta_cu:.2f})"
        print(line)
        prev = (ts, cu)
    print()

    if len(entries) >= 2:
        first_ts = datetime.fromisoformat(entries[0]["timestamp"])
        last_ts = datetime.fromisoformat(entries[-1]["timestamp"])
        total_delta = entries[-1]["cu_hours"] - entries[0]["cu_hours"]
        total_hours = (last_ts - first_ts).total_seconds() / 3600
        if total_hours > 0:
            overall_rate = (total_delta / total_hours) * 24
            print(f"Overall since first reading: +{total_delta:.2f} CU-hrs "
                  f"over {total_hours:.1f}h -> ~{overall_rate:.2f} CU-hrs/day average\n")


def main():
    ap = argparse.ArgumentParser(description="Log and track Neon compute-hour usage over time")
    ap.add_argument("cu_hours", nargs="?", type=float,
                     help="Current 'Compute X / 100CU-hrs' reading from the Neon dashboard")
    ap.add_argument("--reset", action="store_true", help="Wipe the log and start fresh")
    args = ap.parse_args()

    if args.reset:
        if LOG_PATH.exists():
            LOG_PATH.unlink()
        print(f"Log reset. {LOG_PATH.name} removed.")
        return

    entries = load_log()

    if args.cu_hours is not None:
        entries.append({
            "timestamp": datetime.now().isoformat(),
            "cu_hours": args.cu_hours,
        })
        save_log(entries)

    print_history(entries)


if __name__ == "__main__":
    main()