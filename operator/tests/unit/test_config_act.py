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


def test_the_reasoner_client_is_off_by_default():
    c = parse(cm())
    assert c.reasoner_endpoint == "" and c.reasoner_mode == "llm"
    assert c.reasoner_model == "" and c.reasoner_effort is None


def test_reasoner_keys_are_read():
    c = parse(
        cm(
            reasonerEndpoint="http://nexus-reasoner.nexus-reasoner.svc:8080/",
            reasonerMode="llm",
            reasonerModel="openai/gpt-oss-120b",
            reasonerEffort="low",
        )
    )
    assert c.reasoner_endpoint == "http://nexus-reasoner.nexus-reasoner.svc:8080"
    assert (c.reasoner_mode, c.reasoner_model, c.reasoner_effort) == (
        "llm",
        "openai/gpt-oss-120b",
        "low",
    )
    assert (
        parse(cm(reasonerEndpoint="http://r:8080", reasonerMode="rules")).reasoner_model
        == ""
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"reasonerEndpoint": "ftp://r"},
        {"reasonerEndpoint": "http://u:p@r:8080"},
        {"reasonerEndpoint": "http://r:8080?x=1"},
        {"reasonerEndpoint": "http://r:8080", "reasonerMode": "llm"},  # no model
        {
            "reasonerEndpoint": "http://r:8080",
            "reasonerMode": "agentic",
            "reasonerModel": "m",
        },
        {
            "reasonerEndpoint": "http://r:8080",
            "reasonerModel": "m",
            "reasonerEffort": "max",
        },
    ],
)
def test_bad_reasoner_keys_are_refused(extra):
    with pytest.raises(ConfigError):
        parse(cm(**extra))
