#!/usr/bin/env python3
"""
Portfolio Manager - Paper Trading System v7.0
Handles brokerage charges, P&L calculations, and capital updates.
"""

import logging
from typing import Dict, Optional, Tuple

from config import LOT_SIZE, INITIAL_CAPITAL

logger = logging.getLogger(__name__)

FLAT_BROKERAGE = 20.0
STT_SELL_PCT = 0.0625
EXCHANGE_TXN_PCT = 0.053
GST_PCT = 18.0
SEBI_CHARGE = 10.0 / 1e7
STAMP_DUTY_PCT = 0.003


def calculate_option_charges(premium: float, quantity: int,
                              side: str = "BUY") -> float:
    turnover = premium * quantity
    brokerage = FLAT_BROKERAGE
    stt = (STT_SELL_PCT / 100) * turnover if side.upper() == "SELL" else 0.0
    exchange = (EXCHANGE_TXN_PCT / 100) * turnover
    gst = (GST_PCT / 100) * (brokerage + exchange)
    sebi = SEBI_CHARGE * turnover
    stamp = (STAMP_DUTY_PCT / 100) * turnover if side.upper() == "BUY" else 0.0
    total = brokerage + stt + exchange + gst + sebi + stamp
    return round(total, 2)


def calculate_equity_charges(price: float, quantity: int,
                              side: str = "BUY") -> float:
    turnover = price * quantity
    brokerage = min(20.0, 0.03 / 100 * turnover)
    stt = (0.025 / 100) * turnover
    exchange = (0.00345 / 100) * turnover
    gst = (18 / 100) * (brokerage + exchange)
    sebi = SEBI_CHARGE * turnover
    stamp = (0.003 / 100) * turnover if side.upper() == "BUY" else 0.0
    total = brokerage + stt + exchange + gst + sebi + stamp
    return round(total, 2)


def calculate_pnl(entry: float, exit_price: float,
                  quantity: int, action: str) -> float:
    if action.upper() in ("BUY", "LONG"):
        return round((exit_price - entry) * quantity, 2)
    else:
        return round((entry - exit_price) * quantity, 2)


def calculate_net_pnl(entry: float, exit_price: float,
                      quantity: int, action: str,
                      entry_charges: float = 0.0,
                      exit_charges: float = 0.0) -> Tuple[float, float]:
    gross = calculate_pnl(entry, exit_price, quantity, action)
    net = round(gross - entry_charges - exit_charges, 2)
    return gross, net


class PortfolioManager:
    def __init__(self, db):
        self.db = db

    def get_summary(self, mode: str = "PAPER") -> Dict:
        account = self.db.get_account(mode)
        if not account:
            return {}

        open_pos = self.db.get_open_positions(mode)
        opt_pos = self.db.get_open_option_positions(mode)
        stats = self.db.get_trade_stats(mode)
        daily_pnl = self.db.get_daily_pnl(mode)

        locked_equity = sum(
            p["entry_price"] * p["quantity"] + p.get("entry_charges", 0)
            for p in open_pos
        )
        locked_options = sum(
            p["premium"] * p["quantity"]
            for p in opt_pos
        )
        total_locked = locked_equity + locked_options
        unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in open_pos)

        capital = account["current_capital"]
        initial = account["initial_capital"]
        total_pnl = account["total_pnl"]
        roi_pct = round((total_pnl / initial) * 100, 2) if initial > 0 else 0.0

        return {
            "mode": mode,
            "initial_capital": round(initial, 2),
            "current_capital": round(capital, 2),
            "available_capital": round(capital - total_locked, 2),
            "locked_in_positions": round(total_locked, 2),
            "total_pnl": round(total_pnl, 2),
            "roi_pct": roi_pct,
            "daily_pnl": round(daily_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "open_positions": len(open_pos) + len(opt_pos),
            "total_trades": stats.get("total_trades", 0),
            "win_rate": stats.get("win_rate", 0.0),
            "profit_factor": stats.get("profit_factor", 0.0),
            "best_trade": stats.get("best_trade", 0.0),
            "worst_trade": stats.get("worst_trade", 0.0),
            "total_charges": stats.get("total_charges", 0.0),
        }

    def apply_trade_close(self, position: Dict, exit_price: float,
                          exit_reason: str, mode: str = "PAPER") -> Dict:
        """
        Close a position. Works for both equity and options.
        Options use 'premium' as entry price, equity uses 'entry_price'.
        """
        is_option = "option_symbol" in position

        # Get correct entry price field
        if is_option:
            entry = position.get("premium", 0.0)
            action = "BUY"  # options are always bought long
        else:
            entry = position.get("entry_price", 0.0)
            action = position.get("action", "BUY")

        qty = position["quantity"]
        entry_ch = position.get("entry_charges", 0.0)

        # Calculate exit charges
        if is_option:
            exit_ch = calculate_option_charges(exit_price, qty, "SELL")
        else:
            exit_ch = calculate_equity_charges(exit_price, qty, "SELL")

        gross, net = calculate_net_pnl(entry, exit_price, qty, action,
                                       entry_ch, exit_ch)

        # Close in DB
        if is_option:
            self.db.close_option_position(
                position["id"], exit_price, exit_reason, gross, exit_ch
            )
        else:
            self.db.close_position(
                position["id"], exit_price, exit_reason, gross, exit_ch
            )

        # Update account capital
        account = self.db.get_account(mode)
        new_cap = account["current_capital"] + net
        new_pnl = account["total_pnl"] + net
        new_wins = account["winning_trades"] + (1 if net >= 0 else 0)
        new_loss = account["losing_trades"] + (1 if net < 0 else 0)

        self.db.update_account(
            mode,
            current_capital=round(new_cap, 2),
            total_pnl=round(new_pnl, 2),
            winning_trades=new_wins,
            losing_trades=new_loss
        )

        return {
            "gross_pnl": gross,
            "net_pnl": net,
            "entry_charges": entry_ch,
            "exit_charges": exit_ch,
            "total_charges": entry_ch + exit_ch,
            "exit_reason": exit_reason,
        }
