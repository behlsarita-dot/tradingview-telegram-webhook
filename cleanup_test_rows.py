"""
cleanup_test_rows.py
======================
Removes the test rows created by test_idempotency_guard.py and
webhook_persistence.py (webhook_id 99999/99998/99997, plus any
mode='TEST' pending_webhooks rows from testing).

Usage:
    python cleanup_test_rows.py
"""

from database import DatabaseManager

db = DatabaseManager()

with db.get_cursor() as c:
    c.execute("DELETE FROM positions WHERE webhook_id IN (99999, 99998, 99997) RETURNING id")
    deleted_positions = c.fetchall()
    print(f"Deleted {len(deleted_positions)} test position(s)")

    c.execute("DELETE FROM option_positions WHERE webhook_id IN (99999, 99998, 99997) RETURNING id")
    deleted_options = c.fetchall()
    print(f"Deleted {len(deleted_options)} test option position(s)")

    c.execute("DELETE FROM pending_webhooks WHERE mode='TEST' RETURNING id")
    deleted_webhooks = c.fetchall()
    print(f"Deleted {len(deleted_webhooks)} test pending_webhooks row(s)")

print("\nDone. Local TEST-mode test artifacts cleaned up.")