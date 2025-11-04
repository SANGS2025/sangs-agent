# --- stdlib & env ---
import os
from dotenv import load_dotenv

# Load .env for local dev (Render/Prod will inject real env vars)
load_dotenv()

# --- FastAPI ---
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# --- Postgres (psycopg) ---
import psycopg
from psycopg_pool import ConnectionPool

# Resolve DATABASE_URL early and fail fast if missing
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Put it in your .env for local dev, "
        "or set it in Render environment variables."
    )

# Initialize a small connection pool
db_pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=5,
    kwargs={"autocommit": True},
)

# Create the app
app = FastAPI()

# --- Basic health check (no DB) ---
@app.get("/health")
def health():
    return {"ok": True}

# --- DB ping (verifies pool & connectivity) ---
@app.get("/db/ping")
def db_ping():
    with db_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        one = cur.fetchone()[0]
        return {"ok": True, "one": one}

