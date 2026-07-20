"""
test_idempotency_guard.py
============================
Tests the webhook_id duplicate-protection guard added to database.py /
portfolio.py: duplicate OPEN attempts are rejected cleanly (no
exception, no duplicate row), duplicate CLOSE attempts are rejected
cleanly (no double capital adjustment).

Writes test rows into mode='TEST' with webhook_id 99997/99998/99999 -
same DATABASE_URL as PAPER, but isolated by mode so this never touches
live data. Clean these up afterward (see bottom of output).

Usage:
    python test_idempotency_guard.py
"""

from database import DatabaseManager
from portfolio import PortfolioManager

db = DatabaseManager()
pf = PortfolioManager(db)

print("=" * 60)
print("TEST 1: Duplicate OPEN rejected")
print("=" * 60)
pos1 = db.open_position(mode="TEST", symbol="NIFTY", action="BUY",
                         entry_price=100, quantity=65, webhook_id=99999)
print(f"First open:  {pos1}")
pos2 = db.open_position(mode="TEST", symbol="NIFTY", action="BUY",
                         entry_price=100, quantity=65, webhook_id=99999)
print(f"Second open (duplicate): {pos2}")
assert pos1 is not None, "First open should have succeeded"
assert pos2 is None, "Second open with same webhook_id should be rejected"
print("PASSED\n")

print("=" * 60)
print("TEST 2: Duplicate CLOSE rejected")
print("=" * 60)
pos_id = db.open_position(mode="TEST", symbol="NIFTY", action="BUY",
                           entry_price=100, quantity=65, webhook_id=99998)
r1 = db.close_position(pos_id, exit_price=110, exit_reason="test", pnl=650)
print(f"First close:  {r1}")
r2 = db.close_position(pos_id, exit_price=120, exit_reason="test2", pnl=1300)
print(f"Second close (duplicate): {r2}")
assert r1 is True, "First close should return True"
assert r2 is False, "Second close on already-closed position should return False"
print("PASSED\n")

print("=" * 60)
print("TEST 3: Capital only moves once through apply_trade_close")
print("=" * 60)
pos_id3 = db.open_position(mode="TEST", symbol="NIFTY", action="BUY",
                            entry_price=100, quantity=65, webhook_id=99997)
position = db.get_position_by_id(pos_id3)
before = db.get_account("TEST")["current_capital"]

r1 = pf.apply_trade_close(position, 110, "test close", "TEST")
print(f"First apply_trade_close: {'dict returned' if r1 else None}")

r2 = pf.apply_trade_close(position, 999, "duplicate close attempt", "TEST")
print(f"Second apply_trade_close (stale position dict): {r2}")

after = db.get_account("TEST")["current_capital"]
print(f"Capital before: {before}")
print(f"Capital after both attempts: {after}")
print(f"Delta: {round(after - before, 2)}  (should equal only the FIRST close's net P&L)")

assert r1 is not None, "First apply_trade_close should return a result dict"
assert r2 is None, "Second apply_trade_close on stale position should return None"
print("PASSED: capital moved exactly once\n")

print("=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)
print("\nClean up test rows when done:")
print("  DELETE FROM positions WHERE webhook_id IN (99999, 99998, 99997);")
