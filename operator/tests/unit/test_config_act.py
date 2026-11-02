import pytest

from nexus_operator.config import ConfigError, parse

BASE = {"alertmanagerURL": "http://am:9093"}


def cm(**extra):
    return {"data": BASE | extra}


def test_defaults_keep_advisory_on_and_the_epoch_at_zero():
    c = parse(cm())
    assert (c.advisory_checks, c.breaker_epoch, c.prometheus_url) == (True, 0, "")


def test_act_keys_are_read():
    c = parse(
        cm(
            advisoryChecks="off",
            breakerEpoch="1760000000",
            prometheusURL="http://p:9090/",
        )
    )
    assert (c.advisory_checks, c.breaker_epoch, c.prometheus_url) == (
        False,
        1760000000,
        "http://p:9090",
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"advisoryChecks": "maybe"},
        {"breakerEpoch": "soon"},
        {"breakerEpoch": "-1"},
        {"prometheusURL": "ftp://p"},
        {"prometheusURL": "http://u:p@host"},
    ],
)
def test_bad_act_keys_are_refused(extra):
    with pytest.raises(ConfigError):
        parse(cm(**extra))


def test_recorder_and_metrics_defaults_leave_the_writer_unconfigured():
    c = parse(cm())
    assert c.recorder_host == "" and c.recorder_port == 5432 and c.metrics_port == 9102
    assert c.recorder_database == "nexus_recorder" and c.recorder_user == "nexus_writer"
    assert c.recorder_password_file == "/var/run/nexus/flight-recorder-writer/password"


def test_recorder_keys_are_read():
    c = parse(
        cm(
            recorderHost="db.nexus-system.svc",
            recorderPort="6432",
            recorderDatabase="d",
            recorderUser="u",
            recorderPasswordFile="/p/pw",
            metricsPort="9200",
        )
    )
    assert (c.recorder_host, c.recorder_port, c.recorder_database, c.recorder_user) == (
        "db.nexus-system.svc",
        6432,
        "d",
        "u",
    )
    assert (c.recorder_password_file, c.metrics_port) == ("/p/pw", 9200)


@pytest.mark.parametrize(
    "extra",
    [
        {"recorderHost": "bad host"},
        {"recorderHost": "x;drop"},
        {"recorderPort": "0"},
        {"recorderPort": "http"},
        {"recorderPasswordFile": "relative/pw"},
        {"metricsPort": "70000"},
    ],
)
def test_bad_recorder_keys_are_refused(extra):
    with pytest.raises(ConfigError):
        parse(cm(**extra))
