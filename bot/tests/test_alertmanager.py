"""The Telegram route for Alertmanager (ADR-040 decision 12): enabled, and in step with the base
values file except for the one receiver change."""

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OBS = ROOT / "platform/observability"
BASE = yaml.safe_load((OBS / "kube-prometheus-stack-values.yaml").read_text())["alertmanager"]
TG = yaml.safe_load((OBS / "alertmanager-telegram-values.yaml").read_text())["alertmanager"]


def test_enabled_in_the_application_after_the_base_file() -> None:
    app = yaml.safe_load((ROOT / "platform/argocd/applications/observability.yaml").read_text())
    [chart] = [s for s in app["spec"]["sources"] if s.get("chart")]
    assert [Path(f).name for f in chart["helm"]["valueFiles"]] == [
        "kube-prometheus-stack-values.yaml",
        "alertmanager-telegram-values.yaml",
    ]


def test_route_is_the_base_with_only_the_nexus_target_receiver_changed() -> None:
    expected = copy.deepcopy(BASE["config"]["route"])
    [nexus] = [r for r in expected["routes"] if r.get("group_by") == ["nexus_target"]]
    nexus["receiver"] = "nexus-telegram"
    assert TG["config"]["route"] == expected


def test_receivers_are_the_base_plus_telegram() -> None:
    base = BASE["config"]["receivers"]
    assert TG["config"]["receivers"][: len(base)] == base
    [tg] = TG["config"]["receivers"][len(base):]
    assert tg["name"] == "nexus-telegram"
    [cfg] = tg["telegram_configs"]
    assert cfg["parse_mode"] == ""  # asks for plain text (see the values file's comment)
    assert "bot_token" not in cfg  # the token is a file from a Secret, never a value in Git
    assert cfg["bot_token_file"].startswith("/etc/alertmanager/secrets/alertmanager-telegram/")
    assert "chat_id" not in cfg  # so is the chat ID
    assert cfg["chat_id_file"].startswith("/etc/alertmanager/secrets/alertmanager-telegram/")
    assert TG["alertmanagerSpec"]["secrets"] == ["alertmanager-telegram"]


def test_only_route_receivers_and_secrets_are_overridden() -> None:
    assert set(TG) == {"config", "alertmanagerSpec"}
    assert set(TG["config"]) == {"route", "receivers"}
    assert set(TG["alertmanagerSpec"]) == {"secrets"}
