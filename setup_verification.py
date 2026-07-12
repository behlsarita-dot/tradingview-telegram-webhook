#!/usr/bin/env python3
"""Setup Verification - Paper Trading System v7.0"""

import sys, os, importlib
from pathlib import Path

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    C = True
except ImportError:
    C = False
    class Fore:
        RED=GREEN=YELLOW=CYAN=""
    class Style:
        BRIGHT=RESET_ALL=""

REQUIRED_FILES = [
    "app.py","config.py","database.py","risk_manager.py",
    "enhanced_strategy.py","telegram_notifier.py",
    "backtester.py","portfolio.py","exceptions.py",
    "requirements.txt",".gitignore",".env"
]
REQUIRED_TEMPLATES = [
    "templates/dashboard.html","templates/analysis.html",
    "templates/backtesting.html","templates/options.html"
]
REQUIRED_DIRS = ["templates","static","static/css","static/js"]
REQUIRED_PACKAGES = [
    "flask","flask_cors","yfinance","pandas","numpy",
    "requests","telebot","apscheduler","pytz","dotenv","ta","psycopg2"
]

ok  = lambda m: print(f"{Fore.GREEN}  OK  {m}")
err = lambda m: print(f"{Fore.RED}  FAIL {m}")
wrn = lambda m: print(f"{Fore.YELLOW}  WARN {m}")
hdr = lambda m: print(f"\n{Fore.CYAN}{'='*55}\n  {m}\n{'='*55}")

def main():
    all_pass = True

    hdr("FILES")
    for f in REQUIRED_FILES + REQUIRED_TEMPLATES:
        if Path(f).exists():
            ok(f)
        else:
            err(f"{f}  [MISSING]")
            all_pass = False

    hdr("DIRECTORIES")
    for d in REQUIRED_DIRS:
        if Path(d).is_dir():
            ok(d)
        else:
            err(f"{d}/  [MISSING]")
            all_pass = False

    hdr("PACKAGES")
    for pkg in REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            ok(pkg)
        except ImportError:
            err(f"{pkg}  [NOT INSTALLED]")
            all_pass = False

    hdr("ENVIRONMENT")
    if Path(".env").exists():
        from dotenv import load_dotenv
        load_dotenv()
        for var in ["SECRET_KEY","WEBHOOK_SECRET","TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID","DATABASE_URL"]:
            val = os.getenv(var)
            if val:
                ok(f"{var} = {val[:6]}***")
            else:
                if var == "DATABASE_URL":
                    err(f"{var}  [NOT SET - data will NOT survive a Render restart/redeploy]")
                else:
                    err(f"{var}  [NOT SET]")
                all_pass = False
    else:
        err(".env file missing")
        all_pass = False

    hdr("RESULT")
    if all_pass:
        print(f"{Fore.GREEN}  ALL CHECKS PASSED - run: python app.py")
    else:
        print(f"{Fore.RED}  SOME CHECKS FAILED - fix issues above first")
    return 0 if all_pass else 1

if __name__ == "__main__":
    sys.exit(main())
