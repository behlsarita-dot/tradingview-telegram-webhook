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
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import psycopg2
import psycopg2.extras
import psycopg2.errors

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
        # NEW (2026-08-14): dedicated connection + lock for enqueue_webhook()
        # only - see _get_enqueue_connection() docstring below for why this
        # one method gets different treatment from every other get_cursor()
        # call in this file.
        self._enqueue_conn = None
        self._enqueue_conn_lock = threading.Lock()

    @contextmanager
    def get_cursor(self):
        # A fresh connection per call (matches the original SQLite
        # pattern of one connection per operation). This is deliberately
        # NOT pooled - Neon's free tier scales compute to zero after ~5
        # min idle, so a short-lived connection per request plays nicely
        # with that rather than fighting it with a long-lived pool.
        #
        # connect_timeout added 2026-07-14: a suspended/slow Neon endpoint
        # should fail fast and loud (raising here, which app.py's webhook
        # handler now catches on a background thread and reports over
        # Telegram) rather than hang indefinitely. This does not fix
        # TradingView's 3-second webhook timeout on its own - see the
        # comment above api_webhook() in app.py for the actual fix
        # (respond to TradingView immediately, do this connection/query
        # work afterwards on a background thread).
        conn = psycopg2.connect(self.db_url, sslmode="require", connect_timeout=8)
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

            # NEW (2026-07-20): persisted webhook queue. Replaces the
            # in-memory queue.Queue() that used to live in app.py.
            # That queue held un-processed webhooks only in process
            # memory between the instant TradingView got its 200 and
            # the moment the background worker actually processed the
            # trade - so any deploy, gunicorn worker recycle, or crash
            # in that window silently dropped the trade with zero log
            # trace. This table makes "received but not yet processed"
            # a durable DB row instead, so a restart can pick up where
            # it left off. See PATCH_NOTES.md for the one residual edge
            # case (crash between claim and mark-processed -> possible
            # duplicate reprocessing, not loss) - which the webhook_id
            # unique-index guard below now closes at the positions
            # table level too.
            c.execute("""
                CREATE TABLE IF NOT EXISTS pending_webhooks (
                    id SERIAL PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'PAPER',
                    action TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL,
                    claimed_at TEXT,
                    processed_at TEXT,
                    error TEXT
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_pending_webhooks_status
                ON pending_webhooks (mode, status, id)
            """)

            # Postgres supports ADD COLUMN IF NOT EXISTS directly, so no
            # need for the old SQLite PRAGMA table_info() + manual check.
            c.execute("ALTER TABLE account ADD COLUMN IF NOT EXISTS trading_enabled INTEGER DEFAULT 1")
            c.execute("ALTER TABLE account ADD COLUMN IF NOT EXISTS kill_switch_reason TEXT")
            c.execute("ALTER TABLE account ADD COLUMN IF NOT EXISTS peak_capital REAL")

            # NEW (2026-07-20): idempotency guard for duplicate webhook
            # processing. Two distinct races motivate this:
            #   1. Crash between claim_next_pending_webhook() and
            #      mark_webhook_processed() -> recover_stuck_webhooks()
            #      correctly requeues the row, but if the original
            #      attempt actually did complete the DB write just before
            #      dying, re-processing would double-open/close a trade.
            #   2. Under multiple gunicorn workers, one worker's restart
            #      can reset a genuinely-still-processing (not dead) row
            #      to PENDING purely because recover_stuck_webhooks()'s
            #      60s staleness window raced against a slow Neon cold
            #      connect on another worker - see chat history
            #      2026-07-20. That row can then be claimed a second time
            #      by either worker while the first is still finishing.
            # webhook_id ties every position back to the pending_webhooks
            # row that created it. The partial unique index (only
            # enforced when webhook_id IS NOT NULL) means manual/legacy
            # rows with no webhook_id are unaffected, but a second INSERT
            # attempt for the same webhook_id fails loudly at the DB
            # layer instead of silently duplicating the trade.
            c.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS webhook_id INTEGER")
            c.execute("ALTER TABLE option_positions ADD COLUMN IF NOT EXISTS webhook_id INTEGER")

            c.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_webhook_id
                ON positions (webhook_id) WHERE webhook_id IS NOT NULL
            """)
            c.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_option_positions_webhook_id
                ON option_positions (webhook_id) WHERE webhook_id IS NOT NULL
            """)

            # Bootstrap the account row for whatever mode this process is
            # running as (PAPER by default, but TEST when a local dev
            # server is isolated from production - see TRADING_MODE in
            # config.py). Previously hardcoded to 'PAPER', which meant a
            # TEST-mode server would find no account row on first boot
            # and every webhook would fail with "Account not found"
            # instead of cleanly isolating from production data.
            c.execute("SELECT COUNT(*) as cnt FROM account WHERE mode=%s", (TRADING_MODE,))
            if c.fetchone()["cnt"] == 0:
                now = datetime.now().isoformat()
                c.execute("""
                    INSERT INTO account
                        (mode, initial_capital, current_capital, total_pnl,
                         daily_pnl, open_positions, total_trades,
                         winning_trades, losing_trades, trading_enabled,
                         peak_capital, created_at, updated_at)
                    VALUES (%s, %s, %s, 0, 0, 0, 0, 0, 0, 1, %s, %s, %s)
                """, (TRADING_MODE, INITIAL_CAPITAL, INITIAL_CAPITAL, INITIAL_CAPITAL, now, now))
                logger.info(f"{TRADING_MODE} account initialised with Rs.{INITIAL_CAPITAL:,.2f}")

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

    def apply_capital_delta(self, mode: str, net_pnl: float) -> Dict:
        """FIX (2026-07-28): atomic replacement for the old
        get_account() -> mutate in Python -> update_account() pattern
        used by PortfolioManager.apply_trade_close(). That pattern reads
        current_capital/total_pnl/winning_trades/losing_trades into
        Python, computes new values, then writes them back in a SEPARATE
        connection/transaction. Two positions closing at nearly the same
        moment (very possible with the webhook worker thread plus e.g.
        EOD close iterating several positions back-to-back) can both read
        the same starting current_capital before either writes - a
        classic lost-update race. Whichever UPDATE lands second silently
        overwrites the first, and one trade's P&L quietly vanishes from
        the account totals even though the position row itself is
        correctly closed.

        This does the read-modify-write as a single UPDATE statement
        (current_capital = current_capital + %s, etc.), so Postgres's own
        row-level locking makes the increment atomic regardless of how
        many callers race for it. Returns the resulting row so callers
        that need the new values (none currently do, but kept for
        parity with update_account()) don't need a second query.
        """
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                UPDATE account
                SET current_capital = current_capital + %s,
                    total_pnl = total_pnl + %s,
                    winning_trades = winning_trades + %s,
                    losing_trades = losing_trades + %s,
                    updated_at = %s
                WHERE mode=%s
                RETURNING current_capital, total_pnl, winning_trades, losing_trades
            """, (
                net_pnl, net_pnl,
                1 if net_pnl >= 0 else 0,
                1 if net_pnl < 0 else 0,
                now, mode
            ))
            row = c.fetchone()
            return dict(row) if row else {}

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
    # PENDING WEBHOOKS (persisted queue — replaces in-memory queue.Queue())
    # ------------------------------------------------------------------ #

    def _get_enqueue_connection(self):
        """Returns a persistent connection reserved for enqueue_webhook()
        only, reused across calls instead of opening a fresh one every
        time (see get_cursor() above for why every OTHER method still
        deliberately does open fresh, unpooled connections - that
        reasoning still holds everywhere else).

        NEW (2026-08-14): confirmed live on NIFTY260818P24500/C24700,
        2026-08-11 - three separate webhook deliveries (09:20, 15:05,
        15:20 IST) all timed out client-side on TradingView's end, despite
        the underlying pending_webhooks INSERT completing correctly
        seconds later (see check_pending_webhook.py ids 75/76/77 - all
        status=PROCESSED, no data lost). All three happened well after
        market open, ruling out overnight/weekend cold start as the sole
        cause (the 08:58 IST _warm_neon_job in app.py already covers
        that case), and gunicorn.conf.py already runs threads=4 with
        healthy queue latency elsewhere in the system, ruling out thread
        contention too. The remaining variable is get_cursor()'s per-call
        psycopg2.connect(): enqueue_webhook() is the one synchronous DB
        call sitting directly inside TradingView's 3-second window (see
        FIX 2026-07-14 above api_webhook() in app.py), and a fresh
        TCP+TLS+auth handshake has no floor on how long it can
        occasionally take - enough, apparently, to exceed 3s even on an
        already-warm compute.

        This is intentionally narrow: ONLY enqueue_webhook() uses this.
        It's still a single connection, not a pool sized for concurrency,
        so the compute-hour cost is minimal - during a long idle stretch
        (overnight/weekend) Neon may still drop or suspend this
        connection same as any other; the code below just detects that
        and reconnects once, lazily, on the next call, rather than paying
        a fresh handshake on every single call the way get_cursor() does.
        Guarded by self._enqueue_conn_lock because gunicorn.conf.py runs
        threads=4, so more than one Flask request thread can call
        enqueue_webhook() concurrently, and a psycopg2 connection is not
        safe for concurrent use from multiple threads at once."""
        if self._enqueue_conn is not None:
            try:
                if self._enqueue_conn.closed == 0:
                    return self._enqueue_conn
            except Exception:
                pass
            try:
                self._enqueue_conn.close()
            except Exception:
                pass
            self._enqueue_conn = None

        # TEMP INSTRUMENTATION (2026-08-14): timing a fresh connect so we
        # can see cold-start/handshake cost directly in logs whenever this
        # branch is hit (first call, or reconnect after a dropped/suspended
        # connection) rather than only inferring it from a timeout. Remove
        # once the persistent-connection fix above is confirmed sufficient.
        _t0 = time.monotonic()
        self._enqueue_conn = psycopg2.connect(
            self.db_url, sslmode="require", connect_timeout=8
        )
        _elapsed = time.monotonic() - _t0
        logger.info(f"WEBHOOK_TIMING fresh_connect={_elapsed:.2f}s")
        return self._enqueue_conn

    def enqueue_webhook(self, mode: str, action: str, symbol: str, payload: dict) -> int:
        """Called synchronously from /api/webhook, before the 200 is
        returned. A single INSERT - no risk validation, no position
        writes - so it stays fast enough to not reintroduce the
        TradingView 3-second timeout the background-thread fix (2026-07-14)
        was built to avoid.

        UPDATED (2026-08-14): uses a dedicated, lazily-reconnecting
        persistent connection (_get_enqueue_connection()) instead of
        get_cursor()'s per-call psycopg2.connect(), specifically because
        this call sits directly inside TradingView's 3-second window - see
        _get_enqueue_connection() docstring for the full reasoning and the
        2026-08-11 evidence that motivated this change. Every other method
        in this file is unaffected and keeps using get_cursor() as before."""
        now = datetime.now().isoformat()
        # TEMP INSTRUMENTATION (2026-08-14): wall-clock timing around
        # connection acquisition vs. the query itself, so a slow call
        # shows up in logs as WEBHOOK_TIMING with a breakdown, instead of
        # only surfacing as a client-side timeout on TradingView's end
        # with no visibility into which part was slow. Remove once the
        # persistent-connection fix is confirmed sufficient over a few
        # trading days.
        _t_start = time.monotonic()
        with self._enqueue_conn_lock:
            _t_conn0 = time.monotonic()
            conn = self._get_enqueue_connection()
            _conn_time = time.monotonic() - _t_conn0
            cursor = None
            try:
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                _t_query0 = time.monotonic()
                cursor.execute("""
                    INSERT INTO pending_webhooks (mode, action, symbol, payload, status, created_at)
                    VALUES (%s, %s, %s, %s, 'PENDING', %s)
                    RETURNING id
                """, (mode, action, symbol, json.dumps(payload), now))
                row = cursor.fetchone()
                conn.commit()
                _query_time = time.monotonic() - _t_query0
                _total = time.monotonic() - _t_start
                logger.info(
                    f"WEBHOOK_TIMING conn_acquire={_conn_time:.2f}s "
                    f"query={_query_time:.2f}s total={_total:.2f}s "
                    f"webhook_id={row['id']}"
                )
                return row["id"]
            except Exception:
                # Connection may be broken (e.g. Neon dropped it after a
                # long idle period, or a network blip) - roll back, close
                # it, and clear it so the NEXT call reconnects fresh
                # rather than repeatedly failing against a dead
                # connection. Re-raised so app.py's existing exception
                # handling in api_webhook() is unchanged.
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                self._enqueue_conn = None
                raise
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:
                        pass

    def claim_next_pending_webhook(self, mode: str = "PAPER") -> Optional[Dict]:
        """Atomically claims the oldest PENDING row for this mode and
        flips it to PROCESSING, using FOR UPDATE SKIP LOCKED so this is
        safe to call from more than one process/worker concurrently
        (e.g. if gunicorn ever runs multiple workers) without two
        workers claiming the same row."""
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                SELECT id, action, symbol, payload FROM pending_webhooks
                WHERE mode=%s AND status='PENDING'
                ORDER BY id ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """, (mode,))
            row = c.fetchone()
            if not row:
                return None
            c.execute("""
                UPDATE pending_webhooks SET status='PROCESSING', claimed_at=%s WHERE id=%s
            """, (now, row["id"]))
            return {
                "id": row["id"],
                "action": row["action"],
                "symbol": row["symbol"],
                "payload": row["payload"],  # psycopg2 decodes JSONB to dict automatically
            }

    def mark_webhook_processed(self, webhook_id: int):
        with self.get_cursor() as c:
            c.execute("""
                UPDATE pending_webhooks SET status='PROCESSED', processed_at=%s WHERE id=%s
            """, (datetime.now().isoformat(), webhook_id))

    def mark_webhook_failed(self, webhook_id: int, error: str):
        with self.get_cursor() as c:
            c.execute("""
                UPDATE pending_webhooks SET status='FAILED', processed_at=%s, error=%s WHERE id=%s
            """, (datetime.now().isoformat(), str(error)[:2000], webhook_id))

    def recover_stuck_webhooks(self, mode: str = "PAPER", stale_after_seconds: int = 60) -> int:
        """Call once at process startup, before the worker thread starts.
        Any row still PROCESSING from a previous process that died
        mid-flight (deploy, crash, worker recycle) gets reset to PENDING
        so it's picked up again instead of being lost forever. Returns
        the number of rows recovered, so app.py can log/Telegram it -
        that count is your only visibility into "a restart actually
        interrupted a webhook," which previously didn't exist at all.

        NOTE: a row reset here may occasionally have actually still been
        alive on another worker (slow Neon cold-connect racing against
        this 60s window under multi-worker gunicorn), not truly dead.
        That's a real possibility, not just the crash case this was
        originally written for - see chat history 2026-07-20. The
        webhook_id unique-index guard on positions/option_positions is
        what makes a second claim-and-process attempt safe either way:
        it fails as a clean no-op instead of a duplicate trade.

        FIX (2026-07-20): originally compared claimed_at against
        Postgres's NOW(), which is timezone-aware (UTC on Neon) while
        claimed_at is a naive local-clock timestamp (IST on the machine
        writing it). Postgres implicitly treated the naive value as if
        it were already in UTC, shifting it 5.5 hours forward and making
        every row look "claimed in the future" relative to true UTC now
        - so the old query always matched zero rows, silently, with no
        error and no log line (since `if _recovered:` is falsy on 0).
        Confirmed via test_webhook_persistence.py --setup-stale/--verify:
        a row claimed 90s ago was never recovered on restart under the
        old NOW()-based query. Fix: compute the cutoff in Python using
        the same naive-clock convention already used for
        entry_time/exit_time/created_at everywhere else in this file,
        and compare two naive strings directly - no timezone cast for
        Postgres to get wrong."""
        cutoff = (datetime.now() - timedelta(seconds=stale_after_seconds)).isoformat()
        with self.get_cursor() as c:
            c.execute("""
                UPDATE pending_webhooks
                SET status='PENDING', claimed_at=NULL
                WHERE mode=%s AND status='PROCESSING'
                  AND claimed_at < %s
                RETURNING id
            """, (mode, cutoff))
            return len(c.fetchall())

    # ------------------------------------------------------------------ #
    # SHARED HELPERS
    # ------------------------------------------------------------------ #

    def _get_recent_closed_trades(self, c, mode: str, limit: int = 20) -> List[Dict]:
        """Shared by get_validation_snapshot() and get_consecutive_losses()
        so the two never drift apart again (see 2026-07-10 fix history,
        where get_consecutive_losses() was originally missing option
        trades entirely). Takes an already-open cursor `c` so callers
        control the connection lifecycle - this does NOT open its own
        get_cursor() block, and must always be called from inside one.

        FIX (2026-08-03): previously had no date filter at all - it
        ordered by exit_time DESC across ALL history for the mode, with
        no lower bound. On a fresh process restart with zero trades
        closed yet today, this meant the 3 most recent CLOSED trades
        from days (even weeks) ago silently determined today's
        consecutive-loss count before a single trade had happened in the
        new session. Confirmed live 2026-08-03 09:20 IST: bot restarted
        at 08:41, and _check_circuit_breakers() tripped at 09:20:13 off
        three PAPER-mode losses from 2026-07-22/23/24 - the CE24400
        BUY_OPTION at 09:20:01 was silently rejected inside
        validate_new_trade() as a result, despite the webhook itself
        returning 200. Since no new trade could ever open (rejected
        before it could close and push the stale losses out of the
        window), this was a self-sustaining lockout with no automatic
        recovery.

        Now scoped to exit_time falling on today's calendar date, same
        DATE(exit_time::timestamp)=%s pattern already used in
        get_daily_pnl() / get_trades_today() / get_validation_snapshot(),
        so the loss streak - and the circuit breaker it feeds - resets
        naturally every day instead of reaching back into arbitrary
        history."""
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
            SELECT pnl - total_charges as net_pnl, exit_time FROM (
                SELECT pnl, total_charges, exit_time FROM positions
                WHERE mode=%s AND status='CLOSED'
                    AND exit_time IS NOT NULL AND DATE(exit_time::timestamp)=%s
                UNION ALL
                SELECT pnl, total_charges, exit_time FROM option_positions
                WHERE mode=%s AND status='CLOSED'
                    AND exit_time IS NOT NULL AND DATE(exit_time::timestamp)=%s
            ) t
            ORDER BY exit_time DESC LIMIT %s
        """, (mode, today, mode, today, limit))
        return c.fetchall()

    @staticmethod
    def _count_consecutive_losses(rows: List[Dict]) -> int:
        """Counts losses from the most recent trade backwards, stopping
        at the first non-loss. Shared by get_validation_snapshot() and
        get_consecutive_losses() - keep this as the single source of
        truth for the "what counts as a loss streak" definition."""
        count = 0
        for r in rows:
            if r["net_pnl"] < 0:
                count += 1
            else:
                break
        return count

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

            closed_rows = self._get_recent_closed_trades(c, mode, limit=20)

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

        consecutive_losses = self._count_consecutive_losses(closed_rows)

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
                      adx: float = None, confluence_score: int = 0,
                      webhook_id: int = None) -> Optional[str]:
        """Returns None (instead of raising) if webhook_id was already
        used to open a position — this is the expected, correct outcome
        of a duplicate-claim race (see recover_stuck_webhooks() docstring
        and idx_positions_webhook_id in _init_db()), not an error. Callers
        must check for None and skip notify_trade_open() in that case."""
        pos_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        try:
            with self.get_cursor() as c:
                c.execute("""
                    INSERT INTO positions
                        (id, mode, symbol, action, entry_price, quantity,
                         stop_loss, take_profit, entry_charges, status,
                         entry_time, atr, adx, confluence_score, webhook_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, %s)
                """, (pos_id, mode, symbol, action, entry_price, quantity,
                      stop_loss, take_profit, entry_charges, now,
                      atr, adx, confluence_score, webhook_id))
            return pos_id
        except psycopg2.errors.UniqueViolation:
            logger.warning(f"Duplicate OPEN for webhook_id={webhook_id} ignored (already processed)")
            return None

    def close_position(self, pos_id: str, exit_price: float,
                       exit_reason: str, pnl: float,
                       exit_charges: float = 0.0) -> bool:
        """Returns False if the position was already CLOSED by the time
        this ran (i.e. a second worker/retry lost the race) — caller
        (PortfolioManager.apply_trade_close) must check this and skip
        the account capital update rather than treating it as a normal
        close."""
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                UPDATE positions
                SET status='CLOSED', exit_price=%s, exit_time=%s,
                    exit_reason=%s, pnl=%s, exit_charges=%s,
                    total_charges=entry_charges+%s
                WHERE id=%s AND status='OPEN'
            """, (exit_price, now, exit_reason, pnl,
                  exit_charges, exit_charges, pos_id))
            return c.rowcount == 1

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
                             entry_charges: float = 0.0,
                             webhook_id: int = None) -> Optional[str]:
        """Returns None (instead of raising) if webhook_id was already
        used to open a position — mirrors open_position()'s
        UniqueViolation handling. See idx_option_positions_webhook_id in
        _init_db()."""
        pos_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        try:
            with self.get_cursor() as c:
                c.execute("""
                    INSERT INTO option_positions
                        (id, mode, underlying, option_symbol, option_type,
                         strike, expiry, premium, quantity, stop_loss,
                         take_profit, entry_charges, status, entry_time,
                         webhook_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s)
                """, (pos_id, mode, underlying, option_symbol, option_type,
                      strike, expiry, premium, quantity, stop_loss,
                      take_profit, entry_charges, now, webhook_id))
            return pos_id
        except psycopg2.errors.UniqueViolation:
            logger.warning(f"Duplicate BUY_OPTION for webhook_id={webhook_id} ignored (already processed)")
            return None

    def close_option_position(self, pos_id: str, exit_premium: float,
                              exit_reason: str, pnl: float,
                              exit_charges: float = 0.0) -> bool:
        """Returns False if the option position was already CLOSED by
        the time this ran — same race-guard semantics as close_position()."""
        now = datetime.now().isoformat()
        with self.get_cursor() as c:
            c.execute("""
                UPDATE option_positions
                SET status='CLOSED', exit_premium=%s, exit_time=%s,
                    exit_reason=%s, pnl=%s, exit_charges=%s,
                    total_charges=entry_charges+%s
                WHERE id=%s AND status='OPEN'
            """, (exit_premium, now, exit_reason, pnl,
                  exit_charges, exit_charges, pos_id))
            return c.rowcount == 1

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
        # Still used standalone by get_risk_report(), which does not go
        # through get_validation_snapshot(). Delegates to the same
        # shared helpers get_validation_snapshot() uses, so the two
        # can never silently drift apart again (see 2026-07-10 fix
        # history: this used to query `positions` only, missing every
        # option trade - the only kind the live bot actually places).
        # As of 2026-08-03, also delegates the date-scoping fix in
        # _get_recent_closed_trades() - see that method's docstring.
        with self.get_cursor() as c:
            rows = self._get_recent_closed_trades(c, mode, limit=20)
        return self._count_consecutive_losses(rows)

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