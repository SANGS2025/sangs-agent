# ~/sangs-agent/main.py
import os
import asyncio
from dotenv import load_dotenv

# IMPORTANT: allow .env to override anything already in the shell
load_dotenv(override=True)

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

app = FastAPI(title="SANGS Internal Agent", version="0.1")

# --- CORS (admin @3000/3002 and verify @3001) ---
ALLOWED_ORIGINS = [
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:3001", "http://127.0.0.1:3001",
    "http://localhost:3002", "http://127.0.0.1:3002",
    "https://admin.sangs.co.za",
    "https://verify.sangs.co.za",
    "https://sangs-verify-etcztjzl1-sangss-projects.vercel.app",
    "https://*.vercel.app",  # Allow all Vercel preview deployments
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,               # we use Bearer tokens, not cookies
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# --- Routers ---
from auth import router as auth_router
from labels import router as labels_router
from consignments import router as consignments_router
from certs import router as certs_router
from admin_ingest import (
    router as ingest_router,
    labels_from_sheets,
    certs_from_sheets,
)
from public_certs import router as public_certs_router
from public_census import router as public_census_router

app.include_router(auth_router)
app.include_router(labels_router)
app.include_router(consignments_router)
app.include_router(certs_router)
app.include_router(ingest_router)
app.include_router(public_certs_router)
app.include_router(public_census_router)

@app.get("/health")
def health():
    return JSONResponse({"ok": True})

# --- Background scheduler (Sheets -> DB) ---
scheduler: BackgroundScheduler | None = None

def _start_scheduler():
    """Start APScheduler to periodically pull from Google Sheets CSV endpoints."""
    global scheduler
    if scheduler is not None:
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    interval = int(os.getenv("INGEST_INTERVAL_SECONDS", "600"))

    async def labels_job():
        try:
            class _U(dict): pass
            await labels_from_sheets(csv_url=None, user=_U(role="admin"))
        except Exception as e:
            print(f"[ingest] labels error: {e}")

    async def certs_job():
        try:
            class _U(dict): pass
            await certs_from_sheets(csv_url=None, user=_U(role="admin"))
        except Exception as e:
            print(f"[ingest] certs error: {e}")

    def _run_coro(coro):
        # Run on the server's loop if present; otherwise run a fresh loop.
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(coro)
        except RuntimeError:
            asyncio.run(coro)

    scheduler.add_job(
        lambda: _run_coro(labels_job()),
        trigger=IntervalTrigger(seconds=interval),
        id="labels_ingest",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _run_coro(certs_job()),
        trigger=IntervalTrigger(seconds=interval),
        id="certs_ingest",
        replace_existing=True,
    )

    scheduler.start()
    print(f"[ingest] scheduler started, every {interval}s")

@app.on_event("startup")
def _on_startup():
    _start_scheduler()

@app.on_event("shutdown")
def _on_shutdown():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None

