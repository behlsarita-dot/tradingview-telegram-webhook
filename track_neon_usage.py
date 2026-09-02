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

Neon's 100 CU-hr free-tier cap resets on a monthly billing cycle. This
script auto-detects that reset (whenever a new reading is LOWER than
the previous one) and starts a new "billing period" instead of treating
it as negative usage. History is grouped and printed by period, and the
cap projection is calculated using only the CURRENT period's readings.

USAGE
-----
    python track_neon_usage.py 10.7
        Logs a new reading of 10.7 CU-hrs at the current timestamp,
        and prints the delta + implied daily rate since your last entry.

    python track_neon_usage.py
        No new reading - just shows the log history and deltas so far.

    python track_neon_usage.py --delete-last
        Removes the single most recent entry (e.g. you fat-fingered a
        duplicate/wrong number and want to redo it) without wiping the
        whole log. Prints what was removed for confirmation.

    python track_neon_usage.py --reset
        Wipes the entire log (e.g. if you want to start fresh, though
        this is no longer necessary for monthly resets - the script now
        detects those automatically. Use this only to nuke the log
        entirely.)
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

LOG_PATH = Path(__file__).parent / "neon_usage_log.json"

CAP_CU_HRS = 100.0        # Neon free-tier compute-hour cap (per billing period)
WARN_THRESHOLD = 80.0     # warn once cumulative usage in the period crosses this
TRAILING_WINDOW = 7       # number of most recent readings (within period) for the "recent pace" rate
RUNWAY_WARN_DAYS = 14     # also warn if projected runway drops under this many days


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


def split_into_periods(entries):
    """Split the flat entry list into billing periods. A new period starts
    whenever a reading is lower than the one before it (Neon's monthly
    reset). Returns a list of lists, oldest period first."""
    periods = []
    current = []
    for e in entries:
        if current and e["cu_hours"] < current[-1]["cu_hours"]:
            periods.append(current)
            current = []
        current.append(e)
    if current:
        periods.append(current)
    return periods


def period_label(period):
    """Human label for a period, e.g. 'August 2026' or 'Aug-Sep 2026' if
    it spans a calendar-month boundary."""
    first_ts = datetime.fromisoformat(period[0]["timestamp"])
    last_ts = datetime.fromisoformat(period[-1]["timestamp"])
    if (first_ts.year, first_ts.month) == (last_ts.year, last_ts.month):
        return first_ts.strftime("%B %Y")
    return f"{first_ts.strftime('%b')}-{last_ts.strftime('%b %Y')}"


def print_cap_projection(period):
    """Show remaining budget and days-to-cap projections for the CURRENT
    billing period, with a warning if usage is getting close to the
    100 CU-hr free-tier cap."""
    if len(period) < 2:
        return  # not enough data in this period to project a rate yet

    cumulative = period[-1]["cu_hours"]
    remaining = CAP_CU_HRS - cumulative

    first_ts = datetime.fromisoformat(period[0]["timestamp"])
    last_ts = datetime.fromisoformat(period[-1]["timestamp"])
    total_delta = period[-1]["cu_hours"] - period[0]["cu_hours"]
    total_hours = (last_ts - first_ts).total_seconds() / 3600
    overall_rate = (total_delta / total_hours) * 24 if total_hours > 0 else 0

    # Trailing-window rate, using only the last TRAILING_WINDOW readings in this period
    window = period[-TRAILING_WINDOW:] if len(period) > TRAILING_WINDOW else period
    w_first_ts = datetime.fromisoformat(window[0]["timestamp"])
    w_last_ts = datetime.fromisoformat(window[-1]["timestamp"])
    w_delta = window[-1]["cu_hours"] - window[0]["cu_hours"]
    w_hours = (w_last_ts - w_first_ts).total_seconds() / 3600
    trailing_rate = (w_delta / w_hours) * 24 if w_hours > 0 else None

    days_left_overall = (remaining / overall_rate) if overall_rate > 0 else float("inf")
    days_left_trailing = (remaining / trailing_rate) if trailing_rate and trailing_rate > 0 else None

    print(f"--- Cap projection (current period: {period_label(period)}) ---")
    print(f"Remaining budget: {remaining:.2f} CU-hrs (cap: {CAP_CU_HRS:.0f} CU-hrs)")
    print(f"Runway at period average ({overall_rate:.2f} CU-hrs/day): ~{days_left_overall:.0f} days")
    if days_left_trailing is not None:
        print(f"Runway at last {len(window)}-reading pace ({trailing_rate:.2f} CU-hrs/day): ~{days_left_trailing:.0f} days")

    if cumulative >= WARN_THRESHOLD:
        print(f"\n⚠️  WARNING: cumulative usage this period ({cumulative:.2f} CU-hrs) has crossed the "
              f"{WARN_THRESHOLD:.0f} CU-hr threshold - only {remaining:.2f} CU-hrs left before the cap.")
    elif days_left_trailing is not None and days_left_trailing < RUNWAY_WARN_DAYS:
        print(f"\n⚠️  WARNING: at the recent usage pace, you're projected to hit the cap in "
              f"under {RUNWAY_WARN_DAYS} days (~{days_left_trailing:.0f} days).")
    print()


def print_history(entries):
    if not entries:
        print("No readings logged yet. Run: python track_neon_usage.py <current_cu_hrs>")
        return

    print(f"\n=== Neon compute-hour usage log ({LOG_PATH.name}) ===\n")

    periods = split_into_periods(entries)

    for i, period in enumerate(periods):
        is_current = (i == len(periods) - 1)
        label = period_label(period)
        tag = " (current)" if is_current else ""
        print(f"--- {label}{tag} ---")

        prev = None
        for e in period:
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
            elif e is not entries[0]:
                # first entry of a period that isn't the very first entry overall = a reset happened
                line += "   (billing period reset detected)"
            print(line)
            prev = (ts, cu)

        if len(period) >= 2:
            first_ts = datetime.fromisoformat(period[0]["timestamp"])
            last_ts = datetime.fromisoformat(period[-1]["timestamp"])
            total_delta = period[-1]["cu_hours"] - period[0]["cu_hours"]
            total_hours = (last_ts - first_ts).total_seconds() / 3600
            if total_hours > 0:
                rate = (total_delta / total_hours) * 24
                print(f"  Period total: +{total_delta:.2f} CU-hrs over {total_hours:.1f}h -> ~{rate:.2f} CU-hrs/day average")
        print()

    # Cap projection only makes sense for the current (most recent) period,
    # since the cap resets each billing period.
    print_cap_projection(periods[-1])


def main():
    ap = argparse.ArgumentParser(description="Log and track Neon compute-hour usage over time")
    ap.add_argument("cu_hours", nargs="?", type=float,
                     help="Current 'Compute X / 100CU-hrs' reading from the Neon dashboard")
    ap.add_argument("--reset", action="store_true", help="Wipe the entire log and start fresh")
    ap.add_argument("--delete-last", action="store_true",
                     help="Remove only the most recent entry (e.g. to fix a fat-fingered/duplicate reading)")
    args = ap.parse_args()

    if args.reset:
        if LOG_PATH.exists():
            LOG_PATH.unlink()
        print(f"Log reset. {LOG_PATH.name} removed.")
        return

    if args.delete_last:
        entries = load_log()
        if not entries:
            print("Log is already empty - nothing to delete.")
            return
        removed = entries.pop()
        save_log(entries)
        ts = datetime.fromisoformat(removed["timestamp"])
        print(f"Removed most recent entry: {ts.strftime('%Y-%m-%d %H:%M')}  "
              f"{removed['cu_hours']:.2f} CU-hrs")
        print_history(entries)
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