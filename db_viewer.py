#!/usr/bin/env python3
"""DB Viewer - lists all tables, columns, and first 3 rows (Postgres)"""

import os
import sys

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "")

if len(sys.argv) > 1:
    DATABASE_URL = sys.argv[1]

if not DATABASE_URL:
    print("DATABASE_URL not set. Pass it as an argument or set the env var.")
    sys.exit(1)

conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
print(f"Connected to Postgres\n")

cursor.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema='public' ORDER BY table_name
""")
tables = cursor.fetchall()

if not tables:
    print("No tables found.")
else:
    for row in tables:
        table_name = row["table_name"]
        print(f"\nTable: {table_name}")
        print("-" * 40)
        cursor.execute("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
        """, (table_name,))
        cols = cursor.fetchall()
        print("Columns: " + ", ".join(f"{c['column_name']}({c['data_type']})" for c in cols))
        cursor.execute(f'SELECT COUNT(*) as cnt FROM "{table_name}"')
        count = cursor.fetchone()["cnt"]
        print(f"Rows: {count}")
        if count > 0:
            cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 3')
            for r in cursor.fetchall():
                print(f"  {dict(r)}")

cursor.close()
conn.close()

