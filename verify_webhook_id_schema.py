"""
verify_webhook_id_schema.py
=============================
Confirms the webhook_id idempotency guard migration applied cleanly:
- positions.webhook_id and option_positions.webhook_id columns exist
- idx_positions_webhook_id and idx_option_positions_webhook_id unique
  indexes exist

Usage:
    python verify_webhook_id_schema.py
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]

conn = psycopg2.connect(DATABASE_URL, sslmode="require")
c = conn.cursor()

c.execute("""
    SELECT table_name, column_name
    FROM information_schema.columns
    WHERE table_name IN ('positions', 'option_positions')
      AND column_name = 'webhook_id'
""")
columns = c.fetchall()
print("webhook_id columns found:", columns)

c.execute("""
    SELECT indexname
    FROM pg_indexes
    WHERE indexname IN ('idx_positions_webhook_id', 'idx_option_positions_webhook_id')
""")
indexes = c.fetchall()
print("indexes found:", indexes)

conn.close()

if len(columns) == 2 and len(indexes) == 2:
    print("\nPASSED: schema migration applied cleanly.")
else:
    print("\nFAILED: expected 2 columns and 2 indexes.")
    print("Did app.py actually start once with the new database.py to run _init_db()?")
    