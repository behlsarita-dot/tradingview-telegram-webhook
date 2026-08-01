#!/usr/bin/env python3
"""
Configuration - Paper Trading System v7.0
Single source of truth for all settings.
"""

import os
import time
import secrets
import logging
import threading
from dotenv import load_dotenv

load_dotenv()

# SECURITY
SECRET_KEY = os.getenv('SECRET_KEY', secrets.token_hex(32))
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')

if not WEBHOOK_SECRET:
    logging.warning("WEBHOOK_SECRET not set - generating temporary secret. Set it in .env.")
    WEBHOOK_SECRET = secrets.token_hex(32)

# FLASK
FLASK_ENV = os.getenv('FLASK_ENV', 'production')
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
PORT = int(os.getenv('PORT', 5000))

# TRADING
TRADING_MODE = os.getenv('TRADING_MODE', 'PAPER').upper()
INITIAL_CAPITAL = float(os.getenv('INITIAL_CAPITAL', 500000))
DEFAULT_SYMBOL = os.getenv('DEFAULT_SYMBOL', 'NIFTY')
DEFAULT_EXCHANGE = os.getenv('DEFAULT_EXCHANGE', 'NSE')
LOT_SIZE = int(os.getenv('LOT_SIZE', 65))

# POSITION SIZING
MAX_POSITION_SIZE_PERCENT = float(os.getenv('MAX_POSITION_SIZE_PERCENT', 10.0))
STOP_LOSS_PERCENT = float(os.getenv('STOP_LOSS_PERCENT', 0.5))
TAKE_PROFIT_PERCENT = float(os.getenv('TAKE_PROFIT_PERCENT', 1.0))
MAX_OPEN_POSITIONS = int(os.getenv('MAX_OPEN_POSITIONS', 5))
MAX_DAILY_LOSS = float(os.getenv('MAX_DAILY_LOSS', 15000))
MAX_TRADES_PER_DAY = int(os.getenv('MAX_TRADES_PER_DAY', 20))

# RISK MANAGEMENT
ENABLE_RISK_MANAGEMENT = os.getenv('ENABLE_RISK_MANAGEMENT', 'true').lower() == 'true'
POSITION_SIZING_METHOD = os.getenv('POSITION_SIZING_METHOD', 'fixed_fractional')
RISK_PER_TRADE_PERCENT = float(os.getenv('RISK_PER_TRADE_PERCENT', 1.0))
MIN_RISK_REWARD_RATIO = float(os.getenv('MIN_RISK_REWARD_RATIO', 1.0))
MAX_PORTFOLIO_HEAT = float(os.getenv('MAX_PORTFOLIO_HEAT', 20.0))
MAX_DRAWDOWN_PCT = float(os.getenv('MAX_DRAWDOWN_PCT', 15.0))
DRAWDOWN_REDUCTION_START = float(os.getenv('DRAWDOWN_REDUCTION_START', 10.0))
MAX_CONSECUTIVE_LOSSES = int(os.getenv('MAX_CONSECUTIVE_LOSSES', 3))
USE_TRAILING_STOPS = os.getenv('USE_TRAILING_STOPS', 'true').lower() == 'true'
TRAILING_STOP_ACTIVATION = float(os.getenv('TRAILING_STOP_ACTIVATION', 1.5))
TRAILING_STOP_DISTANCE = float(os.getenv('TRAILING_STOP_DISTANCE', 1.0))
KELLY_FRACTION = float(os.getenv('KELLY_FRACTION', 0.25))
ATR_MULTIPLIER = float(os.getenv('ATR_MULTIPLIER', 2.0))

# ENHANCED STRATEGY
ENABLE_ENHANCED_STRATEGY = os.getenv('ENABLE_ENHANCED_STRATEGY', 'true').lower() == 'true'
MIN_CONFLUENCE_SCORE = int(os.getenv('MIN_CONFLUENCE_SCORE', 3))
PRIMARY_TIMEFRAME = os.getenv('PRIMARY_TIMEFRAME', '15m')
CONFIRMATION_TIMEFRAME = os.getenv('CONFIRMATION_TIMEFRAME', '1h')
TREND_TIMEFRAME = os.getenv('TREND_TIMEFRAME', '1d')
MIN_VOLUME_RATIO = float(os.getenv('MIN_VOLUME_RATIO', 1.2))
ADX_THRESHOLD = int(os.getenv('ADX_THRESHOLD', 25))

# TELEGRAM
TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'true').lower() == 'true'
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
TELEGRAM_TIMEOUT = int(os.getenv('TELEGRAM_TIMEOUT', 10))

# DATABASE
# DATABASE_URL is the Postgres connection string (e.g. from Render Postgres,
# Neon, or Supabase). When set, database.py uses Postgres and ignores DB_FILE
# entirely. DB_FILE is kept only as a legacy fallback for local SQLite dev/
# testing without a Postgres instance configured.
DATABASE_URL = os.getenv('DATABASE_URL', '')
DB_FILE = os.getenv('DB_FILE', 'trading_bot.db')

if not DATABASE_URL:
    logging.warning(
        "DATABASE_URL not set - falling back to local SQLite (DB_FILE). "
        "On Render this data does NOT survive restarts/deploys unless DB_FILE "
        "points at a mounted persistent disk. Set DATABASE_URL for durable storage."
    )

# BACKUP
BACKUP_DIR = os.getenv('BACKUP_DIR', 'backups')
BACKUP_INTERVAL_HOURS = int(os.getenv('BACKUP_INTERVAL_HOURS', 6))
BACKUP_RETENTION_DAYS = int(os.getenv('BACKUP_RETENTION_DAYS', 7))
COMPRESS_BACKUPS = os.getenv('COMPRESS_BACKUPS', 'true').lower() == 'true'

# LOGGING
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = os.getenv('LOG_FILE', 'trading_bot.log')

# WEBHOOK
WEBHOOK_MAX_AGE_SECONDS = int(os.getenv('WEBHOOK_MAX_AGE_SECONDS', 300))

# REDIS
REDIS_URL = os.getenv('REDIS_URL', '')


class MockRedis:
    """Thread-safe in-memory Redis mock for single-worker deployments.

    FIX (2026-08-01): expire() was a no-op stub that returned True but
    never actually recorded a TTL. rate_limit() in app.py relies on
    real expiry to reset its window:
        count = redis.incr(rkey)
        if count == 1:
            redis.expire(rkey, window)
        return count <= limit
    Against real Redis, the key vanishes after `window` seconds and the
    next incr() starts a fresh count of 1. Against the old MockRedis,
    expire() did nothing, so the counter kept growing for the entire
    life of the process instead of resetting every 60s. Once any single
    IP crossed `limit` (30) *lifetime* requests, every subsequent
    webhook from that IP got permanently rejected with 429 until a
    process restart -- and since init_redis() silently falls back to
    MockRedis on any Redis connection failure, a transient Upstash
    outage would quietly turn into a permanent webhook lockout rather
    than a temporary one.

    Now every key has a real expiry timestamp. get()/incr() check it
    before touching a key and treat an expired key as absent, so incr()
    on an expired counter starts over at 1 -- matching real Redis
    behavior. A lock guards all operations since this docstring already
    claimed thread-safety that the original implementation didn't
    actually provide.
    """

    def __init__(self):
        self._store = {}
        self._counts = {}
        self._expiry = {}   # key -> epoch seconds when it expires
        self._lock = threading.Lock()

    def _expired(self, key) -> bool:
        deadline = self._expiry.get(key)
        return deadline is not None and time.time() >= deadline

    def _purge_if_expired(self, key):
        if self._expired(key):
            self._store.pop(key, None)
            self._counts.pop(key, None)
            self._expiry.pop(key, None)

    def get(self, key):
        with self._lock:
            self._purge_if_expired(key)
            return self._store.get(key)

    def set(self, key, value, ex=None):
        with self._lock:
            self._store[key] = value
            if ex is not None:
                self._expiry[key] = time.time() + ex
            else:
                self._expiry.pop(key, None)
            return True

    def incr(self, key):
        with self._lock:
            self._purge_if_expired(key)
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]

    def expire(self, key, seconds):
        with self._lock:
            if key not in self._counts and key not in self._store:
                return False
            self._expiry[key] = time.time() + seconds
            return True

    def delete(self, key):
        with self._lock:
            self._store.pop(key, None)
            self._counts.pop(key, None)
            self._expiry.pop(key, None)
            return True

    def ping(self):
        return True


def init_redis():
    """Return Redis client or MockRedis fallback."""
    if not REDIS_URL:
        return MockRedis()
    try:
        import redis
        client = redis.from_url(REDIS_URL, socket_connect_timeout=5)
        client.ping()
        logging.info("Redis connected")
        return client
    except Exception as e:
        logging.warning(f"Redis unavailable ({e}) - using MockRedis")
        return MockRedis()


def validate_config():
    """Warn on missing critical values at startup."""
    warnings = []
    if not os.getenv('SECRET_KEY'):
        warnings.append("SECRET_KEY not set - using random key (sessions won't persist)")
    if not os.getenv('WEBHOOK_SECRET'):
        warnings.append("WEBHOOK_SECRET not set - using random key (TradingView alerts will fail)")
    if not TELEGRAM_BOT_TOKEN:
        warnings.append("TELEGRAM_BOT_TOKEN not set - Telegram disabled")
    if not TELEGRAM_CHAT_ID:
        warnings.append("TELEGRAM_CHAT_ID not set - Telegram disabled")
    if not DATABASE_URL:
        warnings.append("DATABASE_URL not set - using ephemeral local SQLite, data will NOT survive a Render restart/redeploy")
    if DRAWDOWN_REDUCTION_START >= MAX_DRAWDOWN_PCT:
        warnings.append("DRAWDOWN_REDUCTION_START must be less than MAX_DRAWDOWN_PCT")
    for w in warnings:
        logging.warning(f"CONFIG: {w}")
    return warnings
