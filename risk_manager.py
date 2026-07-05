#!/usr/bin/env python3
"""
Risk Manager - Paper Trading System v7.0
"""

import logging
from typing import Dict, Tuple

from config import (
    ENABLE_RISK_MANAGEMENT, POSITION_SIZING_METHOD,
    RISK_PER_TRADE_PERCENT, MIN_RISK_REWARD_RATIO,
    MAX_PORTFOLIO_HEAT, MAX_DRAWDOWN_PCT,
    DRAWDOWN_REDUCTION_START, MAX_CONSECUTIVE_LOSSES,
    MAX_OPEN_POSITIONS, MAX_DAILY_LOSS, MAX_TRADES_PER_DAY,
    USE_TRAILING_STOPS, TRAILING_STOP_ACTIVATION,
    TRAILING_STOP_DISTANCE, KELLY_FRACTION, ATR_MULTIPLIER,
    INITIAL_CAPITAL, LOT_SIZE
)

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, db, initial_capital: float = INITIAL_CAPITAL):
        self.db = db
        self.initial_capital = initial_capital
        logger.info("Risk Manager initialised")

    def validate_new_trade(self, symbol: str, entry: float,
                           sl: float, tp: float,
                           mode: str = "PAPER") -> Tuple[bool, Dict]:
        if not ENABLE_RISK_MANAGEMENT:
            return True, {"message": "Risk management disabled"}

        account = self.db.get_account(mode)
        if not account:
            return False, {"message": "Account not found"}

        # Kill switch check — blocks all new entries
        if not self.db.is_trading_enabled(mode):
            reason = account.get("kill_switch_reason") or "Trading manually halted"
            return False, {"message": f"Kill switch active: {reason}", "circuit_breaker": True}

        capital = account["current_capital"]

        can, reason = self._check_circuit_breakers(account, mode)
        if not can:
            return False, {"message": reason, "circuit_breaker": True}

        # Combine equity/futures positions with option positions — an
        # options-only trader was previously invisible to both of these
        # checks, since only get_open_positions() (equity table) was
        # consulted. MAX_OPEN_POSITIONS could be silently exceeded, and
        # portfolio heat would read near-zero with several options open.
        open_pos = self.db.get_open_positions(mode)
        open_opts = self.db.get_open_option_positions(mode)
        all_open = open_pos + open_opts
        if len(all_open) >= MAX_OPEN_POSITIONS:
            return False, {"message": f"Max open positions reached ({MAX_OPEN_POSITIONS})"}

        trades_today = self.db.get_trades_today(mode)
        if trades_today >= MAX_TRADES_PER_DAY:
            return False, {"message": f"Max trades per day reached ({MAX_TRADES_PER_DAY})"}

        if sl and tp and entry:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                rr = reward / risk
                if rr < MIN_RISK_REWARD_RATIO:
                    return False, {
                        "message": f"R:R {rr:.2f} below minimum {MIN_RISK_REWARD_RATIO}",
                        "rr_ratio": round(rr, 2)
                    }

        heat = self._calculate_portfolio_heat(all_open, capital)
        if heat >= MAX_PORTFOLIO_HEAT:
            return False, {"message": f"Portfolio heat {heat:.1f}% at maximum {MAX_PORTFOLIO_HEAT}%"}

        return True, {
            "message": "Trade approved",
            "portfolio_heat": round(heat, 2),
            "trades_today": trades_today,
            "open_positions": len(all_open)
        }

    def _check_circuit_breakers(self, account: Dict, mode: str) -> Tuple[bool, str]:
        capital = account["current_capital"]
        drawdown = self._calculate_drawdown(capital, mode)

        if drawdown >= MAX_DRAWDOWN_PCT:
            return False, f"Max drawdown {drawdown:.1f}% exceeded ({MAX_DRAWDOWN_PCT}%)"

        daily_pnl = self.db.get_daily_pnl(mode)
        if daily_pnl <= -abs(MAX_DAILY_LOSS):
            return False, f"Daily loss limit Rs.{MAX_DAILY_LOSS:,.0f} reached"

        consec = self.db.get_consecutive_losses(mode)
        if consec >= MAX_CONSECUTIVE_LOSSES:
            return False, f"{consec} consecutive losses (limit {MAX_CONSECUTIVE_LOSSES})"

        return True, ""

    def _calculate_drawdown(self, current_capital: float, mode: str = "PAPER") -> float:
        """Drawdown vs. a peak-capital high-water mark persisted in the
        account table. Previously peak_capital lived only as an in-memory
        attribute on this instance, so every process restart (which happens
        often on Render with the current SQLite-in-/tmp setup) silently
        reset it back to INITIAL_CAPITAL — understating real drawdown and
        weakening the circuit breaker right when it mattered most."""
        peak = self.db.get_peak_capital(mode)
        if current_capital > peak:
            peak = current_capital
            self.db.update_peak_capital(peak, mode)
        if peak == 0:
            return 0.0
        return ((peak - current_capital) / peak) * 100

    def _calculate_portfolio_heat(self, open_positions: list,
                                   capital: float) -> float:
        if not open_positions or capital == 0:
            return 0.0
        total_risk = 0.0
        for pos in open_positions:
            # Equity/futures rows use entry_price; option rows use premium.
            entry = pos.get("entry_price") if pos.get("entry_price") is not None else pos.get("premium", 0)
            sl = pos.get("stop_loss", 0)
            qty = pos.get("quantity", 0)
            if entry and sl and qty:
                total_risk += abs(entry - sl) * qty
            elif entry and qty and "premium" in pos:
                # Long options with no stop_loss set: max loss is capped at
                # the premium paid, so use that as the risk contribution
                # instead of silently counting it as zero heat.
                total_risk += entry * qty
        return (total_risk / capital) * 100

    def calculate_position_size(self, entry: float, sl: float,
                                 mode: str = "PAPER",
                                 method: str = None) -> int:
        method = method or POSITION_SIZING_METHOD
        account = self.db.get_account(mode)
        if not account:
            return LOT_SIZE

        capital = account["current_capital"]
        multiplier = self._size_multiplier(capital, mode)
        risk_amt = capital * (RISK_PER_TRADE_PERCENT / 100) * multiplier

        risk_per_unit = abs(entry - sl) if sl and entry else entry * 0.005
        if risk_per_unit == 0:
            return LOT_SIZE

        if method == "kelly":
            size = self._kelly_size(capital, entry, risk_per_unit, mode)
        elif method == "volatility":
            size = int(risk_amt / (risk_per_unit * ATR_MULTIPLIER))
        else:
            size = int(risk_amt / risk_per_unit)

        lots = max(1, round(size / LOT_SIZE))
        return lots * LOT_SIZE

    def _size_multiplier(self, capital: float, mode: str = "PAPER") -> float:
        drawdown = self._calculate_drawdown(capital, mode)
        if drawdown < DRAWDOWN_REDUCTION_START:
            return 1.0
        if drawdown >= MAX_DRAWDOWN_PCT:
            return 0.0
        span = MAX_DRAWDOWN_PCT - DRAWDOWN_REDUCTION_START
        reduction = (drawdown - DRAWDOWN_REDUCTION_START) / span
        return max(0.25, 1.0 - reduction)

    def _kelly_size(self, capital: float, entry: float,
                    risk_per_unit: float, mode: str) -> int:
        stats = self.db.get_trade_stats(mode)
        total = stats.get("total_trades", 0)
        if total < 10:
            risk_amt = capital * (RISK_PER_TRADE_PERCENT / 100)
            return int(risk_amt / risk_per_unit)
        win_rate = stats.get("win_rate", 50) / 100
        avg_win = abs(stats.get("avg_win", 1))
        avg_loss = abs(stats.get("avg_loss", 1))
        if avg_loss == 0:
            return LOT_SIZE
        rr = avg_win / avg_loss
        kelly = win_rate - ((1 - win_rate) / rr)
        kelly = max(0, kelly * KELLY_FRACTION)
        risk_amt = capital * kelly
        return int(risk_amt / risk_per_unit)

    def get_risk_report(self, mode: str = "PAPER") -> Dict:
        account = self.db.get_account(mode)
        if not account:
            return {"can_trade": False, "error": "Account not found"}

        capital = account["current_capital"]
        drawdown = self._calculate_drawdown(capital, mode)
        daily_pnl = self.db.get_daily_pnl(mode)
        consec = self.db.get_consecutive_losses(mode)
        open_pos = self.db.get_open_positions(mode) + self.db.get_open_option_positions(mode)
        heat = self._calculate_portfolio_heat(open_pos, capital)
        multiplier = self._size_multiplier(capital, mode)
        trading_enabled = self.db.is_trading_enabled(mode)

        can_trade, cb_reason = self._check_circuit_breakers(account, mode)

        # Kill switch overrides everything
        if not trading_enabled:
            can_trade = False
            cb_reason = account.get("kill_switch_reason") or "Kill switch active"

        return {
            "can_trade": can_trade,
            "kill_switch_active": not trading_enabled,
            "circuit_breaker_active": not can_trade,
            "circuit_breaker_reason": cb_reason if not can_trade else None,
            "current_drawdown": round(drawdown, 2),
            "max_drawdown": MAX_DRAWDOWN_PCT,
            "peak_capital": round(self.db.get_peak_capital(mode), 2),
            "current_capital": round(capital, 2),
            "portfolio_heat": round(heat, 2),
            "consecutive_losses": consec,
            "daily_pnl": round(daily_pnl, 2),
            "trades_today": self.db.get_trades_today(mode),
            "open_positions": len(open_pos),
            "max_open_positions": MAX_OPEN_POSITIONS,
            "position_size_multiplier": round(multiplier, 2),
            "recovery_mode": drawdown >= DRAWDOWN_REDUCTION_START,
            "sizing_method": POSITION_SIZING_METHOD,
        }

    def check_trailing_stop(self, position: Dict,
                             current_price: float) -> Tuple[bool, str]:
        if not USE_TRAILING_STOPS:
            return False, ""
        entry = position.get("entry_price", 0)
        action = position.get("action", "BUY")
        if action.upper() in ("BUY", "LONG"):
            gain_pct = ((current_price - entry) / entry) * 100
            if gain_pct >= TRAILING_STOP_ACTIVATION:
                trail_price = current_price * (1 - TRAILING_STOP_DISTANCE / 100)
                sl = position.get("stop_loss", 0)
                if sl and current_price <= trail_price:
                    return True, f"Trailing stop hit at Rs.{current_price:,.2f}"
        else:
            gain_pct = ((entry - current_price) / entry) * 100
            if gain_pct >= TRAILING_STOP_ACTIVATION:
                trail_price = current_price * (1 + TRAILING_STOP_DISTANCE / 100)
                sl = position.get("stop_loss", 0)
                if sl and current_price >= trail_price:
                    return True, f"Trailing stop hit at Rs.{current_price:,.2f}"
        return False, ""


def create_risk_manager(db, initial_capital: float = INITIAL_CAPITAL) -> RiskManager:
    return RiskManager(db, initial_capital)

    