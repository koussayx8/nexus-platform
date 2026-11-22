"""End to end on fixtures: Escalated Incident -> checked revert -> fork branch -> upstream pull request."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexus_revert.github import GitHubError
from nexus_revert.service import Service
from tests.fixtures import BRANCH, MACHINE, UPSTREAM, FakeGitHub, Repos, incident

NOW = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


class Reader:
    def __init__(self, *items: dict[str, Any]) -> None:
        self.items = list(items)

    def list_raw(self) -> list[dict[str, Any]]:
        return self.items


@pytest.fixture
def repos(tmp_path: Path) -> Repos:
    return Repos(tmp_path)


def service(repos: Repos, gh: FakeGitHub, *items: dict[str, Any], **over: Any) -> tuple[Service, list[str]]:
    cfg = repos.config(**over)
    notes: list[str] = []
    svc = Service(cfg, Reader(*items), gh.client(), lambda w: repos.git(w, cfg), notes.append, now=lambda: NOW,
                  scratch=repos.root)
    return svc, notes


@pytest.mark.parametrize("url", [None, 7, "javascript:alert(1)", "http://github.com/x/pull/1",
                                 "https://github.com/x/pull/1 and more"])
def test_the_opened_line_omits_a_missing_or_odd_link(repos: Repos, url: object) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    svc, _notes = service(repos, gh, incident(bad))
    create = svc.gh.create_pull

    def no_link(**kw: Any) -> dict[str, Any]:
        return {**create(**kw), "html_url": url}

    svc.gh.create_pull = no_link  # type: ignore[method-assign]
    [o] = svc.tick()
    assert o.pull == 100 and o.text.endswith("NEXUS will not merge it")


def test_opens_one_ready_pull_request_and_never_merges(repos: Repos) -> None:
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    svc, notes = service(repos, gh, incident(bad))
    [o] = svc.tick()
    assert o.pull == 100
    assert o.text.endswith(f"NEXUS will not merge it: https://github.com/{UPSTREAM}/pull/100")
    sent = gh.pulls[100]["sent"]
    assert sent["head"] == f"{MACHINE}:nexus/revert-inc-0a1b2c3d-1764585600" and sent["base"] == BRANCH
    assert sent["maintainer_can_modify"] is False and sent["draft"] is False
    assert sent["title"] == f"revert(overlays/dev): revert {bad[:12]} for inc-0a1b2c3d-1764585600"
    methods = {(m, p.split("?")[0]) for m, p, _ in gh.calls}
    assert not any("merge" in p for _, p in methods)
    assert all(not p.startswith(f"/repos/{MACHINE}/") or m == "GET" for m, p in methods)  # the fork: read only via API
    assert svc.tick() == []  # one attempt per Incident
    assert len(gh.pulls) == 1 and 100 in svc.watch.tracked
    assert not list(repos.root.glob("nexus-revert-*"))  # scratch directories removed


def test_a1_pull_request_says_so(repos: Repos) -> None:
    repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    svc, _ = service(repos, gh, incident())
    [o] = svc.tick()
    assert "chosen by rule A1" in o.text
    assert "The Reasoner did NOT name it" in gh.pulls[100]["body"]


def test_second_incident_on_the_same_branch_opens_no_second_pull_request(repos: Repos) -> None:
    repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    svc, _ = service(repos, gh, incident(), incident(name="inc-ffff0000-1764585700"))
    first, second = svc.tick()
    assert first.pull == 100 and second.pull is None and "check 6" in second.text
    assert len(gh.pulls) == 1


def test_refused_check_pushes_nothing(repos: Repos) -> None:
    gh = FakeGitHub(repos)
    svc, notes = service(repos, gh, incident("f" * 40))
    [o] = svc.tick()
    assert "check 2" in o.text and repos.fork_branches() == [] and gh.pulls == {}
    assert notes == [o.text]


@pytest.mark.parametrize("inc", [
    incident(phase="Resolved"), incident(kind="none"), incident(action="restart_workload"), incident(ns="nexus-prod"),
    incident(ns="nexus-data"),
])
def test_other_incidents_trigger_nothing(repos: Repos, inc: dict[str, Any]) -> None:
    gh = FakeGitHub(repos)
    svc, notes = service(repos, gh, inc)
    assert svc.tick() == [] and gh.calls == [] and notes == []


def test_main_mapping_is_config_only(repos: Repos) -> None:
    gh = FakeGitHub(repos)
    branches = {"nexus-prod": {"branch": "main", "overlay": "overlays/prod", "application": "sample-api-prod",
                               "allow": ["overlays/prod/kustomization.yaml"]}}
    svc, _ = service(repos, gh, incident(ns="nexus-prod"), branches=branches)
    [o] = svc.tick()
    assert "check" in o.text  # attempted against main (and refused here: the fixture main is empty)


def test_old_incident_after_a_restart_is_not_attempted(repos: Repos) -> None:
    gh = FakeGitHub(repos)
    old = (NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    svc, notes = service(repos, gh, incident(terminal=old))
    assert svc.tick() == [] and gh.calls == []


def test_compare_link_mode_outcome_c(repos: Repos) -> None:
    repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    svc, _ = service(repos, gh, incident(), prMode="compare_link")
    [o] = svc.tick()
    assert repos.fork_branches() == ["nexus/revert-inc-0a1b2c3d-1764585600"] and gh.pulls == {}
    assert f"/{UPSTREAM}/compare/experiment/dev-state...{MACHINE}:nexus/revert-inc-0a1b2c3d-1764585600?expand=1" in o.text


def test_refused_pull_request_falls_back_to_the_link(repos: Repos) -> None:
    repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    gh.create_error = GitHubError("POST /repos/x/pulls: HTTP 403 Resource not accessible", 403)
    svc, _ = service(repos, gh, incident())
    [o] = svc.tick()
    assert "refused" in o.text and "compare/" in o.text and o.pull is None


def test_merge_is_recognised_after_the_owner_merges(repos: Repos) -> None:
    repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = FakeGitHub(repos)
    svc, notes = service(repos, gh, incident())
    svc.tick()
    gh.pulls[100] |= {"state": "closed", "merged": True, "merged_by": {"login": "koussayx8"},
                      "merge_commit_sha": "c" * 40}
    svc.tick()
    assert notes[-1] == f"inc-0a1b2c3d-1764585600: revert pull request #100 merged by koussayx8, merge commit {'c' * 40}"
    assert svc.watch.tracked == {}
