"""NEXUS Sample API — FastAPI service for CI/CD pipeline validation."""

import logging
import os
import threading
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

VERSION = "0.2.0"

# /items budget: 0.3 s slot acquire + 2 s connect + 0.5 s statement = 2.8 s < 3 s.
# 5 slots per pod keep the worst case (2 envs x 7 pods x 5) at 70 connections,
# below the 97 non-superuser slots of max_connections=100.
DB_SLOTS = threading.BoundedSemaphore(5)
DB_SLOT_TIMEOUT_S = 0.3
DB_CONNECT_TIMEOUT_S = 2  # psycopg's minimum; applied per connection attempt
DB_STATEMENT_TIMEOUT_MS = 500

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("sample_api")


app = FastAPI(
    title="NEXUS Sample API",
    description="Sample microservice for validating the NEXUS CI/CD pipeline",
    version=VERSION,
)

Instrumentator().instrument(app).expose(app)


@app.get("/")
async def root():
    """Root endpoint returning service identity."""
    return {"service": "nexus-sample-api", "status": "running"}


@app.get("/health")
async def health():
    """Health check endpoint for Kubernetes liveness probes."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": VERSION,
    }


@app.get("/ready")
async def ready():
    """Readiness check endpoint for Kubernetes readiness probes."""
    return {"ready": True}


@app.get("/items")
def items():
    """Read items from the Dependency DB.

    A plain ``def`` on purpose: Starlette runs it in the threadpool, so a
    blocking connect never stalls the event loop or ``/ready``. A new
    connection per request (no pool) lets the server's error reach the log.
    """
    if not DB_SLOTS.acquire(timeout=DB_SLOT_TIMEOUT_S):
        logger.error("db_slots_exhausted")
        return JSONResponse(status_code=503, content={"error": "db_slots_exhausted"})
    try:
        with psycopg.connect(
            host=os.environ.get("DB_HOST"),
            dbname=os.environ.get("DB_NAME"),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
            connect_timeout=DB_CONNECT_TIMEOUT_S,
            options=f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",
        ) as conn:
            rows = conn.execute("SELECT id, name FROM items ORDER BY id").fetchall()
    except psycopg.Error as exc:
        logger.error(
            "db_error type=%s sqlstate=%s message=%s",
            type(exc).__name__,
            exc.sqlstate,
            exc,
        )
        return JSONResponse(status_code=503, content={"error": "db_unavailable"})
    finally:
        DB_SLOTS.release()
    return {"items": [{"id": row[0], "name": row[1]} for row in rows]}
