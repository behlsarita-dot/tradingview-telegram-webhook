#!/usr/bin/env python3
"""
Backtester - Paper Trading System v7.0
Simple vectorised backtest on yfinance OHLCV data.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import ta
    LIBS_AVAILABLE = True
except ImportError:
    LIBS_AVAILABLE = False

from config import INITIAL_CAPITAL, LOT_SIZE
from portfolio import calculate_equity_charges

SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}


class Backtester:
    def __init__(self, initial_capital: float = INITIAL_CAPITAL):
        self.initial_capital = initial_capital
        self.available = LIBS_AVAILABLE

    def run(self, symbol: str, timeframe: str,
            start_date: str, end_date: str,
            parameters: Dict = None) -> Dict:

        if not LIBS_AVAILABLE:
            return {"success": False, "error": "pandas/yfinance not available"}

        params = {
            "adx_threshold": 25,
            "min_confluence_score": 3,
            "sl_pct": 1.0,
            "tp_pct": 2.0,
        }
        if parameters:
            params.update(parameters)

        yf_sym = SYMBOL_MAP.get(symbol.upper(), f"{symbol}.NS")

        interval_map = {
            "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "60m", "1d": "1d"
        }
        interval = interval_map.get(timeframe, "15m")

        try:
            ticker = yf.Ticker(yf_sym)
            df = ticker.history(start=start_date, end=end_date,
                                interval=interval)
            if df.empty or len(df) < 30:
                return {"success": False, "error": "Insufficient historical data"}
            df.columns = [c.lower() for c in df.columns]
        except Exception as e:
            return {"success": False, "error": f"Data fetch failed: {e}"}

        try:
            adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], 14)
            df["adx"] = adx_ind.adx()
            df["plus_di"] = adx_ind.adx_pos()
            df["minus_di"] = adx_ind.adx_neg()
            df["rsi"] = ta.momentum.RSIIndicator(df["close"], 14).rsi()
            df["ema20"] = ta.trend.EMAIndicator(df["close"], 20).ema_indicator()
            df["vol_avg"] = df["volume"].rolling(20).mean()
            df.dropna(inplace=True)
        except Exception as e:
            return {"success": False, "error": f"Indicator error: {e}"}

        trades = []
        capital = self.initial_capital
        peak = capital
        max_dd = 0.0
        in_trade = False
        entry_price = 0.0
        entry_idx = 0
        direction = ""
        entry_time = ""

        threshold = params["adx_threshold"]

        for i in range(1, len(df)):
            row = df.iloc[i]

            if not in_trade:
                score = 0
                sig = "NONE"

                if row["adx"] >= threshold:
                    score += 1
                if row["plus_di"] > row["minus_di"] and row["adx"] >= 20:
                    score += 1
                    sig = "BUY"
                elif row["minus_di"] > row["plus_di"] and row["adx"] >= 20:
                    score += 1
                    sig = "SELL"
                if row["volume"] > row["vol_avg"] * 1.2:
                    score += 1

                if score >= params["min_confluence_score"] and sig != "NONE":
                    in_trade = True
                    entry_price = float(row["close"])
                    entry_idx = i
                    direction = sig
                    entry_time = str(df.index[i])
            else:
                price = float(row["close"])
                sl_price = (entry_price * (1 - params["sl_pct"] / 100)
                            if direction == "BUY"
                            else entry_price * (1 + params["sl_pct"] / 100))
                tp_price = (entry_price * (1 + params["tp_pct"] / 100)
                            if direction == "BUY"
                            else entry_price * (1 - params["tp_pct"] / 100))

                exit_price = None
                exit_reason = ""

                if direction == "BUY":
                    if price <= sl_price:
                        exit_price = sl_price
                        exit_reason = "Stop Loss"
                    elif price >= tp_price:
                        exit_price = tp_price
                        exit_reason = "Take Profit"
                else:
                    if price >= sl_price:
                        exit_price = sl_price
                        exit_reason = "Stop Loss"
                    elif price <= tp_price:
                        exit_price = tp_price
                        exit_reason = "Take Profit"

                if not exit_price and (i - entry_idx) >= 20:
                    exit_price = price
                    exit_reason = "Max Hold"

                if exit_price:
                    pnl = ((exit_price - entry_price) * LOT_SIZE
                           if direction == "BUY"
                           else (entry_price - exit_price) * LOT_SIZE)
                    charges = (calculate_equity_charges(entry_price, LOT_SIZE, "BUY") +
                               calculate_equity_charges(exit_price, LOT_SIZE, "SELL"))
                    net_pnl = pnl - charges
                    capital += net_pnl

                    if capital > peak:
                        peak = capital
                    dd = ((peak - capital) / peak * 100) if peak > 0 else 0
                    if dd > max_dd:
                        max_dd = dd

                    trades.append({
                        "entry_time": entry_time,
                        "exit_time": str(df.index[i]),
                        "direction": direction,
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(exit_price, 2),
                        "pnl": round(pnl, 2),
                        "charges": round(charges, 2),
                        "net_pnl": round(net_pnl, 2),
                        "exit_reason": exit_reason,
                        "bars_held": i - entry_idx,
                    })
                    in_trade = False

        total = len(trades)
        wins = [t for t in trades if t["net_pnl"] > 0]
        losses = [t for t in trades if t["net_pnl"] < 0]
        total_pnl = sum(t["net_pnl"] for t in trades)

        gross_win = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses))
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0

        avg_bars = (sum(t["bars_held"] for t in trades) / total
                    if total > 0 else 0)

        equity_curve = [{"index": 0, "capital": self.initial_capital}]
        cap = self.initial_capital
        for t in trades:
            cap += t["net_pnl"]
            equity_curve.append({"index": len(equity_curve), "capital": round(cap, 2)})

        return {
            "success": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "start_date": start_date,
            "end_date": end_date,
            "parameters": params,
            "results": {
                "total_trades": total,
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "win_rate": round(len(wins) / total * 100, 2) if total > 0 else 0,
                "profit_factor": pf,
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl / self.initial_capital * 100, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "avg_win": round(sum(t["net_pnl"] for t in wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(t["net_pnl"] for t in losses) / len(losses), 2) if losses else 0,
                "best_trade": round(max((t["net_pnl"] for t in trades), default=0), 2),
                "worst_trade": round(min((t["net_pnl"] for t in trades), default=0), 2),
                "avg_hold_bars": round(avg_bars, 1),
                "total_charges": round(sum(t["charges"] for t in trades), 2),
                "final_capital": round(capital, 2),
            },
            "trades": trades[-50:],
            "equity_curve": equity_curve,
        }


def create_backtester() -> Backtester:
    return Backtester()
