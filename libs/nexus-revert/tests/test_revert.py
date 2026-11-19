"""Checks 1-6 and the mechanical revert, on real fixture repositories (file://, no network)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_revert.revert import Prepared, Refusal, prepare, push
from nexus_revert.trigger import request_for
from tests.fixtures import BRANCH, FakeGitHub, Repos, incident, sh

NOW = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def repos(tmp_path: Path) -> Repos:
    return Repos(tmp_path)


def run(repos: Repos, inc: dict[str, object], gh: FakeGitHub | None = None, **cfg_over: object) -> tuple[Prepared | Refusal, Path]:
    cfg = repos.config(**cfg_over)
    req = request_for(inc, cfg)
    assert req is not None
    work = repos.root / f"scratch-{len(list(repos.root.glob('scratch-*')))}"
    work.mkdir()
    return prepare(req, cfg, repos.git(work, cfg), (gh or FakeGitHub(repos)).client(), NOW), work


def test_named_commit_is_reverted_mechanically(repos: Repos) -> None:
    bad = repos.commit("feat: S3 bad config", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    p, work = run(repos, incident(bad))
    assert isinstance(p, Prepared) and p.sha == bad and p.chosen_by_reasoner
    assert p.branch == "nexus/revert-inc-0a1b2c3d-1764585600" and p.base == BRANCH
    # the revert's diff is exactly the inverse of the candidate's
    assert sh(work, "diff", f"{p.revert_commit}^", p.revert_commit) == sh(repos.work, "diff", bad, f"{bad}^")
    msg = sh(work, "log", "-1", "--format=%B", p.revert_commit)
    assert msg == f"revert(overlays/dev): revert {bad}\n\nThis reverts commit {bad}."
    assert "S3 bad config" not in msg  # the original subject is not copied into the commit


def test_rule_a1_takes_the_newest_overlay_commit(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    repos.commit("docs: outside", {"platform/x.yaml": "x: 2\n"})  # newer, but not in the overlay
    p, _ = run(repos, incident())
    assert isinstance(p, Prepared) and p.sha == bad and not p.chosen_by_reasoner


def test_a1_tries_no_older_commit(repos: Repos) -> None:
    repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    repos.commit("chore: mixed", {"overlays/dev/sample-api-patch.yaml": "readiness: worse\n", "platform/x.yaml": "x: 3\n"})
    p, _ = run(repos, incident())
    assert isinstance(p, Refusal) and p.check == "4"


@pytest.mark.parametrize("detail", ["see commit abc123", "two: " + "a" * 40 + " " + "b" * 40, "short " + "a" * 39])
def test_no_single_named_sha_means_a1(repos: Repos, detail: str) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    p, _ = run(repos, incident(detail=detail))
    assert isinstance(p, Prepared) and p.sha == bad and not p.chosen_by_reasoner


def test_check2_sha_not_in_the_repository(repos: Repos) -> None:
    p, _ = run(repos, incident("f" * 40))
    assert isinstance(p, Refusal) and p.check == "2"


def test_check2_sha_on_another_branch(repos: Repos) -> None:
    other = repos.commit("feat: elsewhere", {"overlays/dev/sample-api-patch.yaml": "readiness: other\n"}, branch="feature/x")
    sh(repos.work, "reset", "-q", "--hard", "HEAD^")
    p, _ = run(repos, incident(other))
    assert isinstance(p, Refusal) and p.check == "2"


def test_check2_older_than_the_depth(repos: Repos) -> None:
    old = repos.commit("feat: old", {"overlays/dev/sample-api-patch.yaml": "readiness: old\n"})
    for i in range(4):
        repos.commit(f"chore: {i}", {"platform/x.yaml": f"x: {i + 10}\n"})
    p, _ = run(repos, incident(old), fetchDepth=3)
    assert isinstance(p, Refusal) and p.check == "2"


def test_check3_merge_commit(repos: Repos) -> None:
    sh(repos.work, "checkout", "-q", "-b", "side")
    repos.commit("feat: side", {"overlays/dev/side.yaml": "s: 1\n"}, branch="side")
    sh(repos.work, "checkout", "-q", BRANCH)
    repos.commit("feat: main side", {"overlays/dev/sample-api-patch.yaml": "readiness: m\n"})
    sh(repos.work, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    sh(repos.work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    merge = sh(repos.work, "rev-parse", "HEAD")
    p, _ = run(repos, incident(merge))
    assert isinstance(p, Refusal) and p.check == "3"


def test_check4_outside_the_overlay(repos: Repos) -> None:
    c = repos.commit("chore: platform", {"platform/x.yaml": "x: 9\n"})
    p, _ = run(repos, incident(c))
    assert isinstance(p, Refusal) and p.check == "4" and "outside overlays/dev" in p.text


def test_check4_overlay_plus_another_path_a2(repos: Repos) -> None:
    c = repos.commit("chore: both", {"overlays/dev/sample-api-patch.yaml": "readiness: b\n", ".github/w.yml": "x\n"})
    p, _ = run(repos, incident(c))
    assert isinstance(p, Refusal) and p.check == "4"


def test_check4_a_prefix_lookalike_is_outside(repos: Repos) -> None:
    c = repos.commit("chore: lookalike", {"overlays/dev-evil/x.yaml": "x\n"})
    p, _ = run(repos, incident(c))
    assert isinstance(p, Refusal) and p.check == "4"


def test_check4_empty_commit(repos: Repos) -> None:
    c = repos.commit("chore: empty", {})
    p, _ = run(repos, incident(c))
    assert isinstance(p, Refusal) and p.check == "4"


def test_check4b_level_commit_is_never_reverted(repos: Repos) -> None:
    lvl = repos.commit("chore(exp): nexus-dev level 2", {"overlays/dev/namespace.yaml": "level: 2\n"})
    p, _ = run(repos, incident(lvl))
    assert isinstance(p, Refusal) and p.check == "4b"
    p2, _ = run(repos, incident())  # A1 would pick it too: refused, no older commit tried
    assert isinstance(p2, Refusal) and p2.check == "4b"


def test_check4b_only_workload_files(repos: Repos) -> None:
    ok = repos.commit("feat: S3 in the kustomization", {"overlays/dev/kustomization.yaml": "resources: [namespace.yaml]\nx: 1\n"})
    p, _ = run(repos, incident(ok))
    assert isinstance(p, Prepared)


@pytest.mark.parametrize("path", ["overlays/dev/other.yaml", "overlays/dev/sample-api/x.yaml", "overlays/dev/README.md"])
def test_check4b_other_overlay_files_are_refused(repos: Repos, path: str) -> None:
    c = repos.commit("chore: overlay, not workload", {path: "x\n"})
    p, _ = run(repos, incident(c))
    assert isinstance(p, Refusal) and p.check == "4b" and path in p.text


def test_check4b_workload_plus_namespace_is_refused_whole(repos: Repos) -> None:
    c = repos.commit("chore: both", {"overlays/dev/sample-api-patch.yaml": "readiness: x\n",
                                     "overlays/dev/namespace.yaml": "level: 1\n"})
    p, _ = run(repos, incident(c))
    assert isinstance(p, Refusal) and p.check == "4b"


def test_check2_older_than_24_hours(repos: Repos) -> None:
    old = repos.commit("feat: S3 two days ago", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"},
                       date="2026-11-29T10:00:00+00:00")
    p, _ = run(repos, incident(old))
    assert isinstance(p, Refusal) and p.check == "2" and "24 h" in p.text


def test_check2_within_24_hours(repos: Repos) -> None:
    c = repos.commit("feat: S3 yesterday", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"},
                     date="2026-11-30T10:30:00+00:00")
    p, _ = run(repos, incident(c))
    assert isinstance(p, Prepared)


def test_check5_already_reverted(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    repos.commit("fix: by hand", {"overlays/dev/sample-api-patch.yaml": "readiness: ok\n"})
    p, _ = run(repos, incident(bad))
    assert isinstance(p, Refusal) and p.check == "5"


def test_check5_conflict(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    repos.commit("feat: later", {"overlays/dev/sample-api-patch.yaml": "readiness: different\n"})
    p, _ = run(repos, incident(bad))
    assert isinstance(p, Refusal) and p.check == "5"


def test_check6_open_revert_pull_request_on_the_branch(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    gh.pulls[7] = {"number": 7, "state": "open", "base": {"ref": BRANCH},
                   "head": {"ref": "nexus/revert-inc-older", "user": {"login": "nexus-machine"}}}
    p, _ = run(repos, incident(bad), gh)
    assert isinstance(p, Refusal) and p.check == "6" and "#7" in p.text


def test_check6_someone_elses_pull_request_does_not_count(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    gh.pulls[8] = {"number": 8, "state": "open", "base": {"ref": BRANCH},
                   "head": {"ref": "nexus/revert-x", "user": {"login": "someone"}}}
    p, _ = run(repos, incident(bad), gh)
    assert isinstance(p, Prepared)


def test_check6_branch_already_in_the_fork(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    sh(repos.work, "push", "-q", repos.fork.as_uri(), "HEAD:refs/heads/nexus/revert-inc-0a1b2c3d-1764585600")
    p, _ = run(repos, incident(bad))
    assert isinstance(p, Refusal) and p.check == "6"


def test_push_sends_only_the_nexus_branch_to_the_fork(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    before = sh(repos.upstream, "for-each-ref")
    p, work = run(repos, incident(bad))
    assert isinstance(p, Prepared)
    cfg = repos.config()
    push(p, cfg, repos.git(work, cfg))
    assert repos.fork_branches() == ["nexus/revert-inc-0a1b2c3d-1764585600"]
    assert sh(repos.fork, "rev-parse", "nexus/revert-inc-0a1b2c3d-1764585600") == p.revert_commit
    assert sh(repos.upstream, "for-each-ref") == before  # upstream untouched


def test_no_hooks_run(repos: Repos) -> None:
    # a hook planted in the template dir of a real clone would run on commit; the scratch repo uses
    # core.hooksPath=/dev/null, so a hook placed in the scratch repository is ignored as well
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    cfg = repos.config()
    req = request_for(incident(bad), cfg)
    assert req is not None
    work = repos.root / "hooked"
    work.mkdir()
    (work / ".git" / "hooks").mkdir(parents=True)
    canary = repos.root / "canary"
    hook = work / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {canary}\n")
    hook.chmod(0o755)
    p = prepare(req, cfg, repos.git(work, cfg), FakeGitHub(repos).client(), NOW)
    assert isinstance(p, Prepared) and not canary.exists()
