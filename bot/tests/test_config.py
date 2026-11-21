"""Configuration and the token file. Error messages never contain the token."""

from pathlib import Path

import pytest

from nexus_bot.config import ConfigError, load, parse

TOKEN = "123456789:" + "B" * 35


def test_empty_allowlist_is_valid_and_admits_nobody() -> None:
    from nexus_bot.auth import sender_of
    from tests.fakes import message

    cfg = parse({"allowlist": {}}, TOKEN)
    assert cfg.allowlist == {} and sender_of(message("/status"), cfg.allowlist) is None


def test_shipped_configmap_parses() -> None:
    import yaml

    cm = yaml.safe_load((Path(__file__).parents[1] / "k8s" / "configmap.yaml").read_text())
    cfg = parse(yaml.safe_load(cm["data"]["config.yaml"]), TOKEN)
    assert cfg.allowlist == {8998653791: "koussay-approver"} and cfg.namespaces == ("nexus-dev", "nexus-prod")


def test_parse_minimal() -> None:
    cfg = parse({"allowlist": {"424242": "koussay-approver"}}, TOKEN)
    assert cfg.allowlist == {424242: "koussay-approver"} and cfg.namespaces == ("nexus-dev", "nexus-prod")
    assert TOKEN not in repr(cfg)


@pytest.mark.parametrize("data", [
    {}, {"allowlist": None}, {"allowlist": []}, {"allowlist": {"abc": "koussay-approver"}}, {"allowlist": {"-5": "x"}},
    {"allowlist": {"1": "Not Valid"}}, {"allowlist": {"1": "a"}, "namespaces": ["kube-system"]},
    {"allowlist": {"1": "a"}, "pollSeconds": 0}, {"allowlist": {"1": "a"}, "approverGroup": "system:masters"},
    [],
])
def test_parse_refuses(data: object) -> None:
    with pytest.raises(ConfigError):
        parse(data, TOKEN)


@pytest.mark.parametrize("token", ["", "abc", "123:short", TOKEN + " extra"])
def test_bad_token_is_refused_without_echo(token: str) -> None:
    with pytest.raises(ConfigError) as e:
        parse({"allowlist": {"1": "a"}}, token)
    assert not token or token not in str(e.value)


def test_load_from_files(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text('allowlist:\n  "424242": koussay-approver\n')
    (tmp_path / "token").write_text(TOKEN + "\n")
    cfg = load({"NEXUS_BOT_CONFIG": str(tmp_path / "config.yaml"), "NEXUS_BOT_TOKEN_FILE": str(tmp_path / "token")})
    assert cfg.token == TOKEN


def test_missing_token_file(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text('allowlist:\n  "1": a\n')
    with pytest.raises(ConfigError, match="token file"):
        load({"NEXUS_BOT_CONFIG": str(tmp_path / "config.yaml"), "NEXUS_BOT_TOKEN_FILE": str(tmp_path / "none")})


def test_trailing_newline_is_not_a_valid_name() -> None:
    with pytest.raises(ConfigError):
        parse({"allowlist": {"1": "koussay-approver\n"}}, TOKEN)
