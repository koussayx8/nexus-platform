"""Tests for the NEXUS Sample API."""

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "nexus-sample-api"
    assert data["status"] == "running"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert data["version"] == "0.1.0"


def test_ready():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True
