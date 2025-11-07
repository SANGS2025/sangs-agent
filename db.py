# ~/sangs-agent/db.py
import os
from dotenv import load_dotenv
from psycopg_pool import ConnectionPool

# Load .env, but allow it to override anything inherited from the shell
load_dotenv(override=True)

raw = os.getenv("DATABASE_URL", "").strip()
if not raw:
    raise RuntimeError("Missing DATABASE_URL in environment (.env)")

# Normalize SQLAlchemy-style driver tags to a plain psycopg DSN
# e.g. postgresql+psycopg://user@host/db  -> postgresql://user@host/db
if raw.startswith("postgresql+psycopg://"):
    raw = "postgresql://" + raw.split("postgresql+psycopg://", 1)[1]

# Optional sanity: quotes around the URL are a common mistake in .env
if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
    raw = raw[1:-1].strip()

POOL_DSN = raw

# Log what we will actually use (one-time, safe to print)
print(f"[db] Using DATABASE_URL: {POOL_DSN}")

# Build a small shared connection pool
pool = ConnectionPool(conninfo=POOL_DSN, min_size=1, max_size=5, kwargs={"autocommit": True})
