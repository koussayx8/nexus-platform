import datetime
import pathlib

import pytest

from nexus_operator import config, killswitch
from nexus_operator.model import iso, parse_time

UTC = datetime.timezone.utc


@pytest.mark.parametrize(
    "value, want",
    [
        ("2026-10-02T08:30:12Z", datetime.datetime(2026, 10, 2, 8, 30, 12, tzinfo=UTC)),
        (
            "2026-10-02T08:30:12.123456789Z",
            datetime.datetime(2026, 10, 2, 8, 30, 12, 123456, tzinfo=UTC),
        ),
        (
            "2026-10-02T10:30:12.5+02:00",
            datetime.datetime(2026, 10, 2, 8, 30, 12, 500000, tzinfo=UTC),
        ),
    ],
)
def test_parse_time(value, want):
    assert parse_time(value) == want


@pytest.mark.parametrize(
    "value", ["", "2026-10-02", "2026-10-02T08:30:12", "yesterday"]
)
def test_parse_time_rejects(value):
    with pytest.raises(ValueError):
        parse_time(value)


def test_iso_is_seconds_precision():
    assert iso(datetime.datetime(2026, 10, 2, 8, 30, 12, 999999, tzinfo=UTC)) == (
        "2026-10-02T08:30:12Z"
    )


URL = "http://observability-kube-prometh-alertmanager.monitoring.svc:9093"


def cm(**data):
    return {"data": data}


def test_config_defaults_are_the_frozen_values():
    c = config.parse(cm(alertmanagerURL=URL, advisoryChecks="on", approvalTTL="15m"))
    assert c == config.Config(
        URL, alert_poll_s=10, reconcile_s=5, detected_timeout_s=20
    )


def test_config_reads_the_keys():
    c = config.parse(
        cm(
            alertmanagerURL=URL + "/",
            alertPollSeconds="7",
            reconcileSeconds="3",
            detectedTimeoutSeconds="30",
        )
    )
    assert (
        c.alertmanager_url,
        c.alert_poll_s,
        c.reconcile_s,
        c.detected_timeout_s,
    ) == (
        URL,
        7,
        3,
        30,
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"alertmanagerURL": ""},
        {"alertmanagerURL": "ftp://am:9093"},
        {"alertmanagerURL": "http://am:9093/?x=1"},
        {"alertmanagerURL": "http://user:pw@am:9093"},
        {"alertmanagerURL": URL, "alertPollSeconds": "ten"},
        {"alertmanagerURL": URL, "reconcileSeconds": "0"},
        {"alertmanagerURL": URL, "detectedTimeoutSeconds": "99999"},
    ],
)
def test_config_rejects(data):
    with pytest.raises(config.ConfigError):
        config.parse(cm(**data))


def test_config_missing_configmap():
    with pytest.raises(config.ConfigError):
        config.parse(None)


@pytest.mark.parametrize(
    "configmap, want",
    [
        ({"data": {"state": "active"}}, "active"),
        ({"data": {"state": "halted"}}, "halted"),
        ({"data": {"state": "Active"}}, "halted"),
        ({"data": {}}, "halted"),
        (None, "missing"),
    ],
)
def test_killswitch_state(configmap, want):
    assert killswitch.state_of(configmap) == want


def test_test_scaffolding_stays_out_of_the_package():
    """M1b-8 exit criterion: the race hook and the envtest login exist only under tests/."""
    package = pathlib.Path(__file__).resolve().parents[2] / "nexus_operator"
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in (
            "_race_hold",
            "NEXUS_ENVTEST",
            "envtest.kubeconfig",
            "operator.kubeconfig",
        ):
            assert word not in text, f"{word} in {path.name}"
