#!/usr/bin/env python3
# Run the public verification schema migration
import os
from db import pool

# Read the migration SQL file
with open("db_migrations/public_verification_schema.sql", "r") as f:
    sql = f.read()

# Split by semicolons and execute each statement
statements = [s.strip() + ";" for s in sql.split(";") if s.strip()]

print(f"Executing {len(statements)} SQL statements...")

with pool.connection() as conn, conn.cursor() as cur:
    for i, stmt in enumerate(statements, 1):
        try:
            cur.execute(stmt)
            print(f"✓ Statement {i}/{len(statements)} executed")
        except Exception as e:
            # Some statements might fail if they already exist (CREATE IF NOT EXISTS)
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                print(f"⚠ Statement {i}/{len(statements)} skipped (already exists)")
            else:
                print(f"✗ Statement {i}/{len(statements)} failed: {e}")
                raise

print("\n✅ Migration completed!")

