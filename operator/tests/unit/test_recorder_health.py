"""Recorder health check (D4): SELECT 1 every 30 s keeps nexus_operator_recorder_up current while
idle and counts failures in nexus_operator_recorder_errors_total, without touching the act path.
Offline: the database connection is replaced by a fake."""

import asyncio

import pytest

from nexus_operator import metrics
from nexus_operator.recorder.act import ReconnectingRecorder, health_loop


def make(tmp_path, password="pw\n"):
    pw = tmp_path / "password"
    if password is not None:
        pw.write_text(password)
    return ReconnectingRecorder(
        host="h", port=1, database="d", user="u", password_file=str(pw)
    )


def errors() -> float:
    return metrics.METRICS.value(metrics.RECORDER_ERRORS)


def test_probe_success_sets_up(tmp_path, monkeypatch):
    rec = make(tmp_path)
    seen = []

    async def ok(password, timeout_s):
        seen.append(password)

    monkeypatch.setattr(rec, "_select_one", ok)
    before = errors()
    assert asyncio.run(rec.probe()) is True
    assert rec.up is True and errors() == before and seen == ["pw"]


@pytest.mark.parametrize("failure", ["connect", "hang", "missing", "empty"])
def test_probe_failure_clears_up_and_counts(tmp_path, monkeypatch, failure):
    rec = make(
        tmp_path, None if failure == "missing" else "\n" if failure == "empty" else "pw"
    )
    rec.up = True

    async def fail(password, timeout_s):
        if failure == "hang":
            await asyncio.sleep(60)
        raise OSError("down")

    monkeypatch.setattr(rec, "_select_one", fail)
    before = errors()
    assert asyncio.run(rec.probe(timeout_s=0.05)) is False
    assert rec.up is False and errors() == before + 1


def test_probe_recovers_after_failure(tmp_path, monkeypatch):
    rec = make(tmp_path)
    outcomes = iter([OSError("down"), None])

    async def step(password, timeout_s):
        o = next(outcomes)
        if o:
            raise o

    monkeypatch.setattr(rec, "_select_one", step)

    async def go():
        return [await rec.probe(), await rec.probe()]

    assert asyncio.run(go()) == [False, True] and rec.up is True


def test_probe_never_holds_the_lock_or_the_pool(tmp_path, monkeypatch):
    """A hung probe must not delay the act path: it takes neither the lock nor the pool."""
    rec = make(tmp_path)
    rec._inner = sentinel = object()

    async def hang(password, timeout_s):
        await asyncio.sleep(60)

    monkeypatch.setattr(rec, "_select_one", hang)

    async def go():
        probe = asyncio.create_task(rec.probe(timeout_s=0.2))
        await asyncio.sleep(0.05)
        assert not rec._lock.locked()  # the act path can still connect
        await asyncio.wait_for(rec._lock.acquire(), 0.05)
        rec._lock.release()
        assert await probe is False

    asyncio.run(go())
    assert rec._inner is sentinel  # a failed probe does not drop the pool


def test_health_loop_probes_each_period_and_survives_failures(tmp_path, monkeypatch):
    rec = make(tmp_path)
    calls = []

    async def flaky(password, timeout_s):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("down")

    monkeypatch.setattr(rec, "_select_one", flaky)

    async def go():
        task = asyncio.create_task(health_loop(rec, period_s=0.01))
        while len(calls) < 4:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert rec.up is True and len(calls) >= 4


def test_recorder_up_gauge_reads_the_probe(tmp_path, monkeypatch):
    rec = make(tmp_path)
    reg = metrics.Registry()
    reg.gauge("nexus_operator_recorder_up", "up", lambda: {(): 1.0 if rec.up else 0.0})

    async def ok(password, timeout_s):
        return None

    monkeypatch.setattr(rec, "_select_one", ok)
    assert "\nnexus_operator_recorder_up 0\n" in reg.render()
    asyncio.run(rec.probe())
    assert "\nnexus_operator_recorder_up 1\n" in reg.render()


def test_main_starts_the_health_loop_for_a_real_recorder():
    import pathlib

    src = (pathlib.Path(metrics.__file__).parent / "main.py").read_text("utf-8")
    assert "health_loop(RUNTIME.recorder)" in src
