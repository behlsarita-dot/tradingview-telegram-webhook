#!/usr/bin/env python3
"""
Database Manager - Paper Trading System v7.0
Postgres (Neon) version - replaces the SQLite implementation.

Why this exists: the SQLite file was living on Render's ephemeral
container filesystem (no persistent disk attached), so every deploy
wiped current_capital, trade history, and peak_capital back to
INITIAL_CAPITAL - confirmed happening live on 2026-07-09. Postgres on
Neon lives outside the container entirely, so it survives deploys,
restarts, and container swaps.

All public method signatures are unchanged from the SQLite version, so
app.py, portfolio.py, and risk_manager.py require NO changes - they
call db.get_account(), db.close_option_position(), etc. exactly as
before.

Requires: psycopg2-binary (add to requirements.txt)
Requires env var: DATABASE_URL - the Neon connection string, e.g.
    postgresql://user:password@ep-xxxx.region.aws.neon.tech/dbname?sslmode=require
"""

import os
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Dict

import psycopg2
import psycopg2.extras

from config import INITIAL_CAPITAL, TRADING_MODE

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


class DatabaseManager:
    def __init__(self, db_url: str = DATABASE_URL):
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Add it as an env var on Render "
                "with your Neon connection string (include ?sslmode=require)."
            )
        self.db_url = db_url
        self._init_db()

    @contextmanager
    def get_cursor(self):
        # A fresh connection per call (matches the original SQLite
        # pattern of one connection per operation). This is deliberately
        # NOT pooled - Neon's free tier scales compute to zero after ~5
        # min idle, so a short-lived connection per request plays nicely
        # with that rather than fighting it with a long-lived pool.
        conn = psycopg2.connect(self.db_url, sslmode="require")
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def _init_db(self):
        with self.get_cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id SERIAL PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'PAPER',
                    initial_capital REAL NOT NULL,
                    current_capital REAL NOT NULL,
                    total_pnl REAL NOT NULL DEFAULT 0.0,
                    daily_pnl REAL NOT NULL DEFAULT 0.0,
                    open_positions INTEGER NOT NULL DEFAULT 0,
                    total_trades INTEGER NOT NULL DEFAULT 0,
                    winning_trades INTEGER NOT NULL DEFAULT 0,
                    losing_trades INTEGER NOT NULL DEFAULT 0,
                    trading_enabled INTEGER NOT NULL DEFAULT 1,
                    kill_switch_reason TEXT,
                    peak_capital REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'PAPER',
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    quantity INTEGER NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    current_price REAL,
                    unrealized_pnl REAL DEFAULT 0.0,
                    pnl REAL,
                    entry_charges REAL DEFAULT 0.0,
                    exit_charges REAL DEFAULT 0.0,
                    total_charges REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    exit_reason TEXT,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    atr REAL,
                    adx REAL,
                    confluence_score INTEGER DEFAULT 0
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS option_positions (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'PAPER',
                    underlying TEXT NOT NULL,
                    option_symbol TEXT NOT NULL,
                    option_type TEXT NOT NULL,
                    strike REAL NOT NULL,
                    expiry TEXT NOT NULL,
                    premium REAL NOT NULL,
                    exit_premium REAL,
                    quantity INTEGER NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    pnl REAL,
                    entry_charges REAL DEFAULT 0.0,
                    exit_charges REAL DEFAULT 0.0,
                    total_charges REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    exit_reason TEXT,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS risk_metrics (
                    id SERIAL PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'PAPER',
                    timestamp TEXT NOT NULL,
                    drawdown_pct REAL DEFAULT 0.0,
                    portfolio_heat_pct REAL DEFAULT 0.0,
                    consecutive_losses INTEGER DEFAULT 0,
                    daily_pnl REAL DEFAULT 0.0,
                    var_95 REAL DEFAULT 0.0
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS circuit_breakers (
                    id SERIAL PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'PAPER',
                    reason TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    reset_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)

            # Postgres supports ADD COLUMN IF NOT EXISTS directly, so no
            # need for the old SQLite PRAGMA table_info() + manual check.
            c.execute("ALTER TABLE account ADD COLUMN IF NOT EXISTS trading_enabled INTEGER DEFAULT 1")
            c.execute("ALTER TABLE account ADD COLUMN IF NOT EXISTS kill_switch_reason TEXT")
            c.execute("ALTER TABLE account ADD COLUMN IF NOT EXISTS peak_capital REAL")

            c.execute("SELECT COUNT(*) as cnt FROM account WHERE mode='PAPER'")
            if c.fetchone()["cnt"] == 0:
                now = datetime.now().isoformat()
                c.execute("""
                    INSERT INTO account
                        (mode, initial_capital, current_capital, total_pnl,
                         daily_pnl, open_positions, total_trades,
                         winning_trades, losing_trades, trading_enabled,
                         peak_capital, created_at, updated_at)
                    VALUES (%s, %s, %s, 0, 0, 0, 0, 0, 0, 1, %s, %s, %s)
                """, ("PAPER", INITIAL_CAPITAL, INITIAL_CAPITAL, INITIAL_CAPITAL, now, now))
                logger.info(f"Paper account initialised with Rs.{INITIAL_CAPITAL:,.2f}")

            c.execute("""
                UPDATE account
                SET peak_capital = GREATEST(current_capital, initial_capital)
                WHERE peak_capital IS NULL
            """)

        logger.info("Database initialised (Postgres/Neon)")

    # ------------------------------------------------------------------ #
    # ACCOUNT
    # ------------------------------------------------------------------ #

    def get_account(self, mode: str = "PAPER") -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("SELECT * FROM account WHERE mode=%s", (mode,))
            row = c.fetchone()
            return dict(row) if row else None

    def update_account(self, mode: str = "PAPER", **kwargs):
        if not kwargs:
            return
        fields = ", ".join(f"{k}=%s" for k in kwargs)
        kwargs["updated_at"] = datetime.now().isoformat()
        fields += ", updated_at=%s"
        values = list(kwargs.values())
        with self.get_cursor() as c:
            c.execute(f"UPDATE account SET {fields} WHERE mode=%s", values + [mode])

    def is_trading_enabled(self, mode: str = "PAPER") -> bool:
        with self.get_cursor() as c:
            c.execute("SELECT trading_enabled FROM account WHERE mode=%s", (mode,))
            row = c.fetchone()
            return bool(row["trading_enabled"]) if row and row["trading_enabled"] is not None else True

    def set_trading_enabled(self, enabled: bool, mode: str = "PAPER", reason: str = ""):
        with self.get_cursor() as c:
            c.execute(
                "UPDATE account SET trading_enabled=%s, kill_switch_reason=%s, updated_at=%s WHERE mode=%s",
                (1 if enabled else 0, reason, datetime.now().isoformat(), mode)
            )

    def get_peak_capital(self, mode: str = "PAPER") -> float:
        with self.get_cursor() as c:
            c.execute("SELECT peak_capital, initial_capital FROM account WHERE mode=%s", (mode,))
            row = c.fetchone()
            if not row:
                return 0.0
            return row["peak_capital"] if row["peak_capital"] is not None else row["initial_capital"]

    def update_peak_capital(self, peak: float, mode: str = "PAPER"):
        with self.get_cursor() as c:
            c.execute(
                "UPDATE account SET peak_capital=%s, updated_at=%s WHERE mode=%s",
                (peak, datetime.now().isoformat(), mode)
            )

    # ------------------------------------------------------------------ #
    # VALIDATION SNAPSHOT — single connection for validate_new_trade()
    # ------------------------------------------------------------------ #

    def get_validation_snapshot(self, mode: str = "PAPER") -> Dict:
        """Fetches everything RiskManager.validate_new_trade() needs in
        ONE connection instead of ~8 separate ones (get_account,
        is_trading_enabled, get_peak_capital, get_daily_pnl,
        get_consecutive_losses, get_open_positions,
        get_open_option_positions, get_trades_today). Each connection is
        a fresh TCP+TLS handshake to Neon (no pooling, by design - see
        get_cursor() above), so on a cold or lightly-loaded connection
        those round trips were stacking up and causing BUY/entry requests
        to consistently take far longer than EXIT requests, which only
        ever needed 1-2 connections. Confirmed via webhook_tester.py and
        server logs on 2026-07-10: BUY signals reliably exceeded a 10s
        client timeout while completing successfully a few seconds later
        server-side once the connection overhead finished."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self.get_cursor() as c:
            c.execute("SELECT * FROM account WHERE mode=%s", (mode,))
            account_row = c.fetchone()
            account = dict(account_row) if account_row else None

            c.execute("""
                SELECT pnl - total_charges as net_pnl, exit_time FROM (
                    SELECT pnl, total_charges, exit_time FROM positions
                    WHERE mode=%s AND status='CLOSED'
                    UNION ALL
                    SELECT pnl, total_charges, exit_time FROM option_positions
                    WHERE mode=%s AND status='CLOSED'
                ) t
                ORDER BY exit_time DESC LIMIT 20
            """, (mode, mode))
            closed_rows = c.fetchall()

            c.execute("""
                SELECT COALESCE(SUM(pnl - total_charges), 0) as daily_pnl FROM (
                    SELECT pnl, total_charges FROM positions
                    WHERE mode=%s AND status='CLOSED'
                        AND exit_time IS NOT NULL AND DATE(exit_time::timestamp)=%s
                    UNION ALL
                    SELECT pnl, total_charges FROM option_positions
                    WHERE mode=%s AND status='CLOSED'
                        AND exit_time IS NOT NULL AND DATE(exit_time::timestamp)=%s
                ) t
            """, (mode, today, mode, today))
            daily_pnl = c.fetchone()["daily_pnl"] or 0.0

            c.execute("""
                SELECT * FROM positions
                WHERE mode=%s AND status='OPEN'
                ORDER BY entry_time DESC
            """, (mode,))
            open_positions = [dict(r) for r in c.fetchall()]

            c.execute("""
                SELECT * FROM option_positions
                WHERE mode=%s AND status='OPEN'
                ORDER BY entry_time DESC
            """, (mode,))
            open_option_positions = [dict(r) for r in c.fetchall()]

            c.execute("""
                SELECT COUNT(*) as cnt FROM (
                    SELECT id FROM positions WHERE mode=%s AND DATE(entry_time::timestamp)=%s
                    UNION ALL
                    SELECT id FROM option_positions WHERE mode=%s AND DATE(entry_time::timestamp)=%s
                ) t
            """, (mode, today, mode, today))
            trades_today = c.fetchone()["cnt"] or 0

        consecutive_losses = 0
        for r in closed_rows:
            if r["net_pnl"] < 0:
                consecutive_losses += 1
            else:
                break

        return {
            "account": account,
            "daily_pnl": daily_pnl,
            "open_positions": open_positions,
            "open_option_positions": open_option_positions,
            "trades_today": trades_today,
            "consecutive_losses": consecutive_losses,
        }

    # ------------------------------------------------------------------ #
    # POSITIONS
    # ------------------------------------------------------------------ #

    def open_position(self, mode: str, symbol: str, action: str,
                      entry_price: float, quantity: int,
                      stop_loss: float = None, take_profit: float = None,
                      entry_charges: float = 0.0, atr: float = None,
                      adx: float = None, confluence_score: int = 0) -> str:
        pos_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                INSERT INTO positions
                    (id, mode, symbol, action, entry_price, quantity,
                     stop_loss, take_profit, entry_charges, status,
                     entry_time, atr, adx, confluence_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s, %s)
            """, (pos_id, mode, symbol, action, entry_price, quantity,
                  stop_loss, take_profit, entry_charges, now,
                  atr, adx, confluence_score))
        return pos_id

    def close_position(self, pos_id: str, exit_price: float,
                       exit_reason: str, pnl: float,
                       exit_charges: float = 0.0):
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                UPDATE positions
                SET status='CLOSED', exit_price=%s, exit_time=%s,
                    exit_reason=%s, pnl=%s, exit_charges=%s,
                    total_charges=entry_charges+%s
                WHERE id=%s
            """, (exit_price, now, exit_reason, pnl,
                  exit_charges, exit_charges, pos_id))

    def get_open_positions(self, mode: str = "PAPER") -> List[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM positions
                WHERE mode=%s AND status='OPEN'
                ORDER BY entry_time DESC
            """, (mode,))
            return [dict(r) for r in c.fetchall()]

    def get_position_by_id(self, pos_id: str) -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("SELECT * FROM positions WHERE id=%s", (pos_id,))
            row = c.fetchone()
            return dict(row) if row else None

    def get_open_position_by_symbol(self, symbol: str, mode: str = "PAPER") -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM positions
                WHERE symbol=%s AND mode=%s AND status='OPEN'
                ORDER BY entry_time DESC LIMIT 1
            """, (symbol, mode))
            row = c.fetchone()
            return dict(row) if row else None

    def get_all_positions(self, mode: str = "PAPER",
                          status: str = None, limit: int = 50) -> List[Dict]:
        with self.get_cursor() as c:
            if status:
                c.execute("""
                    SELECT * FROM positions
                    WHERE mode=%s AND status=%s
                    ORDER BY entry_time DESC LIMIT %s
                """, (mode, status.upper(), limit))
            else:
                c.execute("""
                    SELECT * FROM positions
                    WHERE mode=%s
                    ORDER BY entry_time DESC LIMIT %s
                """, (mode, limit))
            return [dict(r) for r in c.fetchall()]

    def update_position_price(self, pos_id: str,
                              current_price: float, unrealized_pnl: float):
        with self.get_cursor() as c:
            c.execute("""
                UPDATE positions
                SET current_price=%s, unrealized_pnl=%s
                WHERE id=%s
            """, (current_price, unrealized_pnl, pos_id))

    # ------------------------------------------------------------------ #
    # OPTIONS
    # ------------------------------------------------------------------ #

    def open_option_position(self, mode: str, underlying: str,
                             option_symbol: str, option_type: str,
                             strike: float, expiry: str, premium: float,
                             quantity: int, stop_loss: float = None,
                             take_profit: float = None,
                             entry_charges: float = 0.0) -> str:
        pos_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                INSERT INTO option_positions
                    (id, mode, underlying, option_symbol, option_type,
                     strike, expiry, premium, quantity, stop_loss,
                     take_profit, entry_charges, status, entry_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s)
            """, (pos_id, mode, underlying, option_symbol, option_type,
                  strike, expiry, premium, quantity, stop_loss,
                  take_profit, entry_charges, now))
        return pos_id

    def close_option_position(self, pos_id: str, exit_premium: float,
                              exit_reason: str, pnl: float,
                              exit_charges: float = 0.0):
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                UPDATE option_positions
                SET status='CLOSED', exit_premium=%s, exit_time=%s,
                    exit_reason=%s, pnl=%s, exit_charges=%s,
                    total_charges=entry_charges+%s
                WHERE id=%s
            """, (exit_premium, now, exit_reason, pnl,
                  exit_charges, exit_charges, pos_id))

    def get_open_option_positions(self, mode: str = "PAPER") -> List[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM option_positions
                WHERE mode=%s AND status='OPEN'
                ORDER BY entry_time DESC
            """, (mode,))
            return [dict(r) for r in c.fetchall()]

    def get_open_option_by_symbol(self, option_symbol: str,
                                  mode: str = "PAPER") -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM option_positions
                WHERE option_symbol=%s AND mode=%s AND status='OPEN'
                ORDER BY entry_time DESC LIMIT 1
            """, (option_symbol, mode))
            row = c.fetchone()
            return dict(row) if row else None

    def get_all_option_positions(self, mode: str = "PAPER",
                                 status: str = None, limit: int = 50) -> List[Dict]:
        with self.get_cursor() as c:
            if status:
                c.execute("""
                    SELECT * FROM option_positions
                    WHERE mode=%s AND status=%s
                    ORDER BY entry_time DESC LIMIT %s
                """, (mode, status.upper(), limit))
            else:
                c.execute("""
                    SELECT * FROM option_positions
                    WHERE mode=%s
                    ORDER BY entry_time DESC LIMIT %s
                """, (mode, limit))
            return [dict(r) for r in c.fetchall()]

    # ------------------------------------------------------------------ #
    # STATISTICS
    # ------------------------------------------------------------------ #

    def get_trade_stats(self, mode: str = "PAPER") -> Dict:
        with self.get_cursor() as c:
            c.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN net_pnl = 0 THEN 1 ELSE 0 END) as breakeven,
                    COALESCE(SUM(net_pnl), 0) as total_pnl,
                    COALESCE(AVG(CASE WHEN net_pnl > 0 THEN net_pnl END), 0) as avg_win,
                    COALESCE(AVG(CASE WHEN net_pnl < 0 THEN net_pnl END), 0) as avg_loss,
                    COALESCE(MAX(net_pnl), 0) as best_trade,
                    COALESCE(MIN(net_pnl), 0) as worst_trade,
                    COALESCE(SUM(total_charges), 0) as total_charges
                FROM (
                    SELECT
                        pnl - total_charges as net_pnl,
                        total_charges
                    FROM positions WHERE mode=%s AND status='CLOSED'
                    UNION ALL
                    SELECT
                        pnl - total_charges as net_pnl,
                        total_charges
                    FROM option_positions WHERE mode=%s AND status='CLOSED'
                ) t
            """, (mode, mode))
            row = dict(c.fetchone())
            total = row["total_trades"] or 0
            wins = row["wins"] or 0
            row["win_rate"] = round((wins / total * 100), 2) if total > 0 else 0.0
            gross_profit = abs(row["avg_win"] * wins) if wins else 0
            gross_loss = abs(row["avg_loss"] * (row["losses"] or 0))
            row["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0
            return row

    def get_consecutive_losses(self, mode: str = "PAPER") -> int:
        # NOTE (fixed 2026-07-10): previously only queried `positions`,
        # so the circuit breaker was blind to consecutive losses on
        # option trades - the only kind the live bot actually places.
        # Now unions both tables and orders by the combined exit_time,
        # matching the pattern already used in get_trade_stats() and
        # get_daily_pnl(). Still used standalone by get_risk_report(),
        # which does not go through get_validation_snapshot().
        with self.get_cursor() as c:
            c.execute("""
                SELECT pnl - total_charges as net_pnl, exit_time FROM (
                    SELECT pnl, total_charges, exit_time FROM positions
                    WHERE mode=%s AND status='CLOSED'
                    UNION ALL
                    SELECT pnl, total_charges, exit_time FROM option_positions
                    WHERE mode=%s AND status='CLOSED'
                ) t
                ORDER BY exit_time DESC LIMIT 20
            """, (mode, mode))
            rows = c.fetchall()
            count = 0
            for r in rows:
                if r["net_pnl"] < 0:
                    count += 1
                else:
                    break
            return count

    def get_daily_pnl(self, mode: str = "PAPER") -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        with self.get_cursor() as c:
            c.execute("""
                SELECT COALESCE(SUM(pnl - total_charges), 0) as daily_pnl FROM (
                    SELECT pnl, total_charges FROM positions
                    WHERE mode=%s AND status='CLOSED'
                        AND exit_time IS NOT NULL AND DATE(exit_time::timestamp)=%s
                    UNION ALL
                    SELECT pnl, total_charges FROM option_positions
                    WHERE mode=%s AND status='CLOSED'
                        AND exit_time IS NOT NULL AND DATE(exit_time::timestamp)=%s
                ) t
            """, (mode, today, mode, today))
            return c.fetchone()["daily_pnl"] or 0.0

    def get_trades_today(self, mode: str = "PAPER") -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        with self.get_cursor() as c:
            c.execute("""
                SELECT COUNT(*) as cnt FROM (
                    SELECT id FROM positions WHERE mode=%s AND DATE(entry_time::timestamp)=%s
                    UNION ALL
                    SELECT id FROM option_positions WHERE mode=%s AND DATE(entry_time::timestamp)=%s
                ) t
            """, (mode, today, mode, today))
            return c.fetchone()["cnt"] or 0
        