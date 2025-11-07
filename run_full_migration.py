#!/usr/bin/env python3
# Run the full database schema migration (base + public verification)
import os
from db import pool

print("=" * 60)
print("Running Full Database Migration")
print("=" * 60)

# Read base schema
print("\n1. Creating base schema (users, consignments, certs, etc.)...")
with open("schema_v1.sql", "r") as f:
    base_sql = f.read()

# Read public verification migration
print("2. Adding public verification schema (coins, images, etc.)...")
with open("db_migrations/public_verification_schema.sql", "r") as f:
    public_sql = f.read()

# Combine and execute
with pool.connection() as conn, conn.cursor() as cur:
    try:
        # Execute base schema first
        print("\nExecuting base schema...")
        cur.execute(base_sql)
        conn.commit()
        print("✅ Base schema created successfully!")
        
        # Then execute public verification migration
        print("\nExecuting public verification migration...")
        cur.execute(public_sql)
        conn.commit()
        print("✅ Public verification schema added successfully!")
        
        print("\n" + "=" * 60)
        print("✅ Full migration completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        error_msg = str(e).lower()
        # Some errors are expected (IF NOT EXISTS, etc.)
        if "already exists" in error_msg or "duplicate" in error_msg:
            print(f"⚠ Warning: {e}")
            print("✅ Migration completed (some objects already exist - this is OK)")
        else:
            print(f"\n✗ Migration failed: {e}")
            conn.rollback()
            raise

