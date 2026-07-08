#!/usr/bin/env python3
"""
Pre-Market Bot Checklist - Automated Section 3 Checks
Paper Trading System v7.0

Run this every morning before 09:15 IST, from inside the repo directory
(same place you run webhook_tester.py from).

Automates:
  - Render /health endpoint check
  - Git: confirms local HEAD matches what's pushed to origin (catches the
    "forgot to git push" and "pushed but forgot to manually deploy on
    Render" failure modes, since auto-deploy is off)
  - Webhook route reachability (expects 405 on GET - that's healthy)

Does NOT replace the manual TradingView checks in Sections 1, 2, and 4
of the checklist doc - alert existence, webhookSecret/strike/expiry
inputs, and live confirmation after the first signal still need eyes on.
"""

import subprocess
import sys
import time
from datetime import datetime

import requests

# ---- CONFIG -----------------------------------------------------------
BASE_URL = "https://tradingview-telegram-webhook-dpaj.onrender.com"
HEALTH_ENDPOINT = f"{BASE_URL}/health"
WEBHOOK_ENDPOINT = f"{BASE_URL}/api/webhook"
REQUEST_TIMEOUT = 15  # seconds - Render free/low tiers can cold-start slow
# ------------------------------------------------------------------------

PASS = "  OK  "
FAIL = " FAIL "
WARN = " WARN "


def line(status, label, detail=""):
    print(f"{status} {label}" + (f" - {detail}" if detail else ""))


def check_health():
    try:
        start = time.time()
        r = requests.get(HEALTH_ENDPOINT, timeout=REQUEST_TIMEOUT)
        elapsed = time.time() - start
        if r.status_code == 200:
            line(PASS, "Render /health", f"200 OK in {elapsed:.1f}s")
            try:
                print(f"         {r.json()}")
            except ValueError:
                print(f"         (non-JSON body: {r.text[:120]})")
            if elapsed > 5:
                line(WARN, "Slow response", "service may have been cold-starting; re-check in a minute")
            return True
        else:
            line(FAIL, "Render /health", f"got {r.status_code}, expected 200")
            return False
    except requests.exceptions.RequestException as e:
        line(FAIL, "Render /health", f"unreachable - {e}")
        return False


def check_webhook_alive():
    try:
        r = requests.get(WEBHOOK_ENDPOINT, timeout=REQUEST_TIMEOUT)
        if r.status_code == 405:
            line(PASS, "Webhook route reachable", "405 on GET as expected (route only accepts POST)")
            return True
        else:
            line(WARN, "Webhook route", f"got {r.status_code}, expected 405 - investigate if unexpected")
            return False
    except requests.exceptions.RequestException as e:
        line(FAIL, "Webhook route", f"unreachable - {e}")
        return False


def run_git(args):
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, timeout=20
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 1, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return 1, "", "git command timed out"


def check_git_sync():
    code, _, err = run_git(["rev-parse", "--is-inside-work-tree"])
    if code != 0:
        line(WARN, "Git check skipped", "not inside a git repo (run this from the repo directory)")
        return

    run_git(["fetch", "origin"])  # best-effort, ignore failures (e.g. no network)

    code, local_head, _ = run_git(["rev-parse", "HEAD"])
    code2, remote_head, _ = run_git(["rev-parse", "origin/HEAD"])
    if code2 != 0:
        # fall back to origin/main if origin/HEAD isn't set locally
        code2, remote_head, _ = run_git(["rev-parse", "origin/main"])

    if code == 0 and code2 == 0:
        if local_head == remote_head:
            line(PASS, "Git: local matches origin", local_head[:8])
        else:
            line(FAIL, "Git: local HEAD != origin", f"local {local_head[:8]} vs origin {remote_head[:8]} - push before relying on today's deploy")
    else:
        line(WARN, "Git comparison inconclusive", "check manually with 'git status' and 'git log origin/main -1'")

    code, status_out, _ = run_git(["status", "--porcelain"])
    if code == 0 and status_out:
        line(WARN, "Uncommitted changes present", "these are NOT on Render regardless of last push")

    print()
    line(WARN, "Reminder", "auto-deploy is OFF - a matching git push does not mean Render redeployed. Confirm the deploy timestamp on the Render dashboard manually.")


def main():
    print(f"=== Pre-Market Check | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST ===\n")

    print("-- Backend --")
    health_ok = check_health()
    check_webhook_alive()

    print("\n-- Deploy Sync --")
    check_git_sync()

    print("\n-- Still manual (Sections 1, 2, 4 of checklist) --")
    print("  - TradingView CE/PE alerts exist, Active, correct webhook URL")
    print("  - webhookSecret / optionStrike / optionExpiry inputs current on the chart")
    print("  - After first signal: Render log shows POST 200 + Telegram notification")

    print()
    if not health_ok:
        print("RESULT: Backend not healthy - do not assume signals will reach the bot today.")
        sys.exit(1)
    else:
        print("RESULT: Backend checks passed. Proceed to manual TradingView checks.")


if __name__ == "__main__":
    main()
    