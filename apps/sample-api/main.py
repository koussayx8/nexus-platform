"""NEXUS Sample API — FastAPI service for CI/CD pipeline validation."""

from fastapi import FastAPI
from datetime import datetime, timezone
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(
    title="NEXUS Sample API",
    description="Sample microservice for validating the NEXUS CI/CD pipeline",
    version="0.1.0",
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
        "version": "0.1.0",
    }


@app.get("/ready")
async def ready():
    """Readiness check endpoint for Kubernetes readiness probes."""
    return {"ready": True}
