#!/usr/bin/env python3
"""
Telegram Notifier - Paper Trading System v7.0
Pure synchronous telebot implementation. No async loops.
"""

import logging
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


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
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


def notify_trade_open(symbol: str, action: str, entry: float,
                      qty: int, sl: float = None, tp: float = None,
                      pos_id: str = None, charges: float = 0.0):
    sl_str = f"Rs.{sl:,.2f}" if sl else "Not set"
    tp_str = f"Rs.{tp:,.2f}" if tp else "Not set"
    icon = "POSITION OPENED (LONG)" if action.upper() in ("BUY", "LONG") else "POSITION OPENED (SHORT)"
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
    sign = "+" if daily_pnl >= 0 else ""
    rate = round(wins / trades * 100, 1) if trades > 0 else 0
    msg = (
        f"*DAILY SUMMARY*\n"
        f"---------------------\n"
        f"Capital:  `Rs.{capital:,.2f}`\n"
        f"Day P&L:  `{sign}Rs.{daily_pnl:,.2f}`\n"
        f"Trades:   `{trades}` (W:{wins} L:{losses})\n"
        f"Win Rate: `{rate}%`\n"
        f"Date:     `{datetime.now().strftime(chr(37)+chr(100)+chr(45)+chr(37)+chr(98)+chr(45)+chr(37)+chr(89))}`"
    )
    send_message(msg)


def notify_startup(capital: float, mode: str = "PAPER"):
    msg = (
        f"*TRADING BOT STARTED*\n"
        f"---------------------\n"
        f"Mode:    `{mode}`\n"
        f"Capital: `Rs.{capital:,.2f}`\n"
        f"Time:    `{_now()}`\n"
        f"System ready"
    )
    send_message(msg)


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
