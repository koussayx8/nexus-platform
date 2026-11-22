"""Merge recognition: merged, closed, timeout, restart recovery. Notifications only (ADR-041 limit 6)."""

from datetime import UTC, datetime, timedelta

from nexus_revert.prwatch import MergeWatch
from tests.fixtures import BRANCH, MACHINE, FakeGitHub

T0 = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


def watch(gh: FakeGitHub, clock: list[datetime]) -> tuple[MergeWatch, list[str]]:
    notes: list[str] = []
    return MergeWatch(gh.client(), MACHINE, notes.append, now=lambda: clock[0]), notes


def pr(n: int, **kw: object) -> dict:  # type: ignore[type-arg]
    return {"number": n, "state": "open", "merged": False, "base": {"ref": BRANCH},
            "head": {"ref": f"nexus/revert-inc-{n}", "user": {"login": MACHINE}},
            "created_at": "2026-12-01T09:00:00Z"} | kw


def test_merged() -> None:
    gh, clock = FakeGitHub(), [T0]
    gh.pulls[1] = pr(1)
    w, notes = watch(gh, clock)
    w.track(1, "inc-1")
    w.tick()
    assert notes == []
    gh.pulls[1] |= {"state": "closed", "merged": True, "merged_by": {"login": "koussayx8"}, "merge_commit_sha": "a" * 40}
    w.tick()
    assert notes == [f"inc-1: revert pull request #1 merged by koussayx8, merge commit {'a' * 40}"] and not w.tracked


def test_closed_not_merged() -> None:
    gh, clock = FakeGitHub(), [T0]
    gh.pulls[2] = pr(2, state="closed")
    w, notes = watch(gh, clock)
    w.track(2, "inc-2")
    w.tick()
    assert notes == ["inc-2: revert pull request #2 closed, not merged"]


def test_timeout_leaves_it_open() -> None:
    gh, clock = FakeGitHub(), [T0]
    gh.pulls[3] = pr(3)
    w, notes = watch(gh, clock)
    w.track(3, "inc-3")
    clock[0] = T0 + timedelta(hours=24, seconds=1)
    w.tick()
    assert "still open after 1 day" in notes[0] and gh.pulls[3]["state"] == "open"
    assert [m for m, _, _ in gh.calls] == ["GET"]


def test_restart_recovers_by_author_and_prefix() -> None:
    gh, clock = FakeGitHub(), [T0]
    gh.pulls[4] = pr(4)
    gh.pulls[5] = pr(5, head={"ref": "nexus/revert-inc-5", "user": {"login": "someone-else"}})
    gh.pulls[6] = pr(6, head={"ref": "feature/x", "user": {"login": MACHINE}})
    w, _ = watch(gh, clock)
    assert w.recover([BRANCH]) == 1 and list(w.tracked) == [4] and w.tracked[4].incident == "inc-4"
    assert w.tracked[4].since == datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


def test_merged_by_is_sanitised() -> None:
    gh, clock = FakeGitHub(), [T0]
    gh.pulls[7] = pr(7, state="closed", merged=True, merged_by={"login": "x\x1b[2Jy"}, merge_commit_sha="not-a-sha")
    w, notes = watch(gh, clock)
    w.track(7, "inc-7")
    w.tick()
    assert "\x1b" not in notes[0] and "merge commit -" in notes[0]
