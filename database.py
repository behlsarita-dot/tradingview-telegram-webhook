#!/usr/bin/env python3
"""
Database Manager - Paper Trading System v7.0
"""

import sqlite3
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Dict

from config import DB_FILE, INITIAL_CAPITAL, TRADING_MODE

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file
        self._init_db()

    @contextmanager
    def get_cursor(self):
        conn = sqlite3.connect(self.db_file, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    def _init_db(self):
        with self.get_cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT NOT NULL DEFAULT 'PAPER',
                    reason TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    reset_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)

            # Add kill switch columns to existing DB if missing
            c.execute("PRAGMA table_info(account)")
            cols = [r["name"] for r in c.fetchall()]
            if "trading_enabled" not in cols:
                c.execute("ALTER TABLE account ADD COLUMN trading_enabled INTEGER DEFAULT 1")
            if "kill_switch_reason" not in cols:
                c.execute("ALTER TABLE account ADD COLUMN kill_switch_reason TEXT")

            c.execute("SELECT COUNT(*) as cnt FROM account WHERE mode='PAPER'")
            if c.fetchone()["cnt"] == 0:
                now = datetime.now().isoformat()
                c.execute("""
                    INSERT INTO account
                        (mode, initial_capital, current_capital, total_pnl,
                         daily_pnl, open_positions, total_trades,
                         winning_trades, losing_trades, trading_enabled,
                         created_at, updated_at)
                    VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0, 1, ?, ?)
                """, ("PAPER", INITIAL_CAPITAL, INITIAL_CAPITAL, now, now))
                logger.info(f"Paper account initialised with Rs.{INITIAL_CAPITAL:,.2f}")

        logger.info("Database initialised")

    # ------------------------------------------------------------------ #
    # ACCOUNT
    # ------------------------------------------------------------------ #

    def get_account(self, mode: str = "PAPER") -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("SELECT * FROM account WHERE mode=?", (mode,))
            row = c.fetchone()
            return dict(row) if row else None

    def update_account(self, mode: str = "PAPER", **kwargs):
        if not kwargs:
            return
        fields = ", ".join(f"{k}=?" for k in kwargs)
        kwargs["updated_at"] = datetime.now().isoformat()
        fields += ", updated_at=?"
        values = list(kwargs.values())
        with self.get_cursor() as c:
            c.execute(f"UPDATE account SET {fields} WHERE mode=?", values + [mode])

    def is_trading_enabled(self, mode: str = "PAPER") -> bool:
        with self.get_cursor() as c:
            c.execute("SELECT trading_enabled FROM account WHERE mode=?", (mode,))
            row = c.fetchone()
            return bool(row["trading_enabled"]) if row and row["trading_enabled"] is not None else True

    def set_trading_enabled(self, enabled: bool, mode: str = "PAPER", reason: str = ""):
        with self.get_cursor() as c:
            c.execute(
                "UPDATE account SET trading_enabled=?, kill_switch_reason=?, updated_at=? WHERE mode=?",
                (1 if enabled else 0, reason, datetime.now().isoformat(), mode)
            )

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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
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
                SET status='CLOSED', exit_price=?, exit_time=?,
                    exit_reason=?, pnl=?, exit_charges=?,
                    total_charges=entry_charges+?
                WHERE id=?
            """, (exit_price, now, exit_reason, pnl,
                  exit_charges, exit_charges, pos_id))

    def get_open_positions(self, mode: str = "PAPER") -> List[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM positions
                WHERE mode=? AND status='OPEN'
                ORDER BY entry_time DESC
            """, (mode,))
            return [dict(r) for r in c.fetchall()]

    def get_position_by_id(self, pos_id: str) -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("SELECT * FROM positions WHERE id=?", (pos_id,))
            row = c.fetchone()
            return dict(row) if row else None

    def get_open_position_by_symbol(self, symbol: str, mode: str = "PAPER") -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM positions
                WHERE symbol=? AND mode=? AND status='OPEN'
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
                    WHERE mode=? AND status=?
                    ORDER BY entry_time DESC LIMIT ?
                """, (mode, status.upper(), limit))
            else:
                c.execute("""
                    SELECT * FROM positions
                    WHERE mode=?
                    ORDER BY entry_time DESC LIMIT ?
                """, (mode, limit))
            return [dict(r) for r in c.fetchall()]

    def update_position_price(self, pos_id: str,
                              current_price: float, unrealized_pnl: float):
        with self.get_cursor() as c:
            c.execute("""
                UPDATE positions
                SET current_price=?, unrealized_pnl=?
                WHERE id=?
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
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
                SET status='CLOSED', exit_premium=?, exit_time=?,
                    exit_reason=?, pnl=?, exit_charges=?,
                    total_charges=entry_charges+?
                WHERE id=?
            """, (exit_premium, now, exit_reason, pnl,
                  exit_charges, exit_charges, pos_id))

    def get_open_option_positions(self, mode: str = "PAPER") -> List[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM option_positions
                WHERE mode=? AND status='OPEN'
                ORDER BY entry_time DESC
            """, (mode,))
            return [dict(r) for r in c.fetchall()]

    def get_open_option_by_symbol(self, option_symbol: str,
                                  mode: str = "PAPER") -> Optional[Dict]:
        with self.get_cursor() as c:
            c.execute("""
                SELECT * FROM option_positions
                WHERE option_symbol=? AND mode=? AND status='OPEN'
                ORDER BY entry_time DESC LIMIT 1
            """, (option_symbol, mode))
            row = c.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # STATISTICS
    # ------------------------------------------------------------------ #

    def get_trade_stats(self, mode: str = "PAPER") -> Dict:
        with self.get_cursor() as c:
            c.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END) as breakeven,
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(AVG(CASE WHEN pnl > 0 THEN pnl END), 0) as avg_win,
                    COALESCE(AVG(CASE WHEN pnl < 0 THEN pnl END), 0) as avg_loss,
                    COALESCE(MAX(pnl), 0) as best_trade,
                    COALESCE(MIN(pnl), 0) as worst_trade,
                    COALESCE(SUM(total_charges), 0) as total_charges
                FROM positions
                WHERE mode=? AND status='CLOSED'
            """, (mode,))
            row = dict(c.fetchone())
            total = row["total_trades"] or 0
            wins = row["wins"] or 0
            row["win_rate"] = round((wins / total * 100), 2) if total > 0 else 0.0
            gross_profit = abs(row["avg_win"] * wins) if wins else 0
            gross_loss = abs(row["avg_loss"] * (row["losses"] or 0))
            row["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0
            return row

    def get_consecutive_losses(self, mode: str = "PAPER") -> int:
        with self.get_cursor() as c:
            c.execute("""
                SELECT pnl FROM positions
                WHERE mode=? AND status='CLOSED'
                ORDER BY exit_time DESC LIMIT 20
            """, (mode,))
            rows = c.fetchall()
            count = 0
            for r in rows:
                if r["pnl"] < 0:
                    count += 1
                else:
                    break
            return count

    def get_daily_pnl(self, mode: str = "PAPER") -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        with self.get_cursor() as c:
            c.execute("""
                SELECT COALESCE(SUM(pnl), 0) as daily_pnl
                FROM positions
                WHERE mode=? AND status='CLOSED'
                AND DATE(exit_time)=?
            """, (mode, today))
            return c.fetchone()["daily_pnl"] or 0.0

    def get_trades_today(self, mode: str = "PAPER") -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        with self.get_cursor() as c:
            c.execute("""
                SELECT COUNT(*) as cnt FROM positions
                WHERE mode=? AND DATE(entry_time)=?
            """, (mode, today))
            return c.fetchone()["cnt"] or 0
