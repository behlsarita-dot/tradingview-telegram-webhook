#!/usr/bin/env python3
"""
Risk Manager - Paper Trading System v7.1

CHANGE LOG (v7.1, 2026-08-10):
Added a scoped auto-clear for circuit breaker trips caused SPECIFICALLY by
consecutive losses. See _maybe_auto_clear_consecutive_loss_halt() below for
the full rationale. Drawdown trips and daily-loss trips are UNCHANGED and
remain fully sticky/manual, per the original v7.0 design intent.
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

# FIX (2026-08-05): confirmed live on NIFTY260818P24500, 2026-08-05
# 11:35:07 IST — a BUY_OPTION signal with a genuinely symmetric 10-point
# SL/TP (entry - slPoints / entry + tpPoints, from the Pine script's
# fixed slPoints=10.0/tpPoints=10.0 inputs) was rejected with
# "R:R 1.00 below minimum 1.0". The displayed value (rr rounded to 2dp)
# and the actual comparison value disagreed: `entry - sl` and
# `tp - entry` are algebraically identical (both equal to the same fixed
# point offset) but are NOT guaranteed to produce bit-identical binary
# floating-point results — e.g. entry=126.5734 gives
# risk=abs(entry-sl)=10.0 exactly, but reward=abs(tp-entry)=
# 9.999999999999986, so rr=0.9999999999999986. That fails a strict
# `rr < MIN_RISK_REWARD_RATIO` (1.0) check while `f"{rr:.2f}"` still
# prints a clean "1.00", producing exactly the confusing
# "1.00 below minimum 1.0" message seen in Telegram. Reproduced this by
# sweeping 200,000 random entry premiums through entry±10.0: ~2% (4,350)
# landed on the wrong side of exact 10.0 purely from float rounding —
# not a rare edge case, an intermittent one that any symmetric-offset
# script (like this one) will keep hitting unpredictably. A tiny epsilon
# tolerance below is applied to the comparison only, so a genuinely
# sub-minimum R:R (e.g. 0.8) is still rejected exactly as before —
# 1e-9 is far larger than the ~1e-15 float error above but far smaller
# than any real trading R:R difference.
_RR_EPSILON = 1e-9

# NEW (2026-08-10): prefix used to identify consecutive-loss trips inside
# kill_switch_reason, so the auto-clear logic only ever matches trips it
# is explicitly allowed to touch. Any other reason string (drawdown,
# daily loss, or a manual halt with a custom message) is left alone.
_CONSECUTIVE_LOSS_TRIP_MARKER = "consecutive losses"


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

        # FIX (2026-07-10): previously this method made ~8 separate DB
        # calls (get_account, is_trading_enabled, get_peak_capital,
        # get_daily_pnl, get_consecutive_losses, get_open_positions,
        # get_open_option_positions, get_trades_today), each opening a
        # fresh unpooled connection to Neon. That consistently made
        # BUY/entry requests take far longer than EXIT requests (which
        # only need 1-2 connections), to the point of exceeding client
        # timeouts even though the request eventually succeeded.
        # get_validation_snapshot() now fetches everything in ONE
        # connection instead.
        snap = self.db.get_validation_snapshot(mode)
        account = snap["account"]
        if not account:
            return False, {"message": "Account not found"}

        # NEW (2026-08-10): before honoring a kill switch, check whether
        # it's a consecutive-loss trip that has become stale (today's
        # date-scoped counter is back under the limit) and auto-clear it
        # if so. Mutates `account` in place so every read below (kill
        # switch check, circuit breaker check) sees the cleared state
        # within this same request.
        self._maybe_auto_clear_consecutive_loss_halt(account, snap, mode)

        # Kill switch check — blocks all new entries
        trading_enabled = bool(account.get("trading_enabled", 1))
        if not trading_enabled:
            reason = account.get("kill_switch_reason") or "Trading manually halted"
            return False, {"message": f"Kill switch active: {reason}", "circuit_breaker": True}

        capital = account["current_capital"]

        can, reason = self._check_circuit_breakers(account, snap, mode)
        if not can:
            return False, {"message": reason, "circuit_breaker": True}

        # Combine equity/futures positions with option positions — an
        # options-only trader was previously invisible to both of these
        # checks, since only get_open_positions() (equity table) was
        # consulted. MAX_OPEN_POSITIONS could be silently exceeded, and
        # portfolio heat would read near-zero with several options open.
        all_open = snap["open_positions"] + snap["open_option_positions"]
        if len(all_open) >= MAX_OPEN_POSITIONS:
            return False, {"message": f"Max open positions reached ({MAX_OPEN_POSITIONS})"}

        trades_today = snap["trades_today"]
        if trades_today >= MAX_TRADES_PER_DAY:
            return False, {"message": f"Max trades per day reached ({MAX_TRADES_PER_DAY})"}

        if sl and tp and entry:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                rr = reward / risk
                # FIX (2026-08-05): epsilon tolerance — see _RR_EPSILON
                # comment at top of file. A symmetric-offset R:R that is
                # mathematically exactly at the minimum can land a hair
                # below it in binary floating point (e.g. 0.9999999999999986
                # instead of 1.0); without this tolerance that gets
                # rejected while the rounded display still reads "1.00",
                # producing a confusing false rejection.
                if rr < MIN_RISK_REWARD_RATIO - _RR_EPSILON:
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

    def _maybe_auto_clear_consecutive_loss_halt(self, account: Dict,
                                                  snap: Dict = None,
                                                  mode: str = "PAPER") -> bool:
        """NEW (2026-08-10): scoped auto-clear for consecutive-loss trips.

        BACKGROUND: as of the 2026-08-03 change, ANY circuit breaker trip
        (drawdown, daily loss, OR consecutive losses) sets a sticky
        trading_enabled=False that persists until a manual POST to
        /api/kill-switch with enabled=true — by design, so a trip never
        silently self-clears just because a date-scoped window empties out
        overnight.

        PROBLEM: get_consecutive_losses() / the validation snapshot's
        consecutive_losses field IS date-scoped (per the 2026-08-03
        database.py fix). So the day after a consecutive-losses trip, the
        counter correctly reads 0 (no losses yet today) while
        trading_enabled is still False from yesterday's trip — the two
        pieces of state disagree, and trading stays blocked indefinitely
        with no automatic path back even though the condition that caused
        the trip no longer holds.

        FIX: on every validate_new_trade() call, if the account is halted
        AND the halt reason is specifically a consecutive-losses trip (as
        identified by _CONSECUTIVE_LOSS_TRIP_MARKER in the stored reason
        string) AND today's date-scoped consecutive-loss count is now
        below MAX_CONSECUTIVE_LOSSES, clear the halt via
        set_trading_enabled(True, ...) and mutate the in-memory `account`
        dict so the rest of THIS request sees the cleared state too.

        SCOPE, DELIBERATELY NARROW:
        - Drawdown trips are untouched — a drawdown halt has nothing to do
          with today's date, so there is no "staleness" for it to recover
          from automatically. Still requires manual clear.
        - Daily-loss trips are untouched for the same reason — MAX_DAILY_LOSS
          resets are a separate daily-P&L concern, not modeled here, so
          leaving this manual avoids accidentally re-enabling trading on a
          bad day just because of how P&L is being read.
        - A manual halt (trading disabled via a direct kill-switch POST with
          a custom reason that doesn't contain the marker string) is never
          auto-cleared, regardless of what the consecutive-loss counter
          says.
        - Only fires from validate_new_trade()'s snapshot path (this method
          takes an already-fetched `account` dict + optional `snap`). If
          `snap` is None it fetches today's consecutive-loss count directly
          so get_risk_report() (which calls _check_circuit_breakers with
          snap=None) also benefits and reports consistent state.

        Returns True if it cleared the halt, False otherwise (no
        behavioral action needed by callers — they just re-read
        `account["trading_enabled"]` after calling this).
        """
        trading_enabled = bool(account.get("trading_enabled", 1))
        if trading_enabled:
            return False  # nothing to clear

        reason = account.get("kill_switch_reason") or ""
        if _CONSECUTIVE_LOSS_TRIP_MARKER not in reason.lower():
            return False  # not a consecutive-loss trip — leave it alone

        consec = snap["consecutive_losses"] if snap is not None \
            else self.db.get_consecutive_losses(mode)

        if consec >= MAX_CONSECUTIVE_LOSSES:
            return False  # still genuinely at/above the limit today

        logger.info(
            f"Auto-clearing consecutive-loss circuit breaker for {mode}: "
            f"today's count ({consec}) is below limit ({MAX_CONSECUTIVE_LOSSES}). "
            f"Previous reason: {reason!r}"
        )
        self.db.set_trading_enabled(True, mode, None)

        # Reflect the clear in the in-memory dict immediately, so the
        # remainder of THIS validate_new_trade() call (and get_risk_report(),
        # which reads trading_enabled again after calling this) sees it
        # without needing a second DB round trip.
        account["trading_enabled"] = True
        account["kill_switch_reason"] = None
        return True

    def _check_circuit_breakers(self, account: Dict, snap: Dict = None,
                                 mode: str = "PAPER") -> Tuple[bool, str]:
        capital = account["current_capital"]

        # get_risk_report() still calls this without a snapshot — fall
        # back to the original individual-query path in that case so its
        # behavior and connection count are unchanged there.
        if snap is None:
            drawdown = self._calculate_drawdown(capital, mode)
            daily_pnl = self.db.get_daily_pnl(mode)
            consec = self.db.get_consecutive_losses(mode)
        else:
            drawdown = self._calculate_drawdown_from_account(capital, account, mode)
            daily_pnl = snap["daily_pnl"]
            consec = snap["consecutive_losses"]

        # NOTE (2026-08-03): the underlying consecutive-loss staleness
        # bug (get_consecutive_losses() / get_validation_snapshot()
        # reading all-time history with no date bound, tripping this
        # breaker off days-old losses before any trade happened in the
        # new session) is fixed in database.py's
        # _get_recent_closed_trades() — see that method's docstring.
        # This file didn't need to change for that fix; `consec` here
        # now arrives already correctly scoped to today.
        #
        # NOTE (2026-08-10): that date-scoping fix is exactly what makes
        # the NEW _maybe_auto_clear_consecutive_loss_halt() above safe to
        # trust — it reads the same correctly-scoped `consec` value.

        if drawdown >= MAX_DRAWDOWN_PCT:
            reason = f"Max drawdown {drawdown:.1f}% exceeded ({MAX_DRAWDOWN_PCT}%)"
            self._trip_circuit_breaker(reason, mode)
            return False, reason

        if daily_pnl <= -abs(MAX_DAILY_LOSS):
            reason = f"Daily loss limit Rs.{MAX_DAILY_LOSS:,.0f} reached"
            self._trip_circuit_breaker(reason, mode)
            return False, reason

        if consec >= MAX_CONSECUTIVE_LOSSES:
            reason = f"{consec} consecutive losses (limit {MAX_CONSECUTIVE_LOSSES})"
            self._trip_circuit_breaker(reason, mode)
            return False, reason

        return True, ""

    def _trip_circuit_breaker(self, reason: str, mode: str):
        """A circuit-breaker trip sends a Telegram message (via
        notify_circuit_breaker() in app.py) AND makes the halt an explicit,
        sticky state: trading_enabled flips to false with kill_switch_reason
        set, visible via /api/kill-switch (GET) and /api/system/info.

        As of v7.1 (2026-08-10), a trip stays halted until EITHER:
          (a) a deliberate POST to /api/kill-switch with enabled=true, OR
          (b) for consecutive-loss trips specifically, the next call to
              validate_new_trade() finds today's date-scoped consecutive-
              loss count back under MAX_CONSECUTIVE_LOSSES — see
              _maybe_auto_clear_consecutive_loss_halt().
        Drawdown and daily-loss trips are unaffected by (b) and remain
        (a)-only, same as the original v7.0 behavior.

        Only writes if not already disabled, so it doesn't overwrite a
        reason already set by an earlier manual kill-switch call, and
        doesn't spam updated_at on every single rejected signal while a
        trip is already active."""
        if self.db.is_trading_enabled(mode):
            self.db.set_trading_enabled(False, mode, f"Circuit breaker: {reason}")
            logger.warning(f"Circuit breaker tripped, trading halted: {reason}")

    def _calculate_drawdown(self, current_capital: float, mode: str = "PAPER") -> float:
        """Drawdown vs. a peak-capital high-water mark persisted in the
        account table. Previously peak_capital lived only as an in-memory
        attribute on this instance, so every process restart (which happens
        often on Render with the current SQLite-in-/tmp setup) silently
        reset it back to INITIAL_CAPITAL — understating real drawdown and
        weakening the circuit breaker right when it mattered most.

        Still used directly by get_risk_report(), calculate_position_size(),
        and _size_multiplier() — those call sites don't have a snapshot
        available, so they keep using this standalone version."""
        peak = self.db.get_peak_capital(mode)
        if current_capital > peak:
            peak = current_capital
            self.db.update_peak_capital(peak, mode)
        if peak == 0:
            return 0.0
        return ((peak - current_capital) / peak) * 100

    def _calculate_drawdown_from_account(self, current_capital: float,
                                          account: Dict, mode: str = "PAPER") -> float:
        """Same logic as _calculate_drawdown(), but reuses the account
        row already fetched in get_validation_snapshot() instead of a
        separate get_peak_capital() connection. Still writes back via
        update_peak_capital() on a new high, same as before — only the
        read is saved, not the occasional write."""
        peak = account.get("peak_capital")
        if peak is None:
            peak = account.get("initial_capital", current_capital)
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

        # NEW (2026-08-10): apply the same scoped auto-clear here so
        # /api/system/info and check_risk.py never show a stale
        # consecutive-loss halt that validate_new_trade() would already
        # have cleared on the next real trade attempt. snap=None here, so
        # this internally fetches today's consecutive-loss count itself.
        self._maybe_auto_clear_consecutive_loss_halt(account, None, mode)

        capital = account["current_capital"]
        drawdown = self._calculate_drawdown(capital, mode)
        daily_pnl = self.db.get_daily_pnl(mode)
        consec = self.db.get_consecutive_losses(mode)
        open_pos = self.db.get_open_positions(mode) + self.db.get_open_option_positions(mode)
        heat = self._calculate_portfolio_heat(open_pos, capital)
        multiplier = self._size_multiplier(capital, mode)
        trading_enabled = bool(account.get("trading_enabled", 1))

        can_trade, cb_reason = self._check_circuit_breakers(account, None, mode)

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
        """
        FIX (2026-08-01): trail_price was derived from current_price
        itself (trail_price = current_price * (1 - d/100)), then tested
        as current_price <= trail_price. That compares a number against
        a strictly smaller version of itself -- mathematically impossible
        for any positive TRAILING_STOP_DISTANCE, so this never fired. The
        mirrored short-side branch had the same defect.

        FIX (2026-08-01, v2): the first pass still computed the
        activation gate from current_price alone:
            gain_pct = ((current_price - entry) / entry) * 100
        That re-litigates "has this trade activated trailing yet?" from
        scratch on every tick using only the LATEST price. A position
        that ran up past activation, recorded a high peak, then gapped
        straight back down past both the activation threshold AND the
        trail level in a single tick (plausible for options) would have
        gain_pct computed from the low current_price, read as "never
        activated", and return False without ever comparing
        current_price to trail_price -- silently skipping the exact
        fast-reversal case a trailing stop exists to catch.

        Now the activation gate is computed from the peak/trough (which
        already folds in current_price via max/min) instead of from
        current_price alone, so once a trade has genuinely reached
        TRAILING_STOP_ACTIVATION at any point, the trail stays live on
        every subsequent tick regardless of how sharply price reverses.

        STILL NOT WIRED IN: neither `positions` nor `option_positions`
        has a highest_price_since_entry / lowest_price_since_entry
        column yet, and nothing currently calls update_position_price()
        with a running peak/trough or invokes this function at all --
        exits in this system are event-driven from TradingView
        (EXIT/EXIT_OPTION), not server-polled. Wiring this in for real
        needs: (a) a price-polling loop, (b) persisting the peak/trough
        this function computes back onto the position row each tick,
        and (c) a decision on what firing True actually does
        (presumably a synthetic exit through the same path
        _handle_exit() uses).
        """
        if not USE_TRAILING_STOPS:
            return False, ""
        entry = position.get("entry_price", 0)
        if not entry:
            return False, ""
        sl = position.get("stop_loss", 0)
        if not sl:
            # Matches original intent: only trail a position that was
            # opened with an initial stop_loss set.
            return False, ""
        action = position.get("action", "BUY")

        if action.upper() in ("BUY", "LONG"):
            peak = position.get("highest_price_since_entry", entry)
            peak = max(peak, current_price)
            gain_pct = ((peak - entry) / entry) * 100
            if gain_pct < TRAILING_STOP_ACTIVATION:
                return False, ""
            trail_price = peak * (1 - TRAILING_STOP_DISTANCE / 100)
            if current_price <= trail_price:
                return True, f"Trailing stop hit at Rs.{current_price:,.2f} (peak Rs.{peak:,.2f})"
        else:
            trough = position.get("lowest_price_since_entry", entry)
            trough = min(trough, current_price)
            gain_pct = ((entry - trough) / entry) * 100
            if gain_pct < TRAILING_STOP_ACTIVATION:
                return False, ""
            trail_price = trough * (1 + TRAILING_STOP_DISTANCE / 100)
            if current_price >= trail_price:
                return True, f"Trailing stop hit at Rs.{current_price:,.2f} (trough Rs.{trough:,.2f})"
        return False, ""


def create_risk_manager(db, initial_capital: float = INITIAL_CAPITAL) -> RiskManager:
    return RiskManager(db, initial_capital)