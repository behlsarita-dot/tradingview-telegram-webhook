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
                          exit_reason: str, mode: str = "PAPER") -> Optional[Dict]:
        """
        Close a position. Works for both equity and options.
        Options use 'premium' as entry price, equity uses 'entry_price'.

        Returns None if the position was already CLOSED by the time the
        DB write ran — i.e. this call lost a race against another
        worker/retry closing the same position first (see
        DatabaseManager.close_position()/close_option_position(), which
        only flip status when it's still 'OPEN' and report back via
        rowcount whether they actually did it).

        NEW (2026-07-20): this check exists specifically so that a
        duplicate EXIT/EXIT_OPTION webhook (recovered-but-not-actually-
        dead row, or a genuine retry) can never double-apply P&L to
        current_capital. Previously this method assumed its own
        db.close_position() call always succeeded and unconditionally
        adjusted the account afterward — safe when there was only ever
        one attempt, not safe once recover_stuck_webhooks() and multiple
        gunicorn workers made a second attempt possible. Callers
        (app.py: _handle_exit, _handle_exit_option, close_all_positions_eod,
        api_close_position) must check for None and skip
        notify_trade_close() / treat it as "already handled", not as an
        error.
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

        # Close in DB — this is the actual race guard. status='OPEN' is
        # enforced in the WHERE clause at the DB layer; the bool return
        # tells us whether THIS call is the one that won the race.
        if is_option:
            closed = self.db.close_option_position(
                position["id"], exit_price, exit_reason, gross, exit_ch
            )
        else:
            closed = self.db.close_position(
                position["id"], exit_price, exit_reason, gross, exit_ch
            )

        if not closed:
            logger.warning(
                f"apply_trade_close: position {position['id']} was already "
                f"CLOSED — skipping duplicate account update"
            )
            return None

        # Only reaches here if this call actually performed the close,
        # so it's safe to apply net P&L to current_capital exactly once.
        #
        # FIX (2026-07-28): previously this did get_account() -> mutate in
        # Python -> update_account(), which is a read-then-write across
        # two separate connections/transactions. Two positions closing
        # near-simultaneously (webhook worker thread + EOD close looping
        # over several positions, etc.) could both read the same starting
        # current_capital before either wrote back, silently losing one
        # trade's P&L. apply_capital_delta() does the increment as a
        # single atomic UPDATE, so this race is closed regardless of how
        # many callers hit it concurrently.
        self.db.apply_capital_delta(mode, round(net, 2))

        return {
            "gross_pnl": gross,
            "net_pnl": net,
            "entry_charges": entry_ch,
            "exit_charges": exit_ch,
            "total_charges": entry_ch + exit_ch,
            "exit_reason": exit_reason,
        }
    