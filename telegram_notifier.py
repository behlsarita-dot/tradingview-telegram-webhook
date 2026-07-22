#!/usr/bin/env python3
"""
Telegram Notifier - Paper Trading System v7.0
Pure synchronous telebot implementation, dispatched via background threads
so that Telegram API latency never blocks the Flask webhook response.
"""

import logging
import threading
import requests
from datetime import datetime
from typing import Optional

from config import (
    TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID, TELEGRAM_TIMEOUT
)

logger = logging.getLogger(__name__)

_bot = None
_bot_ready = False
_bot_user = None


def _get_bot():
    global _bot, _bot_ready, _bot_user
    if _bot_ready:
        return _bot
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured")
        return None
    try:
        import telebot
        telebot.apihelper.CONNECT_TIMEOUT = 5
        telebot.apihelper.READ_TIMEOUT = TELEGRAM_TIMEOUT
        _bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
        _bot_user = _bot.get_me()
        _bot_ready = True
        logger.info(f"Telegram connected: @{_bot_user.username}")
    except Exception as e:
        logger.error(f"Telegram init failed: {e}")
        _bot = None
        _bot_ready = True
    return _bot


def _send_async(func, *args, **kwargs):
    """
    Run a notification function in a background thread so it never blocks
    the Flask request/response cycle (e.g. the /api/webhook route).
    Daemon=True ensures these threads never prevent process shutdown.
    """
    threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True).start()


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """
    Synchronous send. Used internally by the _sync notify functions (which
    run on background threads) and by test_connection() (which is a manual
    diagnostic call, not part of the webhook request path, so blocking here
    is fine and preferable).
    """
    bot = _get_bot()
    if not bot:
        return False
    try:
        bot.send_message(TELEGRAM_CHAT_ID, text,
                         parse_mode=parse_mode,
                         disable_web_page_preview=True)
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return _send_via_requests(text)


def _send_via_requests(text: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=TELEGRAM_TIMEOUT)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram fallback failed: {e}")
        return False


def send_document(file_bytes: bytes, filename: str, caption: str = "") -> bool:
    """
    Send an in-memory file (bytes) to Telegram as a document attachment.
    No disk write required - safe to call even on ephemeral storage.

    Synchronous by design: intended for scheduled jobs (e.g. the daily
    snapshot job in app.py), not the webhook request path, so blocking
    here is fine and lets the caller know immediately if it failed.
    """
    bot = _get_bot()
    if not bot:
        return _send_document_via_requests(file_bytes, filename, caption)
    try:
        import io
        bot.send_document(
            TELEGRAM_CHAT_ID,
            (filename, io.BytesIO(file_bytes)),
            caption=caption
        )
        return True
    except Exception as e:
        logger.error(f"Telegram send_document failed: {e}")
        return _send_document_via_requests(file_bytes, filename, caption)


def _send_document_via_requests(file_bytes: bytes, filename: str, caption: str = "") -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"document": (filename, file_bytes)},
            timeout=TELEGRAM_TIMEOUT
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send_document fallback failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Public notify_* functions — these are the ones called from app.py.
# Each one only dispatches to a background thread and returns immediately.
# The actual message-building + send_message() logic lives in the paired
# _sync function below it.
# ---------------------------------------------------------------------------

def notify_trade_open(symbol: str, action: str, entry: float,
                      qty: int, sl: float = None, tp: float = None,
                      pos_id: str = None, charges: float = 0.0):
    _send_async(_notify_trade_open_sync, symbol, action, entry, qty, sl, tp, pos_id, charges)


def _notify_trade_open_sync(symbol: str, action: str, entry: float,
                            qty: int, sl: float = None, tp: float = None,
                            pos_id: str = None, charges: float = 0.0):
    sl_str = f"Rs.{sl:,.2f}" if sl else "Not set"
    tp_str = f"Rs.{tp:,.2f}" if tp else "Not set"
    # FIX (2026-07-22): was an exact match against ("BUY", "LONG"), which
    # only ever matched the equity path (_handle_open sends plain "BUY"/
    # "SELL"). The options path (_handle_open_option) sends "BUY CE" or
    # "BUY PE" - action.upper() == "BUY CE" fails the exact-match check
    # and fell through to the SHORT label, mislabeling every option BUY
    # (CE or PE) as a short position in Telegram. Confirmed live on
    # NIFTY260728C24300, 2026-07-22 13:40 IST: action="BUY CE", Telegram
    # showed "POSITION OPENED (SHORT)" for what was actually a long call.
    # Now uses a prefix check so "BUY", "BUY CE", "BUY PE", and "LONG"
    # all correctly resolve to LONG.
    icon = "POSITION OPENED (LONG)" if action.upper().startswith(("BUY", "LONG")) else "POSITION OPENED (SHORT)"
    msg = (
        f"*{icon}*\n"
        f"---------------------\n"
        f"Symbol:  `{symbol}`\n"
        f"Action:  `{action.upper()}`\n"
        f"Entry:   `Rs.{entry:,.2f}`\n"
        f"Qty:     `{qty}` units\n"
        f"SL:      `{sl_str}`\n"
        f"TP:      `{tp_str}`\n"
        f"Charges: `Rs.{charges:.2f}`\n"
        f"Time:    `{_now()}`"
    )
    send_message(msg)


def notify_trade_close(symbol: str, action: str, entry: float,
                       exit_price: float, qty: int, pnl: float,
                       reason: str, charges: float = 0.0):
    _send_async(_notify_trade_close_sync, symbol, action, entry, exit_price, qty, pnl, reason, charges)


def _notify_trade_close_sync(symbol: str, action: str, entry: float,
                             exit_price: float, qty: int, pnl: float,
                             reason: str, charges: float = 0.0):
    net_pnl = pnl - charges
    icon = "POSITION CLOSED - PROFIT" if net_pnl >= 0 else "POSITION CLOSED - LOSS"
    sign = "+" if net_pnl >= 0 else ""
    msg = (
        f"*{icon}*\n"
        f"---------------------\n"
        f"Symbol:  `{symbol}`\n"
        f"Action:  `{action.upper()}`\n"
        f"Entry:   `Rs.{entry:,.2f}`\n"
        f"Exit:    `Rs.{exit_price:,.2f}`\n"
        f"Qty:     `{qty}` units\n"
        f"P&L:     `{sign}Rs.{net_pnl:,.2f}`\n"
        f"Charges: `Rs.{charges:.2f}`\n"
        f"Reason:  `{reason}`\n"
        f"Time:    `{_now()}`"
    )
    send_message(msg)


def notify_circuit_breaker(reason: str):
    _send_async(_notify_circuit_breaker_sync, reason)


def _notify_circuit_breaker_sync(reason: str):
    msg = (
        f"*CIRCUIT BREAKER TRIGGERED*\n"
        f"---------------------\n"
        f"Reason: `{reason}`\n"
        f"Trading HALTED\n"
        f"Time: `{_now()}`"
    )
    send_message(msg)


def notify_daily_summary(capital: float, daily_pnl: float,
                          trades: int, wins: int, losses: int):
    _send_async(_notify_daily_summary_sync, capital, daily_pnl, trades, wins, losses)


def _notify_daily_summary_sync(capital: float, daily_pnl: float,
                               trades: int, wins: int, losses: int):
    sign = "+" if daily_pnl >= 0 else ""
    rate = round(wins / trades * 100, 1) if trades > 0 else 0
    msg = (
        f"*DAILY SUMMARY*\n"
        f"---------------------\n"
        f"Capital:  `Rs.{capital:,.2f}`\n"
        f"Day P&L:  `{sign}Rs.{daily_pnl:,.2f}`\n"
        f"Trades:   `{trades}` (W:{wins} L:{losses})\n"
        f"Win Rate: `{rate}%`\n"
        f"Date:     `{datetime.now().strftime('%d-%b-%Y')}`"
    )
    send_message(msg)


def notify_startup(capital: float, mode: str = "PAPER"):
    _send_async(_notify_startup_sync, capital, mode)


def _notify_startup_sync(capital: float, mode: str = "PAPER"):
    msg = (
        f"*TRADING BOT STARTED*\n"
        f"---------------------\n"
        f"Mode:    `{mode}`\n"
        f"Capital: `Rs.{capital:,.2f}`\n"
        f"Time:    `{_now()}`\n"
        f"System ready"
    )
    send_message(msg)


# ---------------------------------------------------------------------------
# Diagnostic functions — intentionally left SYNCHRONOUS.
# These are called manually (not from the webhook request path), so blocking
# here is fine and actually desirable: you want the real result immediately.
# ---------------------------------------------------------------------------

def test_connection() -> dict:
    bot = _get_bot()
    if not bot:
        return {"success": False, "message": "Telegram not configured or init failed"}
    try:
        info = bot.get_me()
        send_message("Test message from Paper Trading Bot v7.0")
        return {
            "success": True,
            "message": "Test message sent",
            "bot_username": f"@{info.username}",
            "chat_id": TELEGRAM_CHAT_ID
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_status() -> dict:
    return {
        "enabled": TELEGRAM_ENABLED,
        "bot_token_configured": bool(TELEGRAM_BOT_TOKEN),
        "chat_id_configured": bool(TELEGRAM_CHAT_ID),
        "bot_ready": _bot_ready,
        "bot_available": _bot is not None,
        "bot_username": f"@{_bot_user.username}" if _bot_user else None
    }


def _now() -> str:
    return datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    