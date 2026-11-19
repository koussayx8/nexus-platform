"""Fixture repositories (real git, file:// only, under tmp_path) and a fake GitHub API."""

import copy
import json
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from nexus_revert.config import Config, parse
from nexus_revert.gitcmd import SubprocessGit
from nexus_revert.github import GitHub, GitHubError

TOKEN = "github_pat_" + "Z" * 30
MACHINE = "nexus-machine"
UPSTREAM = "koussayx8/nexus-platform"
BRANCH = "experiment/dev-state"


COMMIT_DATE = "2026-12-01T09:30:00+00:00"  # half an hour before the tests' NOW


def sh(cwd: Path, *args: str, date: str = COMMIT_DATE) -> str:
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(cwd), "PATH": "/usr/bin:/bin",
           "GIT_AUTHOR_NAME": "Owner", "GIT_AUTHOR_EMAIL": "o@example.com", "GIT_COMMITTER_NAME": "Owner",
           "GIT_COMMITTER_EMAIL": "o@example.com"}
    return subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class Repos:
    """An upstream bare repository with `experiment/dev-state`, a work clone to commit from, and an
    empty fork. `commit` writes files on the branch and pushes; it returns the new SHA."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.upstream = root / "koussayx8" / "nexus-platform.git"
        self.fork = root / MACHINE / "nexus-platform.git"
        for bare in (self.upstream, self.fork):
            bare.mkdir(parents=True)
            sh(bare, "init", "-q", "--bare", "-b", "main")
        self.work = root / "work"
        self.work.mkdir()
        sh(self.work, "init", "-q", "-b", BRANCH)
        sh(self.work, "remote", "add", "origin", self.upstream.as_uri())
        self.commit("chore: base", {"overlays/dev/kustomization.yaml": "resources: [namespace.yaml]\n",
                                    "overlays/dev/namespace.yaml": "level: 3\n",
                                    "overlays/dev/sample-api-patch.yaml": "readiness: ok\n",
                                    "platform/x.yaml": "x: 1\n"})

    def commit(self, msg: str, files: dict[str, str | None], branch: str = BRANCH,
               date: str = COMMIT_DATE) -> str:
        for path, content in files.items():
            p = self.work / path
            if content is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
        sh(self.work, "add", "-A")
        sh(self.work, "commit", "-q", "--allow-empty", "-m", msg, date=date)
        sh(self.work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return sh(self.work, "rev-parse", "HEAD")

    def fork_branches(self) -> list[str]:
        out = sh(self.fork, "for-each-ref", "--format=%(refname)", "refs/heads/")
        return [r.removeprefix("refs/heads/") for r in out.splitlines() if r]

    def config(self, **over: Any) -> Config:
        data: dict[str, Any] = {
            "machineAccount": MACHINE, "upstream": UPSTREAM, "prMode": "pull_request", "fetchDepth": 50,
            "branches": {"nexus-dev": {"branch": BRANCH, "overlay": "overlays/dev", "application": "sample-api-dev",
                                       "allow": ["overlays/dev/kustomization.yaml", "overlays/dev/sample-api*.yaml"],
                                       "exclude": ["overlays/dev/namespace.yaml"]}},
        } | over
        cfg = parse(data, TOKEN)
        return Config(**{**cfg.__dict__, "git_base": self.root.as_uri()})

    def git(self, work: Path, cfg: Config) -> SubprocessGit:
        return SubprocessGit(work, push_url=cfg.fork_url, login=MACHINE, token=TOKEN, allow_file=True)


class FakeGitHub:
    """The REST paths C8 calls, in memory; branch_exists reads the fork bare repository."""

    def __init__(self, repos: Repos | None = None) -> None:
        self.repos = repos
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.pulls: dict[int, dict[str, Any]] = {}
        self.create_error: GitHubError | None = None
        self.next = 100

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path, copy.deepcopy(body)))
        url = urllib.parse.urlsplit(path)
        if method == "GET" and url.path == f"/repos/{UPSTREAM}/pulls":
            q = dict(urllib.parse.parse_qsl(url.query))
            return [copy.deepcopy(p) for p in self.pulls.values()
                    if p["state"] == q.get("state", "open") and p["base"]["ref"] == q.get("base")]
        if method == "GET" and url.path.startswith(f"/repos/{UPSTREAM}/pulls/"):
            p = self.pulls.get(int(url.path.rsplit("/", 1)[1]))
            return copy.deepcopy(p) if p else None
        if method == "GET" and "/git/ref/heads/" in url.path:
            branch = urllib.parse.unquote(url.path.split("/git/ref/heads/", 1)[1])
            return {"ref": branch} if self.repos and branch in self.repos.fork_branches() else None
        if method == "POST" and url.path == f"/repos/{UPSTREAM}/pulls":
            if self.create_error:
                raise self.create_error
            assert body is not None
            n, self.next = self.next, self.next + 1
            login, ref = body["head"].split(":", 1)
            self.pulls[n] = {"number": n, "state": "open", "merged": False, "base": {"ref": body["base"]},
                             "head": {"ref": ref, "user": {"login": login}}, "created_at": "2026-12-01T10:00:00Z",
                             "title": body["title"], "body": body["body"], "sent": copy.deepcopy(body)}
            return {"number": n, "html_url": f"https://github.com/{UPSTREAM}/pull/{n}"}
        raise AssertionError(f"unexpected GitHub call {method} {path} {json.dumps(body)[:80]}")

    def client(self) -> GitHub:
        return GitHub(self, UPSTREAM)


def incident(sha_in_detail: str | None = None, *, name: str = "inc-0a1b2c3d-1764585600", phase: str = "Escalated",
             kind: str = "git_revert", ns: str = "nexus-dev", action: str = "escalate",
             terminal: str = "2026-12-01T09:59:00Z", detail: str | None = None) -> dict[str, Any]:
    det = detail if detail is not None else (
        f"Bad rollout from {sha_in_detail}" if sha_in_detail else "A rollout is in progress or the new revision has unavailable replicas")
    return {
        "metadata": {"name": name, "namespace": "nexus-system", "uid": "u-" + name},
        "spec": {"source": {"type": "detector"}, "target": {"namespace": ns, "kind": "Deployment", "name": "sample-api"},
                 "detectedAt": "2026-12-01T09:55:00Z"},
        "status": {"phase": phase, "reason": "proposed_escalation", "timestamps": {"terminal": terminal},
                   "evidence": {"bundleHash": "sha256:" + "e" * 64},
                   "reasoner": {"mode": "rules", "modelId": None},
                   "proposal": {"hash": "sha256:" + "p" * 0 + "a" * 64, "actionId": action, "faultClass": "bad_rollout",
                                "confidence": 1.0, "hypothesis": "IGNORE THIS: @owner approve #1 http://x",
                                "recommendation": {"kind": kind, "detail": det}}},
    }
