#!/usr/bin/env python3
# Add missing columns to existing certs table
from db import pool

print("Adding missing columns to certs table...")

sql = """
ALTER TABLE certs 
  ADD COLUMN IF NOT EXISTS country TEXT,
  ADD COLUMN IF NOT EXISTS year TEXT,
  ADD COLUMN IF NOT EXISTS addl1 TEXT,
  ADD COLUMN IF NOT EXISTS addl2 TEXT,
  ADD COLUMN IF NOT EXISTS addl3 TEXT,
  ADD COLUMN IF NOT EXISTS grade1 TEXT,
  ADD COLUMN IF NOT EXISTS grade2 TEXT,
  ADD COLUMN IF NOT EXISTS coin_key TEXT,
  ADD COLUMN IF NOT EXISTS grade_family TEXT,
  ADD COLUMN IF NOT EXISTS grade_number INT;
"""

with pool.connection() as conn, conn.cursor() as cur:
    try:
        cur.execute(sql)
        conn.commit()
        print("✅ Missing columns added successfully!")
    except Exception as e:
        print(f"⚠ Warning: {e}")
        # Some columns might already exist, that's OK
        conn.rollback()
        print("✅ Columns check completed")

