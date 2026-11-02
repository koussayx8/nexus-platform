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
