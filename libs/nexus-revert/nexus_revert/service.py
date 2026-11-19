"""The C8 loop: find the Incidents that ask for a revert, prepare, push, open, then watch.

One attempt per Incident per process. Across a restart, check 6 (an open revert pull request on
the branch, or the working branch in the fork) stops a second pull request, and Incidents that
ended more than `catch_up` before the start are not attempted at all.
"""

import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import template
from .config import Config
from .gitcmd import Git, GitError
from .github import GitHub, GitHubError, compare_link
from .prwatch import MergeWatch, _time
from .revert import Prepared, Refusal, prepare, push
from .trigger import Request, request_for

log = logging.getLogger("nexus_revert")


class IncidentReader(Protocol):
    def list_raw(self) -> list[dict[str, Any]]: ...


GitFactory = Callable[[Path], Git]


@dataclass(frozen=True)
class Outcome:
    incident: str
    text: str
    pull: int | None = None


class Service:
    def __init__(self, cfg: Config, reader: IncidentReader, gh: GitHub, git_factory: GitFactory,
                 notify: Callable[[str], None], *, now: Callable[[], datetime],
                 scratch: Path | None = None) -> None:
        self.cfg, self.reader, self.gh, self.git_factory = cfg, reader, gh, git_factory
        self.notify, self.now, self.scratch = notify, now, scratch
        self.watch = MergeWatch(gh, cfg.machine, notify, now=now,
                                timeout=timedelta(hours=cfg.merge_timeout_hours))
        self.attempted: set[str] = set()
        self.started = now()

    def _fresh(self, inc: dict[str, Any]) -> bool:
        ended = _time(((inc.get("status") or {}).get("timestamps") or {}).get("terminal"))
        return ended is None or ended >= self.started - timedelta(minutes=self.cfg.catch_up_minutes)

    def tick(self) -> list[Outcome]:
        out: list[Outcome] = []
        for inc in self.reader.list_raw():
            req = request_for(inc, self.cfg)
            uid = str((inc.get("metadata") or {}).get("uid") or (inc.get("metadata") or {}).get("name"))
            if req is None or uid in self.attempted:
                continue
            self.attempted.add(uid)
            if not self._fresh(inc):
                log.info("%s: ended before the catch-up window; not attempted", req.name)
                continue
            o = self.attempt(req)
            self.notify(o.text)
            out.append(o)
        self.watch.tick()
        return out

    def attempt(self, req: Request) -> Outcome:
        work = Path(tempfile.mkdtemp(prefix="nexus-revert-", dir=self.scratch))
        try:
            git = self.git_factory(work)
            result = prepare(req, self.cfg, git, self.gh)
            if isinstance(result, Refusal):
                return Outcome(req.name, f"{req.name}: no revert pull request "
                                         f"(check {result.check}): {result.text}")
            return self._open(req, result, git)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _open(self, req: Request, p: Prepared, git: Git) -> Outcome:
        try:
            push(p, self.cfg, git)
        except GitError as e:
            return Outcome(req.name, f"{req.name}: no revert pull request (push to the fork failed): {e}")
        link = compare_link(self.cfg.git_base, self.cfg.upstream, p.base, self.cfg.machine, p.branch)
        chosen = "named by the Reasoner" if p.chosen_by_reasoner else "chosen by rule A1"
        if self.cfg.mode == "compare_link":
            return Outcome(req.name, f"{req.name}: revert of {p.sha[:12]} ({chosen}) pushed to the fork; "
                                     f"open the pull request: {link}")
        try:
            pr = self.gh.create_pull(title=template.title(p, req), head=f"{self.cfg.machine}:{p.branch}",
                                     base=p.base, body=template.body(p, req, self.cfg))
        except GitHubError as e:
            return Outcome(req.name, f"{req.name}: the fork has the revert, but the pull request was refused "
                                     f"({e}); open it here: {link}")
        self.watch.track(pr["number"], req.name)
        return Outcome(req.name, f"{req.name}: revert pull request #{pr['number']} opened against {p.base} "
                                 f"(reverts {p.sha[:12]}, {chosen}); NEXUS will not merge it", pr["number"])

    def recover(self) -> int:
        try:
            return self.watch.recover(sorted({t.branch for t in self.cfg.branches.values()}))
        except GitHubError as e:
            log.warning("recover: %s", e)
            return 0
