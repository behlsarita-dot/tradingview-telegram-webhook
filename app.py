#!/usr/bin/env python3
"""
Paper Trading System v7.0 - Main Flask Application
Features:
- EOD auto-close at 15:20 IST (intraday enforcement)
- Block new entries after 15:00 IST
- Re-enable trading at 09:10 IST
- Kill switch (manual halt/resume)
- Emergency close all positions
- Per-position close via dashboard
"""

import os
import logging
import time
from datetime import datetime

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

import pytz
from config import LOG_LEVEL, LOG_FILE, TRADING_MODE, INITIAL_CAPITAL, WEBHOOK_SECRET

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from config import (
    SECRET_KEY, FLASK_DEBUG, PORT, WEBHOOK_MAX_AGE_SECONDS,
    LOT_SIZE, ENABLE_RISK_MANAGEMENT, TELEGRAM_ENABLED, init_redis
)
from exceptions import (
    handle_exception, InvalidWebhookSecretError,
    PositionNotFoundError, ValidationError, InvalidActionError
)
from database import DatabaseManager
from risk_manager import create_risk_manager
from portfolio import (
    PortfolioManager, calculate_option_charges,
    calculate_equity_charges, calculate_pnl
)
from telegram_notifier import (
    notify_trade_open, notify_trade_close,
    notify_circuit_breaker, notify_startup,
    test_connection, get_status as tg_status, send_message
)

try:
    from enhanced_strategy import create_strategy_engine
    strategy_engine = create_strategy_engine()
    STRATEGY_AVAILABLE = True
except Exception as e:
    logger.warning(f"Enhanced strategy not available: {e}")
    strategy_engine = None
    STRATEGY_AVAILABLE = False

try:
    from backtester import create_backtester
    backtester = create_backtester()
    BACKTESTING_AVAILABLE = True
except Exception as e:
    logger.warning(f"Backtester not available: {e}")
    backtester = None
    BACKTESTING_AVAILABLE = False

app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app)

db = DatabaseManager()
risk_mgr = create_risk_manager(db, INITIAL_CAPITAL)
portfolio = PortfolioManager(db)
redis = init_redis()
START_TIME = time.time()

IST = pytz.timezone("Asia/Kolkata")

VALID_ACTIONS = {
    "BUY", "SELL", "LONG", "SHORT",
    "EXIT", "EXIT_LONG", "EXIT_SHORT",
    "BUY_OPTION", "EXIT_OPTION"
}

logger.info(f"Paper Trading System v7.0 starting - mode={TRADING_MODE}")


def ist_now() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def normalise_action(action: str) -> str:
    a = action.upper().strip()
    if a == "LONG":
        return "BUY"
    if a == "SHORT":
        return "SELL"
    return a


def verify_webhook_secret(payload: dict):
    secret = payload.get("webhook_secret", "")
    if secret != WEBHOOK_SECRET:
        raise InvalidWebhookSecretError()


def rate_limit(key: str, limit: int = 30, window: int = 60) -> bool:
    rkey = f"rl:{key}"
    try:
        count = redis.incr(rkey)
        if count == 1:
            redis.expire(rkey, window)
        return count <= limit
    except Exception:
        return True


# ── Price Fetcher ──────────────────────────────────────────────────────────

def _get_nifty_price() -> tuple:
    """
    Get current NIFTY price.
    Returns (price, is_live) — is_live=False means price is estimated.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker("^NSEI")
        data = ticker.history(period="1d", interval="1m")
        if not data.empty:
            price = round(float(data["Close"].iloc[-1]), 2)
            return price, True
    except Exception as e:
        logger.warning(f"yfinance NIFTY fetch failed: {e}")

    # Fallback 1: use last known price from open positions
    try:
        positions = db.get_open_positions(TRADING_MODE)
        if positions:
            price = float(positions[0].get("current_price") or positions[0]["entry_price"])
            logger.warning(f"Using entry price as fallback: {price}")
            return price, False
    except Exception:
        pass

    # Fallback 2: hardcoded — flagged clearly
    logger.warning("Using hardcoded NIFTY fallback price 24400 — P&L will be approximate")
    return 24400.0, False


def _get_option_price(pos: dict) -> tuple:
    """
    Get current option price.
    Returns (price, is_live).
    """
    try:
        import yfinance as yf
        symbol = pos.get("option_symbol", "")
        if symbol:
            ticker = yf.Ticker(f"{symbol}.NS")
            data = ticker.history(period="1d", interval="1m")
            if not data.empty:
                price = round(float(data["Close"].iloc[-1]), 2)
                return price, True
    except Exception as e:
        logger.warning(f"yfinance option fetch failed: {e}")

    # Fallback: entry premium (flat exit)
    entry_premium = pos.get("premium", 0)
    logger.warning(f"Using entry premium as option fallback: {entry_premium}")
    return entry_premium, False


# ── EOD Auto-Close ─────────────────────────────────────────────────────────

def close_all_positions_eod(reason: str = "EOD Auto-Close 15:20"):
    """
    Close all open positions.
    Used by scheduler at 15:20 and by emergency-close route.
    Flags estimated prices clearly in exit_reason.
    """
    logger.info(f"Close all triggered: {reason}")

    try:
        close_price, price_live = _get_nifty_price()
        price_flag = "" if price_live else " [ESTIMATED PRICE]"

        open_positions = db.get_open_positions(TRADING_MODE)
        open_options   = db.get_open_option_positions(TRADING_MODE)
        total          = len(open_positions) + len(open_options)

        if total == 0:
            logger.info("Close all: no open positions to close")
            send_message(f"*{reason}*\nNo open positions found.")
            return 0

        send_message(
            f"*{reason}*\n"
            f"Closing {total} position(s)\n"
            f"NIFTY: Rs.{close_price:,.2f}{price_flag}\n"
            f"Time: `{ist_now()}`"
        )

        closed = 0

        # Close equity/futures positions
        for pos in open_positions:
            try:
                exit_reason = f"{reason}{price_flag}"
                result = portfolio.apply_trade_close(
                    pos, close_price, exit_reason, TRADING_MODE
                )
                notify_trade_close(
                    pos["symbol"], pos["action"],
                    pos["entry_price"], close_price,
                    pos["quantity"], result["gross_pnl"],
                    exit_reason, result["total_charges"]
                )
                logger.info(
                    f"Closed: {pos['action']} {pos['symbol']} "
                    f"@ Rs.{close_price:,.2f} pnl=Rs.{result['net_pnl']:,.2f}{price_flag}"
                )
                closed += 1
            except Exception as e:
                logger.error(f"Close failed for position {pos['id']}: {e}")

        # Close option positions
        for pos in open_options:
            try:
                opt_price, opt_live = _get_option_price(pos)
                opt_flag = "" if opt_live else " [ESTIMATED PRICE]"
                exit_reason = f"{reason}{opt_flag}"

                result = portfolio.apply_trade_close(
                    pos, opt_price, exit_reason, TRADING_MODE
                )
                notify_trade_close(
                    pos["option_symbol"], "SELL",
                    pos["premium"], opt_price,
                    pos["quantity"], result["gross_pnl"],
                    exit_reason, result["total_charges"]
                )
                logger.info(
                    f"Closed option: {pos['option_symbol']} "
                    f"@ Rs.{opt_price:,.2f} pnl=Rs.{result['net_pnl']:,.2f}{opt_flag}"
                )
                closed += 1
            except Exception as e:
                logger.error(f"Close failed for option {pos['id']}: {e}")

        # Summary
        account   = db.get_account(TRADING_MODE)
        daily_pnl = db.get_daily_pnl(TRADING_MODE)
        send_message(
            f"*CLOSE COMPLETE*\n"
            f"Closed: {closed}/{total} position(s)\n"
            f"Capital: Rs.{account['current_capital']:,.2f}\n"
            f"Today P&L: Rs.{daily_pnl:+,.2f}\n"
            f"{price_flag if not price_live else ''}"
        )
        return closed

    except Exception as e:
        logger.error(f"Close all error: {e}")
        send_message(f"*CLOSE ALL ERROR*\n{str(e)}")
        return 0


# ── Scheduler ─────────────────────────────────────────────────────────────

def _eod_scheduler_job():
    """Scheduled EOD close — skips if kill switch was manually set by user."""
    close_all_positions_eod("EOD Auto-Close 15:20")


def _block_entries_job():
    """Block new entries at 15:15 — only if trading was enabled (don't overwrite manual halt)."""
    if db.is_trading_enabled(TRADING_MODE):
        db.set_trading_enabled(False, TRADING_MODE, "Market closed 15:15")
        logger.info("Scheduler: new entries blocked at 15:15")
        send_message("*Market Closed*\nNew entries blocked (15:15 IST)")


def _enable_trading_job():
    """Re-enable trading at 09:10 AM — fresh day."""
    db.set_trading_enabled(True, TRADING_MODE, "")
    logger.info("Scheduler: trading enabled at 09:10")
    send_message("*Market Pre-Open*\nTrading enabled (09:10 IST)")


def start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone=IST)

        scheduler.add_job(
            _eod_scheduler_job,
            trigger="cron", hour=15, minute=20,
            day_of_week="mon-fri", id="eod_close", replace_existing=True
        )
        scheduler.add_job(
            _block_entries_job,
            trigger="cron", hour=15, minute=15,
            day_of_week="mon-fri", id="block_entries", replace_existing=True
        )
        scheduler.add_job(
            _enable_trading_job,
            trigger="cron", hour=9, minute=10,
            day_of_week="mon-fri", id="enable_trading", replace_existing=True
        )

        scheduler.start()
        logger.info("Scheduler started: enable 09:10 | block 15:15 | EOD close 15:20 IST")
        return scheduler

    except Exception as e:
        logger.error(f"Scheduler failed to start: {e}")
        return None


scheduler = start_scheduler()


# ── Pages ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/analysis")
def analysis():
    return render_template("analysis.html")

@app.route("/backtesting")
def backtesting():
    return render_template("backtesting.html")

@app.route("/options")
def options():
    return render_template("options.html")


# ── Health ─────────────────────────────────────────────────────────────────

@app.route("/health")
@app.route("/api/health")
def health():
    uptime = int(time.time() - START_TIME)
    try:
        db.get_account(TRADING_MODE)
        db_status = "connected"
    except Exception:
        db_status = "error"
    return jsonify({
        "status":          "healthy",
        "version":         "7.0",
        "mode":            TRADING_MODE,
        "timestamp":       ist_now(),
        "database":        db_status,
        "telegram":        "enabled" if TELEGRAM_ENABLED else "disabled",
        "risk_manager":    "active" if ENABLE_RISK_MANAGEMENT else "disabled",
        "strategy":        "active" if STRATEGY_AVAILABLE else "fallback",
        "backtesting":     "active" if BACKTESTING_AVAILABLE else "unavailable",
        "uptime_seconds":  uptime,
        "lot_size":        LOT_SIZE,
        "trading_enabled": db.is_trading_enabled(TRADING_MODE),
        "scheduler":       "active" if scheduler else "inactive",
    })


# ── Portfolio ──────────────────────────────────────────────────────────────

@app.route("/api/portfolio")
@app.route("/api/account")
def api_portfolio():
    try:
        summary = portfolio.get_summary(TRADING_MODE)
        return jsonify({"success": True, **summary})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


@app.route("/api/equity_curve")
def api_equity_curve():
    try:
        positions = db.get_all_positions(TRADING_MODE, status="CLOSED", limit=500)
        account   = db.get_account(TRADING_MODE)
        capital   = INITIAL_CAPITAL
        curve     = [{"timestamp": "Start", "capital": capital}]
        for p in reversed(positions):
            if p.get("pnl") is not None:
                capital += p["pnl"] - p.get("total_charges", 0)
                curve.append({"timestamp": p.get("exit_time", ""), "capital": round(capital, 2)})
        return jsonify({
            "success":         True,
            "data":            curve,
            "initial_capital": INITIAL_CAPITAL,
            "current_capital": account["current_capital"] if account else INITIAL_CAPITAL
        })
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── Positions ──────────────────────────────────────────────────────────────

@app.route("/api/positions")
def api_positions():
    try:
        status    = request.args.get("status")
        limit     = int(request.args.get("limit", 50))
        symbol    = request.args.get("symbol")
        positions = db.get_all_positions(TRADING_MODE, status=status, limit=limit)
        if symbol:
            positions = [p for p in positions if p["symbol"] == symbol.upper()]
        stats = db.get_trade_stats(TRADING_MODE)
        return jsonify({"success": True, "positions": positions, "total": len(positions), "stats": stats})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


@app.route("/api/options/positions")
def api_option_positions():
    try:
        positions = db.get_open_option_positions(TRADING_MODE)
        return jsonify({"success": True, "positions": positions, "total": len(positions)})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── Kill Switch ────────────────────────────────────────────────────────────

@app.route("/api/kill-switch", methods=["GET", "POST"])
def kill_switch():
    if request.method == "GET":
        trading_enabled = db.is_trading_enabled(TRADING_MODE)
        account         = db.get_account(TRADING_MODE)
        reason          = account.get("kill_switch_reason", "") if account else ""
        return jsonify({
            "success":             True,
            "trading_enabled":     trading_enabled,
            "kill_switch_reason":  reason
        })
    try:
        payload = request.get_json(silent=True) or {}
        verify_webhook_secret(payload)
        enabled = bool(payload.get("enabled", True))
        reason  = payload.get("reason", "")
        db.set_trading_enabled(enabled, TRADING_MODE, reason)
        status_text = "RESUMED" if enabled else "HALTED"
        msg = f"Kill switch: Trading {status_text}"
        if reason:
            msg += f" - {reason}"
        send_message(f"*{msg}*\nTime: `{ist_now()}`")
        logger.info(msg)
        return jsonify({"success": True, "trading_enabled": enabled, "message": msg})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── Emergency Close All ────────────────────────────────────────────────────

@app.route("/api/emergency-close", methods=["POST"])
def api_emergency_close():
    """
    Manually close ALL open positions immediately.
    Requires webhook secret. Used by dashboard Close All button.
    Flags estimated prices in exit_reason.
    """
    try:
        payload = request.get_json(silent=True) or {}
        verify_webhook_secret(payload)
        reason = payload.get("reason", "Emergency Close - Manual")
        closed = close_all_positions_eod(reason)
        return jsonify({
            "success": True,
            "message": f"Closed {closed} position(s)",
            "closed":  closed
        })
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── EOD Manual Trigger ─────────────────────────────────────────────────────

@app.route("/api/eod-close", methods=["POST"])
def api_eod_close():
    """Manually trigger EOD close logic (requires webhook secret)."""
    try:
        payload = request.get_json(silent=True) or {}
        verify_webhook_secret(payload)
        closed = close_all_positions_eod("Manual EOD Close")
        return jsonify({"success": True, "message": f"EOD close triggered - {closed} position(s) closed"})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── Close Single Position ──────────────────────────────────────────────────

@app.route("/api/positions/<pos_id>/close", methods=["POST"])
def api_close_position(pos_id):
    """
    Close a single position by ID.
    Used by dashboard per-position close button.
    """
    try:
        payload = request.get_json(silent=True) or {}
        verify_webhook_secret(payload)

        position = db.get_position_by_id(pos_id)
        if not position:
            return jsonify({"success": False, "message": f"Position {pos_id} not found"}), 404
        if position.get("status") != "OPEN":
            return jsonify({"success": False, "message": "Position is not open"}), 400

        # Get price
        close_price, price_live = _get_nifty_price()
        price_flag  = "" if price_live else " [ESTIMATED PRICE]"
        exit_reason = f"Manual Close{price_flag}"

        result = portfolio.apply_trade_close(
            position, close_price, exit_reason, TRADING_MODE
        )
        notify_trade_close(
            position["symbol"], position["action"],
            position["entry_price"], close_price,
            position["quantity"], result["gross_pnl"],
            exit_reason, result["total_charges"]
        )
        logger.info(
            f"Manual close: {position['action']} {position['symbol']} "
            f"@ Rs.{close_price:,.2f} pnl=Rs.{result['net_pnl']:,.2f}{price_flag}"
        )
        return jsonify({
            "success":    True,
            "message":    "Position closed",
            "exit_price": close_price,
            "pnl":        result["gross_pnl"],
            "net_pnl":    result["net_pnl"],
            "charges":    result["total_charges"],
            "price_live": price_live,
        })
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── Webhook ────────────────────────────────────────────────────────────────

@app.route("/api/webhook", methods=["POST"])
def api_webhook():
    try:
        ip = request.remote_addr or "unknown"
        if not rate_limit(f"webhook:{ip}"):
            return jsonify({"success": False, "message": "Rate limit exceeded"}), 429

        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"success": False, "message": "Invalid JSON payload"}), 400

        verify_webhook_secret(payload)

        raw_action = payload.get("action", "").upper()
        if not raw_action:
            return jsonify({"success": False, "message": "action field required"}), 400

        action = normalise_action(raw_action)
        if action not in VALID_ACTIONS:
            raise InvalidActionError(raw_action, list(VALID_ACTIONS))

        symbol = payload.get("symbol", "NIFTY").upper()

        if action in ("BUY", "SELL"):
            return _handle_open(payload, action, symbol)
        elif action in ("EXIT", "EXIT_LONG", "EXIT_SHORT"):
            return _handle_exit(payload, symbol)
        elif action == "BUY_OPTION":
            return _handle_open_option(payload, symbol)
        elif action == "EXIT_OPTION":
            return _handle_exit_option(payload, symbol)

        return jsonify({"success": False, "message": f"Unhandled action: {action}"}), 400

    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


def _handle_open(payload: dict, action: str, symbol: str):
    entry = float(payload.get("price", 0))
    if entry <= 0:
        return jsonify({"success": False, "message": "Valid price required"}), 400

    # Block new entries at or after 15:00 IST
    now_ist = datetime.now(IST)
    if now_ist.hour >= 15:
        return jsonify({
            "success": False,
            "message": "New entries blocked after 15:00 IST (intraday only)"
        }), 400

    qty       = int(payload.get("quantity", LOT_SIZE))
    sl        = float(payload.get("sl", 0)) or None
    tp        = float(payload.get("tp", 0)) or None
    atr       = float(payload.get("atr", 0)) or None
    adx       = float(payload.get("adx", 0)) or None
    confluence = int(payload.get("confluence_score", 0))

    is_valid, details = risk_mgr.validate_new_trade(
        symbol, entry, sl or 0, tp or 0, TRADING_MODE
    )
    if not is_valid:
        if details.get("circuit_breaker"):
            notify_circuit_breaker(details["message"])
        return jsonify({"success": False, "message": details["message"], "details": details}), 400

    charges = calculate_equity_charges(entry, qty, "BUY")
    pos_id  = db.open_position(
        mode=TRADING_MODE, symbol=symbol, action=action,
        entry_price=entry, quantity=qty,
        stop_loss=sl, take_profit=tp,
        entry_charges=charges, atr=atr, adx=adx,
        confluence_score=confluence
    )

    account = db.get_account(TRADING_MODE)
    db.update_account(TRADING_MODE, current_capital=round(account["current_capital"] - charges, 2))

    notify_trade_open(symbol, action, entry, qty, sl, tp, pos_id, charges)
    logger.info(f"Position opened: {action} {symbol} @ Rs.{entry} qty={qty}")

    return jsonify({
        "success":     True,
        "message":     "Position opened",
        "position_id": pos_id,
        "symbol":      symbol,
        "action":      action,
        "entry_price": entry,
        "quantity":    qty,
        "charges":     charges,
    })


def _handle_exit(payload: dict, symbol: str):
    exit_price = float(payload.get("price", 0))
    if exit_price <= 0:
        return jsonify({"success": False, "message": "Valid exit price required"}), 400

    position = db.get_open_position_by_symbol(symbol, TRADING_MODE)
    if not position:
        raise PositionNotFoundError(symbol=symbol)

    reason = payload.get("exit_reason", "Signal Exit")
    result = portfolio.apply_trade_close(position, exit_price, reason, TRADING_MODE)

    notify_trade_close(
        symbol, position["action"],
        position["entry_price"], exit_price,
        position["quantity"], result["gross_pnl"],
        reason, result["total_charges"]
    )
    logger.info(f"Position closed: {symbol} @ Rs.{exit_price} pnl=Rs.{result['net_pnl']}")

    return jsonify({
        "success":     True,
        "message":     "Position closed",
        "position_id": position["id"],
        "exit_price":  exit_price,
        "pnl":         result["gross_pnl"],
        "net_pnl":     result["net_pnl"],
        "charges":     result["total_charges"],
        "exit_reason": reason,
    })


def _handle_open_option(payload: dict, symbol: str):
    option_symbol = payload.get("option_symbol", "")
    option_type   = payload.get("option_type", "CE").upper()
    strike        = float(payload.get("strike", 0))
    expiry        = payload.get("expiry", "")
    premium       = float(payload.get("premium", 0))
    qty           = int(payload.get("quantity", LOT_SIZE))
    sl            = float(payload.get("sl", 0)) or None
    tp            = float(payload.get("tp", 0)) or None

    if not option_symbol or premium <= 0:
        return jsonify({"success": False, "message": "option_symbol and premium required"}), 400

    charges = calculate_option_charges(premium, qty, "BUY")
    pos_id  = db.open_option_position(
        mode=TRADING_MODE, underlying=symbol,
        option_symbol=option_symbol, option_type=option_type,
        strike=strike, expiry=expiry, premium=premium,
        quantity=qty, stop_loss=sl, take_profit=tp,
        entry_charges=charges
    )

    account = db.get_account(TRADING_MODE)
    cost    = premium * qty + charges
    db.update_account(TRADING_MODE, current_capital=round(account["current_capital"] - cost, 2))

    notify_trade_open(option_symbol, f"BUY {option_type}", premium, qty, sl, tp, pos_id, charges)
    logger.info(f"Option opened: {option_symbol} @ Rs.{premium} qty={qty}")

    return jsonify({
        "success":       True,
        "message":       "Option position opened",
        "position_id":   pos_id,
        "option_symbol": option_symbol,
        "premium":       premium,
        "quantity":      qty,
        "charges":       charges,
    })


def _handle_exit_option(payload: dict, symbol: str):
    option_symbol = payload.get("option_symbol", "")
    exit_premium  = float(payload.get("premium", 0))

    if not option_symbol:
        return jsonify({"success": False, "message": "option_symbol required for EXIT_OPTION"}), 400

    position = db.get_open_option_by_symbol(option_symbol, TRADING_MODE)
    if not position:
        raise PositionNotFoundError(symbol=option_symbol)

    reason = payload.get("exit_reason", "Signal Exit")
    result = portfolio.apply_trade_close(position, exit_premium, reason, TRADING_MODE)

    notify_trade_close(
        option_symbol, "SELL",
        position["premium"], exit_premium,
        position["quantity"], result["gross_pnl"],
        reason, result["total_charges"]
    )

    return jsonify({
        "success":       True,
        "message":       "Option position closed",
        "position_id":   position["id"],
        "exit_premium":  exit_premium,
        "pnl":           result["gross_pnl"],
        "net_pnl":       result["net_pnl"],
        "charges":       result["total_charges"],
    })


# ── Analysis ───────────────────────────────────────────────────────────────

@app.route("/api/analysis/quick")
def api_analysis_quick():
    symbol = request.args.get("symbol", "NIFTY").upper()
    try:
        if strategy_engine:
            result = strategy_engine.analyze(symbol)
        else:
            from enhanced_strategy import quick_analysis
            result = quick_analysis(symbol)
        return jsonify({"success": True, **result})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


@app.route("/api/analyze/<symbol>")
def api_analyze_symbol(symbol):
    try:
        from enhanced_strategy import quick_analysis
        result = quick_analysis(symbol.upper())
        return jsonify({"success": True, **result})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


@app.route("/api/market/status")
def api_market_status():
    now_ist = datetime.now(IST)
    hour    = now_ist.hour
    minute  = now_ist.minute
    weekday = now_ist.weekday()
    if weekday >= 5:
        status = "CLOSED"
    elif (hour == 9 and minute >= 15) or (10 <= hour < 15) or (hour == 15 and minute <= 15):
        status = "OPEN"
    elif hour == 9 and minute < 15:
        status = "PRE_OPEN"
    else:
        status = "CLOSED"
    return jsonify({
        "status":           status,
        "current_time_ist": now_ist.strftime("%H:%M:%S"),
        "opens_at":         "09:15",
        "closes_at":        "15:15",
        "eod_auto_close":   "15:20",
        "timezone":         "Asia/Kolkata",
        "is_weekday":       weekday < 5,
    })


# ── Risk ───────────────────────────────────────────────────────────────────

@app.route("/api/risk/report")
def api_risk_report():
    try:
        report = risk_mgr.get_risk_report(TRADING_MODE)
        return jsonify({"success": True, "timestamp": ist_now(), **report})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


@app.route("/api/risk/validate", methods=["POST"])
def api_risk_validate():
    try:
        data      = request.get_json(silent=True) or {}
        symbol    = data.get("symbol", "NIFTY")
        entry     = float(data.get("price", 0))
        sl        = float(data.get("sl", 0))
        tp        = float(data.get("tp", 0))
        is_valid, details = risk_mgr.validate_new_trade(symbol, entry, sl, tp, TRADING_MODE)
        size      = risk_mgr.calculate_position_size(entry, sl, TRADING_MODE) if is_valid else 0
        return jsonify({"success": True, "accepted": is_valid, "details": details, "recommended_quantity": size})
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── Backtesting ────────────────────────────────────────────────────────────

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    if not BACKTESTING_AVAILABLE:
        return jsonify({"success": False, "error": "Backtesting not available"}), 503
    try:
        data   = request.get_json(silent=True) or {}
        result = backtester.run(
            symbol     = data.get("symbol", "NIFTY"),
            timeframe  = data.get("timeframe", "15m"),
            start_date = data.get("start_date", "2025-01-01"),
            end_date   = data.get("end_date", "2025-12-31"),
            parameters = data.get("parameters", {})
        )
        return jsonify(result)
    except Exception as e:
        resp, code = handle_exception(e)
        return jsonify(resp), code


# ── Telegram ───────────────────────────────────────────────────────────────

@app.route("/api/telegram/test")
def api_telegram_test():
    result = test_connection()
    code   = 200 if result["success"] else 503
    return jsonify(result), code


@app.route("/api/telegram/status")
def api_telegram_status():
    return jsonify(tg_status())


# ── System ─────────────────────────────────────────────────────────────────

@app.route("/api/system/info")
def api_system_info():
    import sys
    from flask import __version__ as flask_ver
    return jsonify({
        "version":                 "7.0",
        "trading_mode":            TRADING_MODE,
        "initial_capital":         INITIAL_CAPITAL,
        "lot_size":                LOT_SIZE,
        "risk_management_enabled": ENABLE_RISK_MANAGEMENT,
        "enhanced_strategy":       STRATEGY_AVAILABLE,
        "backtesting":             BACKTESTING_AVAILABLE,
        "python_version":          sys.version.split()[0],
        "flask_version":           flask_ver,
        "uptime_seconds":          int(time.time() - START_TIME),
        "trading_enabled":         db.is_trading_enabled(TRADING_MODE),
        "scheduler":               "active" if scheduler else "inactive",
        "eod_auto_close":          "15:20 IST weekdays",
    })


# ── Error handlers ─────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "message": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"success": False, "message": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({"success": False, "message": "Internal server error"}), 500


# ── Startup ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("PAPER TRADING SYSTEM v7.0")
    logger.info(f"Mode:    {TRADING_MODE}")
    logger.info(f"Capital: Rs.{INITIAL_CAPITAL:,.2f}")
    logger.info(f"Lot:     {LOT_SIZE} units")
    logger.info("Intraday: entries blocked after 15:00 IST")
    logger.info("EOD auto-close: 15:20 IST weekdays")
    logger.info("=" * 60)
    notify_startup(INITIAL_CAPITAL, TRADING_MODE)
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
