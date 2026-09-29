"""Tests for the NEXUS Sample API."""

import inspect
import logging
import threading

import main
import psycopg
import pytest
from fastapi.testclient import TestClient
from main import app
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client.utils import floatToGoString
from psycopg.conninfo import conninfo_to_dict, make_conninfo

client = TestClient(app)

NOLOGIN_MESSAGE = (
    'connection failed: connection to server at "10.43.0.10", port 5432 failed: '
    'FATAL:  role "app_dev" is not permitted to log in'
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query):
        return FakeCursor(self._rows)


@pytest.fixture
def connect_calls(monkeypatch):
    """Record every psycopg.connect call and return two rows."""
    calls = []

    def fake_connect(*args, **kwargs):
        calls.append(kwargs)
        return FakeConnection([(1, "alpha"), (2, "beta")])

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return calls


@pytest.fixture
def connect_nologin(monkeypatch):
    """Make psycopg.connect fail the way it does when the role is NOLOGIN."""
    calls = []

    def fake_connect(*args, **kwargs):
        calls.append(kwargs)
        raise psycopg.OperationalError(NOLOGIN_MESSAGE)

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return calls


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
    assert data["version"] == "0.2.0"


def test_ready():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_items_returns_rows(connect_calls):
    response = client.get("/items")
    assert response.status_code == 200
    assert response.json() == {
        "items": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
    }
    assert len(connect_calls) == 1


def test_items_nologin_returns_503_and_logs_server_message(connect_nologin, caplog):
    with caplog.at_level(logging.ERROR, logger="sample_api"):
        response = client.get("/items")
    assert response.status_code == 503
    assert response.json() == {"error": "db_unavailable"}
    assert 'role "app_dev" is not permitted to log in' in caplog.text
    assert "OperationalError" in caplog.text


def test_ready_stays_200_while_connect_fails(connect_nologin):
    assert client.get("/items").status_code == 503
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_items_conninfo_has_timeouts(connect_calls):
    client.get("/items")
    conninfo = conninfo_to_dict(make_conninfo("", **connect_calls[0]))
    assert conninfo["connect_timeout"] == "2"
    assert "statement_timeout=500" in conninfo["options"]


def test_items_is_a_plain_def():
    assert not inspect.iscoroutinefunction(main.items)


def test_items_slots_exhausted_returns_503_without_connecting(
    connect_calls, monkeypatch, caplog
):
    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()
    monkeypatch.setattr(main, "DB_SLOTS", exhausted)
    with caplog.at_level(logging.ERROR, logger="sample_api"):
        response = client.get("/items")
    assert response.status_code == 503
    assert response.json() == {"error": "db_slots_exhausted"}
    assert "db_slots_exhausted" in caplog.text
    assert connect_calls == []


def test_items_multiline_error_is_logged_on_one_line(monkeypatch, caplog):
    def fake_connect(*args, **kwargs):
        raise psycopg.OperationalError(NOLOGIN_MESSAGE + "\nDETAIL: forged\r\nline")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    with caplog.at_level(logging.ERROR, logger="sample_api"):
        response = client.get("/items")
    assert response.status_code == 503
    [record] = [r for r in caplog.records if r.name == "sample_api"]
    message = record.getMessage()
    assert "\n" not in message
    assert "\r" not in message
    assert "not permitted to log in\\nDETAIL: forged\\r\\nline" in message


# --- metrics (ADR-022) ---


def _families():
    return {
        f.name: f for f in text_string_to_metric_families(client.get("/metrics").text)
    }


def test_metrics_is_served_async():
    [route] = [r for r in app.routes if getattr(r, "path", None) == "/metrics"]
    assert inspect.iscoroutinefunction(route.endpoint)


def test_metrics_has_the_inflight_gauge_per_handler():
    client.get("/health")
    gauge = _families()["http_requests_inprogress"]
    assert gauge.type == "gauge"
    assert {"handler": "/health", "method": "GET"} in [s.labels for s in gauge.samples]


def test_latency_histogram_has_the_finer_buckets():
    client.get("/health")
    histogram = _families()["http_request_duration_seconds"]
    les = {
        s.labels["le"]
        for s in histogram.samples
        if s.name.endswith("_bucket") and s.labels["handler"] == "/health"
    }
    assert les == {floatToGoString(b) for b in main.LATENCY_BUCKETS} | {"+Inf"}
    assert "0.005" in les and "0.075" in les
