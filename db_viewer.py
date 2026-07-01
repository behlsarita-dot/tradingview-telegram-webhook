#!/usr/bin/env python3
"""DB Viewer - lists all tables, columns, and first 5 rows"""

import sqlite3, os, sys

DB_PATH = os.getenv("DB_FILE", "trading_bot.db")

if len(sys.argv) > 1:
    DB_PATH = sys.argv[1]

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
print(f"Connected to {DB_PATH}\n")

cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

if not tables:
    print("No tables found.")
else:
    for (table_name,) in tables:
        print(f"\nTable: {table_name}")
        print("-" * 40)
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = cursor.fetchall()
        print("Columns: " + ", ".join(f"{c[1]}({c[2]})" for c in cols))
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"Rows: {count}")
        if count > 0:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
            for row in cursor.fetchall():
                print(f"  {row}")

conn.close()
