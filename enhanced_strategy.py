#!/usr/bin/env python3
"""
Enhanced Strategy Engine - Paper Trading System v7.0
Multi-timeframe confluence scoring for NIFTY 50 options.
"""

import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import ta
    LIBS_AVAILABLE = True
except ImportError as e:
    LIBS_AVAILABLE = False
    logger.warning(f"Strategy libs not available: {e}")

from config import (
    ADX_THRESHOLD, MIN_CONFLUENCE_SCORE, MIN_VOLUME_RATIO,
    PRIMARY_TIMEFRAME, CONFIRMATION_TIMEFRAME
)

SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
}

FALLBACK_PRICES = {
    "NIFTY": 24400,
    "BANKNIFTY": 52000,
    "SENSEX": 80000,
}


def _yf_symbol(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol.upper(), f"{symbol}.NS")


def _fetch_ohlcv(symbol: str, period: str = "5d",
                  interval: str = "15m"):
    if not LIBS_AVAILABLE:
        return None
    try:
        ticker = yf.Ticker(_yf_symbol(symbol))
        df = ticker.history(period=period, interval=interval)
        if df.empty or len(df) < 20:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        logger.warning(f"yfinance fetch failed ({symbol}): {e}")
        return None


def _compute_indicators(df) -> Dict:
    try:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
        adx = adx_ind.adx().iloc[-1]
        plus_di = adx_ind.adx_pos().iloc[-1]
        minus_di = adx_ind.adx_neg().iloc[-1]

        rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]

        ema20 = ta.trend.EMAIndicator(close, window=20).ema_indicator().iloc[-1]
        ema50 = ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1]

        vol_avg = volume.rolling(20).mean().iloc[-1]
        vol_now = volume.iloc[-1]
        vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1.0

        current_price = close.iloc[-1]

        return {
            "price": round(float(current_price), 2),
            "adx": round(float(adx), 2),
            "plus_di": round(float(plus_di), 2),
            "minus_di": round(float(minus_di), 2),
            "rsi": round(float(rsi), 2),
            "ema20": round(float(ema20), 2),
            "ema50": round(float(ema50), 2),
            "vol_ratio": round(float(vol_ratio), 2),
        }
    except Exception as e:
        logger.error(f"Indicator compute failed: {e}")
        return {}


def _score_confluence(ind: Dict, ind_1h: Dict = None) -> Dict:
    score = 0
    factors = []
    signal = "NONE"

    if not ind:
        return {"score": 0, "signal": "NONE", "factors": []}

    adx = ind.get("adx", 0)
    plus_di = ind.get("plus_di", 0)
    minus_di = ind.get("minus_di", 0)
    rsi = ind.get("rsi", 50)
    price = ind.get("price", 0)
    ema20 = ind.get("ema20", 0)
    ema50 = ind.get("ema50", 0)
    vol_ratio = ind.get("vol_ratio", 1.0)

    bullish = plus_di > minus_di
    bearish = minus_di > plus_di

    if adx >= ADX_THRESHOLD:
        score += 1
        factors.append(f"ADX {adx:.1f} - trending regime")

    if bullish and adx >= 20:
        score += 1
        factors.append(f"+DI {plus_di:.1f} > -DI {minus_di:.1f} - bullish")
        signal = "BUY"
    elif bearish and adx >= 20:
        score += 1
        factors.append(f"-DI {minus_di:.1f} > +DI {plus_di:.1f} - bearish")
        signal = "SELL"

    if price > ema20 > ema50:
        score += 1
        factors.append("Price > EMA20 > EMA50 - uptrend")
        if signal == "NONE":
            signal = "BUY"
    elif price < ema20 < ema50:
        score += 1
        factors.append("Price < EMA20 < EMA50 - downtrend")
        if signal == "NONE":
            signal = "SELL"

    if vol_ratio >= MIN_VOLUME_RATIO:
        score += 1
        factors.append(f"Volume {vol_ratio:.1f}x above average")

    if ind_1h:
        adx_1h = ind_1h.get("adx", 0)
        plus_1h = ind_1h.get("plus_di", 0)
        minus_1h = ind_1h.get("minus_di", 0)
        if signal == "BUY" and plus_1h > minus_1h and adx_1h >= 20:
            score += 1
            factors.append("1H timeframe confirms bullish")
        elif signal == "SELL" and minus_1h > plus_1h and adx_1h >= 20:
            score += 1
            factors.append("1H timeframe confirms bearish")

    return {"score": score, "signal": signal, "factors": factors}


def quick_analysis(symbol: str = "NIFTY") -> Dict:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    df_15m = _fetch_ohlcv(symbol, period="5d", interval="15m")
    df_1h = _fetch_ohlcv(symbol, period="10d", interval="60m")

    if df_15m is not None:
        ind = _compute_indicators(df_15m)
        ind_1h = _compute_indicators(df_1h) if df_1h is not None else {}
        result = _score_confluence(ind, ind_1h)
        price = ind.get("price", FALLBACK_PRICES.get(symbol, 24400))
        adx = ind.get("adx", 0)
        fallback = False
    else:
        price = FALLBACK_PRICES.get(symbol, 24400)
        ind = {"price": price, "adx": 0, "rsi": 50, "vol_ratio": 1.0}
        result = {"score": 0, "signal": "NONE", "factors": ["yfinance unavailable - no signal"]}
        adx = 0
        fallback = True

    score = result["score"]
    signal = result["signal"]

    if score >= 4:
        quality = "HIGH"
    elif score >= 3:
        quality = "MEDIUM"
    else:
        quality = "LOW"

    if signal == "BUY":
        sl = round(price * 0.99, 2)
        tp = round(price * 1.02, 2)
    elif signal == "SELL":
        sl = round(price * 1.01, 2)
        tp = round(price * 0.98, 2)
    else:
        sl = tp = None

    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "signal": signal,
        "quality": quality,
        "confluence_score": score,
        "confluence_max": 5,
        "entry_price": price,
        "stop_loss": sl,
        "take_profit": tp,
        "adx": adx,
        "rsi": ind.get("rsi", 50),
        "volume_ratio": ind.get("vol_ratio", 1.0),
        "reasons": result["factors"],
        "fallback": fallback,
    }


class EnhancedStrategyEngine:
    def __init__(self):
        self.available = LIBS_AVAILABLE
        status = "yes" if LIBS_AVAILABLE else "no"
        logger.info(f"Strategy engine ready (libs={status})")

    def analyze(self, symbol: str = "NIFTY") -> Dict:
        return quick_analysis(symbol)

    def is_tradeable(self, analysis: Dict) -> bool:
        return (
            analysis.get("signal") in ("BUY", "SELL") and
            analysis.get("confluence_score", 0) >= MIN_CONFLUENCE_SCORE
        )


def create_strategy_engine() -> EnhancedStrategyEngine:
    return EnhancedStrategyEngine()
