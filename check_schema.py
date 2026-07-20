"""
check_schema.py
================
One-off utility: lists the actual column names of option_positions
(and positions, for comparison) from Neon Postgres, so weekly_report.py
can be corrected to match the real schema.

Usage:
    python check_schema.py
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=8)
with conn.cursor() as c:
    for table in ["option_positions", "positions"]:
        c.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        rows = c.fetchall()
        print(f"\n=== {table} ===")
        for col, dtype in rows:
            print(f"  {col:<25} {dtype}")

conn.close()
