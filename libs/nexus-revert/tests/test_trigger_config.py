"""Trigger, candidate extraction and configuration."""

from pathlib import Path

import pytest

from nexus_revert.config import ConfigError, load, parse
from nexus_revert.trigger import named_commit, request_for
from tests.fixtures import MACHINE, TOKEN, incident

BR = {"nexus-dev": {"branch": "experiment/dev-state", "overlay": "overlays/dev", "application": "sample-api-dev",
                    "allow": ["overlays/dev/kustomization.yaml"]}}
CFG = parse({"machineAccount": MACHINE, "branches": BR}, TOKEN)
A = "a" * 40


def test_named_commit() -> None:
    assert named_commit(f"revert {A} please") == A
    assert named_commit(f"{A.upper()} and {A}") == A  # one distinct SHA
    assert named_commit(f"{A} {'b' * 40}") is None
    assert named_commit("x" + "a" * 41) is None and named_commit(None) is None and named_commit(5) is None


def test_trigger_requires_escalated_escalate_git_revert_mapped_deployment() -> None:
    r = request_for(incident(A), CFG)
    assert r is not None and r.named_sha == A and r.target.branch == "experiment/dev-state"
    bad = incident(A)
    bad["spec"]["target"]["kind"] = "StatefulSet"
    assert request_for(bad, CFG) is None
    odd = incident(A, name="inc-UPPER")
    assert request_for(odd, CFG) is None


def test_config_defaults_and_fork_owner() -> None:
    assert CFG.fork == f"{MACHINE}/nexus-platform" and CFG.mode == "pull_request" and CFG.fetch_depth == 50
    assert TOKEN not in repr(CFG)
    assert CFG.upstream_url == "https://github.com/koussayx8/nexus-platform.git"


@pytest.mark.parametrize("data", [
    {}, {"machineAccount": MACHINE}, {"machineAccount": MACHINE, "branches": {}},
    {"machineAccount": MACHINE, "branches": BR, "fork": "koussayx8/nexus-platform"},
    {"machineAccount": MACHINE, "branches": BR, "fork": "someone/nexus-platform"},
    {"machineAccount": MACHINE, "branches": BR, "prMode": "merge"},
    {"machineAccount": MACHINE, "branches": {"nexus-dev": {**BR["nexus-dev"], "overlay": "../etc"}}},
    {"machineAccount": MACHINE, "branches": {"nexus-dev": {**BR["nexus-dev"], "branch": "-x"}}},
    {"machineAccount": MACHINE, "branches": {"nexus-dev": {**BR["nexus-dev"], "exclude": ["platform/x.yaml"]}}},
    {"machineAccount": "bad login!", "branches": BR},
    {"machineAccount": MACHINE, "branches": {"nexus-dev": {**BR["nexus-dev"], "allow": []}}},
    {"machineAccount": MACHINE, "branches": {"nexus-dev": {**BR["nexus-dev"], "allow": ["platform/*.yaml"]}}},
    {"machineAccount": MACHINE, "branches": BR, "maxAgeHours": 0},
])
def test_config_refuses(data: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ConfigError):
        parse(data, TOKEN)


def test_bad_token_not_echoed() -> None:
    with pytest.raises(ConfigError) as e:
        parse({"machineAccount": MACHINE, "branches": BR}, "short token!")
    assert "short token" not in str(e.value)


def test_shipped_example_parses(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[1] / "revert.example.yaml"
    (tmp_path / "t").write_text(TOKEN)
    cfg = load({"NEXUS_REVERT_CONFIG": str(example), "NEXUS_REVERT_TOKEN_FILE": str(tmp_path / "t")})
    assert list(cfg.branches) == ["nexus-dev"]
    assert cfg.branches["nexus-dev"].exclude == ("overlays/dev/namespace.yaml",)
    assert cfg.branches["nexus-dev"].allow == ("overlays/dev/kustomization.yaml", "overlays/dev/sample-api*.yaml")
    assert cfg.max_age_hours == 24
    assert cfg.branches["nexus-dev"].image == "ghcr.io/koussayx8/nexus-platform/sample-api"
