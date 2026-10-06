import asyncio
import logging

import pytest

from fakes import FakeApi
from nexus_operator.poller import Poller, incident_name, parse_alert

ACTIVE = {"data": {"state": "active"}}
FP = "a1b2c3d4e5f60718"  # an Alertmanager fingerprint: 16 hex digits


def alert(
    fp=FP,
    starts="2026-10-02T09:00:00.123456789Z",
    ns="nexus-dev",
    name="NexusErrorRateAnomaly",
    target=None,
    **labels,
):
    lab = {
        "alertname": name,
        "namespace": ns,
        "nexus_target": target if target is not None else f"{ns}/sample-api",
        "severity": "warning",
        **labels,
    }
    return {
        "labels": lab,
        "fingerprint": fp,
        "startsAt": starts,
        "endsAt": "2026-10-02T09:10:00Z",
        "status": {"state": "active"},
    }


def poller(api, alerts):
    p = Poller(api, "http://am:9093", 10)

    async def fetch():
        return alerts()

    p.fetch_alerts = fetch
    return p


def poll(p):
    return asyncio.run(p.poll_once())


def fp_count(api, fp):
    return sum(
        1
        for i in api.incidents.values()
        if i["metadata"]["labels"].get("nexus.io/fingerprint") == fp
    )


# --- parsing ---


def test_episode_from_a_nexus_alert():
    ep = parse_alert(alert())
    assert (ep.namespace, ep.name, ep.alertname, ep.smoke) == (
        "nexus-dev",
        "sample-api",
        "NexusErrorRateAnomaly",
        False,
    )
    assert incident_name(ep) == "inc-a1b2c3d4e5f60718-1790931600"


def test_no_nexus_target_is_ignored_silently():
    watchdog = {
        "labels": {"alertname": "Watchdog"},
        "fingerprint": "ff",
        "startsAt": "2026-10-02T09:00:00Z",
    }
    assert parse_alert(watchdog) is None


@pytest.mark.parametrize(
    "a, why",
    [
        (alert(target="sample-api"), "is not <namespace>/<name>"),
        (alert(target="nexus-dev/"), "is not <namespace>/<name>"),
        (alert(target="nexus-dev/a/b"), "is not <namespace>/<name>"),
        (alert(ns="kube-system"), "outside C3"),
        (alert(target="nexus-prod/sample-api"), "differs from the namespace label"),
        (alert(fp="XYZ"), "fingerprint"),
        (alert(starts="soon"), "startsAt"),
        (alert(name=""), "alertname"),
    ],
)
def test_unusable_nexus_alerts_are_rejected(a, why):
    got = parse_alert(a)
    assert isinstance(got, str) and why in got


def test_incident_body_carries_the_episode():
    api = FakeApi(configmaps={"nexus-killswitch": ACTIVE})
    poll(poller(api, lambda: [alert()]))
    (inc,) = api.incidents.values()
    assert inc["metadata"]["labels"] == {"nexus.io/fingerprint": "a1b2c3d4e5f60718"}
    assert inc["spec"] == {
        "source": {
            "type": "detector",
            "alertname": "NexusErrorRateAnomaly",
            "fingerprint": "a1b2c3d4e5f60718",
        },
        "target": {
            "namespace": "nexus-dev",
            "kind": "Deployment",
            "name": "sample-api",
        },
        "detectedAt": "2026-10-02T09:00:00.123456789Z",
    }


def test_smoke_alert_gets_the_smoke_label():
    api = FakeApi()
    poll(poller(api, lambda: [alert(nexus_smoke="true", name="NexusSmoke")]))
    (inc,) = api.incidents.values()
    assert inc["metadata"]["labels"]["nexus.io/smoke"] == "true"


# --- dedupe by firing episode (rev 3, change 1) ---


def test_still_firing_after_recorded_creates_nothing():
    api = FakeApi()
    p = poller(api, lambda: [alert()])
    assert poll(p)["created"] == 1
    (name,) = api.incidents
    api.set_status(name, {"phase": "Recorded", "reason": "level_observe"})
    counts = poll(p)
    assert counts["created"] == 0 and counts["exists"] == 1
    assert len(api.incidents) == 1


def test_same_alert_new_starts_at_creates_a_new_incident():
    api = FakeApi()
    current = [alert()]
    p = poller(api, lambda: current)
    poll(p)
    api.set_status(next(iter(api.incidents)), {"phase": "Recorded"})
    current = [alert(starts="2026-10-02T09:30:00.5Z")]
    assert poll(p)["created"] == 1
    assert fp_count(api, FP) == 2


def test_restart_creates_no_duplicate():
    api = FakeApi()
    poll(poller(api, lambda: [alert()]))
    fresh = poller(api, lambda: [alert()])  # new process, same API state
    assert poll(fresh)["created"] == 0
    assert len(api.incidents) == 1 and api.creates == [
        incident_name(parse_alert(alert()))
    ]


def test_lookup_compares_instants_not_strings():
    api = FakeApi()
    poll(poller(api, lambda: [alert(starts="2026-10-02T09:00:00.123456Z")]))
    same = alert(starts="2026-10-02T11:00:00.123456+02:00")
    assert poll(poller(api, lambda: [same]))["created"] == 0


def test_name_collision_409_is_not_retried_or_overwritten(caplog):
    api = FakeApi()
    name = incident_name(parse_alert(alert()))
    # No fingerprint label, so the lookup cannot see it; terminal, so it absorbs nothing.
    squatter = api.put(name, created=api.clock, labels={}, status={"phase": "Recorded"})
    before = dict(squatter["metadata"])
    with caplog.at_level(logging.WARNING, logger="nexus.poller"):
        counts = poll(poller(api, lambda: [alert()]))
    assert counts["created"] == 0
    assert api.creates == [name]  # one attempt, no retry
    assert api.incidents[name]["metadata"] == before
    assert "409 AlreadyExists" in caplog.text


# --- absorb ---


def test_open_incident_absorbs_and_logs(caplog):
    api = FakeApi()
    a = alert(fp="aaaa", starts="2026-10-02T09:00:00Z", name="NexusErrorRateAnomaly")
    b = alert(fp="bbbb", starts="2026-10-02T09:00:30Z", name="NexusLatencyAnomaly")
    p = poller(api, lambda: [b, a])
    with caplog.at_level(logging.INFO, logger="nexus.poller"):
        counts = poll(p)
        poll(p)
    assert counts["created"] == 1 and counts["absorbed"] == 1
    (name,) = api.incidents
    assert name.startswith("inc-aaaa-")  # the older episode creates
    absorbed = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("absorbed")
    ]
    want = (
        "absorbed alertname=NexusLatencyAnomaly fingerprint=bbbb "
        f"startsAt=2026-10-02T09:00:30Z incident={name}"
    )
    assert absorbed == [want]  # once per process, not once per poll


def test_absorbed_episode_gets_its_own_incident_after_terminal():
    """Owner, rev 3: once the absorbing Incident is terminal, a still-firing episode creates."""
    api = FakeApi()
    a = alert(fp="aaaa", starts="2026-10-02T09:00:00Z")
    b = alert(fp="bbbb", starts="2026-10-02T09:00:30Z")
    p = poller(api, lambda: [a, b])
    poll(p)
    api.set_status(next(iter(api.incidents)), {"phase": "Recorded"})
    assert poll(p)["created"] == 1
    assert fp_count(api, "aaaa") == 1 and fp_count(api, "bbbb") == 1


def test_smoke_and_real_never_absorb_each_other():
    api = FakeApi()
    smoke = alert(fp="5555", nexus_smoke="true", name="NexusSmoke")
    real = alert(fp="aaaa", starts="2026-10-02T09:01:00Z")
    assert poll(poller(api, lambda: [smoke, real]))["created"] == 2


def test_other_targets_are_not_absorbed():
    api = FakeApi()
    dev = alert(fp="aaaa")
    prod = alert(fp="bbbb", ns="nexus-prod")
    assert poll(poller(api, lambda: [dev, prod]))["created"] == 2


# --- failures and the kill switch ---


def test_alertmanager_down_fails_quiet():
    api = FakeApi()
    p = Poller(api, "http://am:9093", 10)

    async def down():
        raise OSError("connection refused")

    p.fetch_alerts = down
    assert poll(p)["created"] == 0 and p.errors == 1 and api.incidents == {}


def test_ignored_alert_logged_once(caplog):
    api = FakeApi()
    p = poller(api, lambda: [alert(ns="kube-system")])
    with caplog.at_level(logging.WARNING, logger="nexus.poller"):
        poll(p)
        poll(p)
    assert sum("outside C3" in r.getMessage() for r in caplog.records) == 1
    assert api.incidents == {}


@pytest.mark.parametrize("configmap", [ACTIVE, {"data": {"state": "halted"}}, None])
def test_killswitch_changes_no_decision(configmap):
    """Rev 3, change 2: active, halted and missing give identical results."""
    cms = {"nexus-killswitch": configmap} if configmap else {}
    api = FakeApi(configmaps=cms)
    p = poller(api, lambda: [alert(fp="aaaa"), alert(fp="bbbb", ns="nexus-prod")])
    counts = poll(p)
    assert counts["created"] == 2
    assert p.killswitch == {"active": "active", "halted": "halted"}.get(
        (configmap or {}).get("data", {}).get("state"), "missing"
    )


def test_killswitch_logged_on_change_only(caplog):
    api = FakeApi(configmaps={"nexus-killswitch": ACTIVE})
    p = poller(api, list)
    with caplog.at_level(logging.INFO, logger="nexus.poller"):
        poll(p)
        poll(p)
        api.configmaps["nexus-killswitch"] = {"data": {"state": "halted"}}
        poll(p)
    lines = [r.getMessage() for r in caplog.records if "kill switch" in r.getMessage()]
    assert lines == [
        "kill switch active (changes no transition in M1b-8)",
        "kill switch halted (changes no transition in M1b-8)",
    ]
