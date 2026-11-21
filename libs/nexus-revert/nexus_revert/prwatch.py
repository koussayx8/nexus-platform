"""Merge recognition by polling (ADR-041 decision 10, "Merge recognition and the C8 metric").

The owner merges; the bot only notices. Merged: "merged by <login>, merge commit <sha>". Closed:
"closed, not merged". After 24 hours: one line, and the pull request stays open. A restart finds
the open pull requests again by author and head prefix; no state file. Per the ADR (stated limit
6), the result is a notification, not an Incident or Flight Recorder write: the Incident stays
Escalated, and the export reads created_at and merged_at from GitHub.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from nexusctl.render.sanitize import sanitize

from .github import GitHub, GitHubError
from .revert import BRANCH_PREFIX, SHA

log = logging.getLogger("nexus_revert.prwatch")


@dataclass
class Tracked:
    number: int
    incident: str
    since: datetime


def _time(raw: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


class MergeWatch:
    def __init__(self, gh: GitHub, machine: str, notify: Callable[[str], None], *,
                 now: Callable[[], datetime], timeout: timedelta = timedelta(hours=24)) -> None:
        self._gh, self._machine, self._notify, self._now, self._timeout = gh, machine, notify, now, timeout
        self.tracked: dict[int, Tracked] = {}

    def track(self, number: int, incident: str, since: datetime | None = None) -> None:
        self.tracked.setdefault(number, Tracked(number, incident, since or self._now()))

    def recover(self, bases: list[str]) -> int:
        """After a restart: re-track the open revert pull requests by author and head prefix."""
        found = 0
        for base in bases:
            for p in self._gh.open_pulls(base):
                head = p.get("head") or {}
                login = str((head.get("user") or {}).get("login", ""))
                ref = str(head.get("ref", ""))
                if login.lower() != self._machine.lower() or not ref.startswith(BRANCH_PREFIX):
                    continue
                if isinstance(p.get("number"), int) and p["number"] not in self.tracked:
                    self.track(p["number"], ref[len(BRANCH_PREFIX):], _time(p.get("created_at")))
                    found += 1
        return found

    def tick(self) -> None:
        for t in list(self.tracked.values()):
            try:
                p = self._gh.get_pull(t.number)
            except GitHubError as e:
                log.warning("pull request #%d: %s", t.number, e)
                continue
            if p is None:
                self._done(t, "is gone")
            elif p.get("merged") is True:
                who = sanitize(((p.get("merged_by") or {}).get("login")) or "-", 39)
                sha = str(p.get("merge_commit_sha") or "")
                self._done(t, f"merged by {who}, merge commit {sha if SHA.fullmatch(sha) else '-'}")
            elif p.get("state") == "closed":
                self._done(t, "closed, not merged")
            elif self._now() - t.since > self._timeout:
                self._done(t, f"still open after {self._timeout}; no longer watched")

    def _done(self, t: Tracked, what: str) -> None:
        self._notify(f"{t.incident}: revert pull request #{t.number} {what}")
        del self.tracked[t.number]
