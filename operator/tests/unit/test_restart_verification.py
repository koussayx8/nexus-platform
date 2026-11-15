"""restart_v2 (ADR-039 addendum 2026-11-15, #197 A2): a restart is verified only when every signal
that was anomalous in the evidence bundle is back within its pre-fault baseline.

restart_v1 read Ready replicas, p95 and container restarts, so a restart that fixed nothing was
`Resolved / remediation_verified` whenever p95 stayed flat: a partial hang (S1), a dependency
that refuses the application (S5), a traffic step that goes on (S6). Each case below names what
it ended as under restart_v1 (run on dev c007c78, 2026-11-15).

Real values, S1 on nexus-dev at L3, 2026-11-11 (F3, docs/plans/m2-exit-record.md; the Flight
Recorder rows in ~/nexus-evidence/f3-2026-11-11/): the bundle's Z-scores, the p95 baseline, two
replicas, B = 80 req/s, an in-flight baseline of 0, and p95 good 55 s after Verifying began.
Modelled, not measured: how the 2 min request rate climbs back after the restart (see
s1_recovery). No recorded run holds that series.
"""

import pytest

from fakes_act import BASELINE, FakeMetrics, proposal_doc
from nexus_operator import verifier
from nexus_operator.catalogue import CATALOGUE
from nexus_operator.model import parse_time
from rig import TERMINAL, Rig, ended
from test_diagnose import detected
from test_diagnose_reasoner import FakeReasoner, answer

P95 = "namespace:nexus_sample_api_latency_p95:2m"
INFLIGHT = "namespace:nexus_sample_api_inflight:sum"
ERRORS = "namespace:nexus_sample_api_error_ratio:rate2m"
RATE = "namespace:nexus_sample_api_requests:rate2m"
Z = "namespace:nexus_sample_api_{}:zscore15m"

S1_ZSCORES = {  # the F3 bundle, sha256:31ac78d6…
    "cpu": -7.480735581823422,
    "inflight": 80.0,
    "errorRatio": 0.0,
    "latencyP95": 0.2179147744504898,
    "requestRate": -60.315555555555534,
}
S1_P95_BASELINE = (0.0256848873432144, 0.002448679639240639)  # mean, stddev (s)
S5_ZSCORES = {  # the S5 shape of the detection-rule tests: errors 0 -> 20 %, the rest flat
    "cpu": 0.1,
    "inflight": 0.0,
    "errorRatio": 20.0,
    "latencyP95": 0.2,
    "requestRate": -0.5,
}
S6_ZSCORES = {  # a traffic step x1.5 (S4's shape; S6 adds log text, which no signal reads)
    "cpu": 0.9,
    "inflight": 0.4,
    "errorRatio": 0.0,
    "latencyP95": 0.3,
    "requestRate": 40.0,
}


def metrics(zscores, p95_baseline=(0.05, 0.01)):
    m = FakeMetrics()
    names = {
        "cpu": "cpu",
        "inflight": "inflight",
        "errorRatio": "error_ratio",
        "latencyP95": "latency_p95",
        "requestRate": "requests",
    }
    for key, z in zscores.items():
        m.values[Z.format(names[key])] = z
    mean, std = p95_baseline
    m.values["namespace:nexus_sample_api_latency_p95:baseline_avg15m"] = mean
    m.values["namespace:nexus_sample_api_latency_p95:baseline_stddev15m"] = std
    m.values[P95] = mean
    return m


def drive(rig, at, ticks=150, seconds=5):
    """Tick to a terminal phase. Before each tick `at(values, t)` sets the raw signals; t is the
    seconds since Verifying began, None before."""
    for _ in range(ticks):
        status = rig.status()
        if status["phase"] in TERMINAL:
            return
        began = (status.get("timestamps") or {}).get("verifying")
        t = None if began is None else (rig.clock() - parse_time(began)).total_seconds()
        at(rig.metrics.values, t)
        rig.tick(seconds=seconds)


def evictions(rig):
    return len([r for r in rig.api.mutations() if r[0] == "evict"])


def hang(values, t):
    """S1 while it lasts: 80 clients wait, and the 2 min rate has fallen to about 17 req/s."""
    values[INFLIGHT], values[RATE] = 80.0, 17.0


def s1_recovery(lag):
    """The restart clears the hang. In-flight falls to 0 at once (a gauge). p95 is good from
    55 s (recorded). The request rate is a 2 min rate over the new pods' series, so it climbs
    for 120 s after they serve, read `lag` seconds late (a 15 s scrape and a 30 s rule
    interval: 0 to 45 s). The climb is the rule's arithmetic, not a measurement."""

    def at(values, t):
        if t is None:
            return hang(values, t)
        values[INFLIGHT] = 0.0
        values[P95] = S1_P95_BASELINE[0] if t >= 55 else None
        values[RATE] = 80.0 * min(1.0, max(0.0, t - lag) / 120)

    return at


# ---------------------------------------------------------------- S1: the right restart
@pytest.mark.parametrize("lag", [0, 45])
def test_s1_real_values_a_restart_that_clears_the_hang_is_verified(lag):
    rig = Rig(collector=True, metrics=metrics(S1_ZSCORES, S1_P95_BASELINE))
    detected(rig)
    drive(rig, s1_recovery(lag))
    assert ended(rig) == ("Resolved", "remediation_verified")
    assert evictions(rig) == 2
    status = rig.status()
    # the in-flight branch's two signals were anomalous; p95 and the error ratio were not
    assert status["evidence"]["anomalous"] == ["inflight", "requestRate"]
    assert status["proposal"]["actionId"] == "restart_workload"
    result, obs = rig.rec.verifications[("uid-inc", "restart_v2")]
    assert result == "met"
    assert set(obs["signals"]) == {"latencyP95", "inflight", "requestRate"}
    assert all(s["within"] for s in obs["signals"].values())


def test_s1_verification_waits_for_the_request_rate():
    # restart_v1 was met 115 s after Verifying began (F3). The 2 min rate is back within
    # 80 +- 3 req/s only 116 s after the new pods serve, and then has to hold for 60 s.
    rig = Rig(collector=True, metrics=metrics(S1_ZSCORES, S1_P95_BASELINE))
    detected(rig)
    drive(rig, s1_recovery(0))
    state = rig.status()["verification"]
    waited = (
        parse_time(state["goodSince"]) - parse_time(state["startedAt"])
    ).total_seconds()
    assert 115 <= waited <= 125
    assert waited + verifier.RESTART_STABLE_S + 45 < (
        CATALOGUE["restart_workload"].verify_timeout_s
    )


# ---------------------------------------------------------------- wrong restarts, per scenario
def test_s1_a_partial_hang_that_the_restart_did_not_clear_is_escalated():
    # restart_v1: Resolved / remediation_verified (the p95 of the requests that complete is flat)
    rig = Rig(collector=True, metrics=metrics(S1_ZSCORES, S1_P95_BASELINE))
    detected(rig)
    drive(rig, hang)
    assert ended(rig) == ("Escalated", "verification_failed")
    result, obs = rig.rec.verifications[("uid-inc", "restart_v2")]
    assert result == "not_met"
    assert obs["signals"]["latencyP95"]["within"] is True
    assert obs["signals"]["inflight"] == {"value": 80.0, "within": False}
    assert obs["signals"]["requestRate"] == {"value": 17.0, "within": False}


def test_s1_a_full_hang_that_the_restart_did_not_clear_is_escalated():
    # restart_v1: Escalated / verification_failed too (no request completes, so no p95)
    def at(values, t):
        hang(values, t)
        values[P95] = None

    rig = Rig(collector=True, metrics=metrics(S1_ZSCORES, S1_P95_BASELINE))
    detected(rig)
    drive(rig, at)
    assert ended(rig) == ("Escalated", "verification_failed")


def test_s3_a_restart_during_a_bad_rollout_is_refused_before_any_eviction():
    # restart_v1: the same. The Gate's preconditions stop it; no Verifier is reached.
    rig = Rig()
    rig.api.rolling = True
    rig.api.pods[1]["status"]["conditions"] = [{"type": "Ready", "status": "False"}]
    rig.proposed(proposal_doc())
    rig.run(ticks=90)
    assert ended(rig) == ("Blocked", "precondition_failed")
    assert evictions(rig) == 0


def test_s5_a_restart_for_a_dependency_failure_is_escalated():
    # restart_v1: Resolved / remediation_verified. R1 escalates S5 (rule 6), so the restart is
    # a Reasoner's proposal. The pods come back Ready and p95 stays flat; /items still fails.
    def at(values, t):
        values[ERRORS] = 0.2

    rig = Rig(
        collector=True, metrics=metrics(S5_ZSCORES), reasoner=FakeReasoner(answer())
    )
    detected(rig)
    drive(rig, at)
    assert rig.status()["evidence"]["anomalous"] == ["errorRatio"]
    assert evictions(rig) == 2
    assert ended(rig) == ("Escalated", "verification_failed")
    result, obs = rig.rec.verifications[("uid-inc", "restart_v2")]
    assert result == "not_met"
    assert obs["signals"]["errorRatio"] == {"value": 0.2, "within": False}
    assert obs["signals"]["latencyP95"]["within"] is True


def test_s6_a_restart_during_a_traffic_step_that_goes_on_is_escalated():
    # restart_v1: Resolved / remediation_verified. R1 proposes no_action (rule 5), so the
    # restart is a Reasoner's proposal, e.g. one that followed the injected text.
    def at(values, t):
        values[RATE] = 120.0

    rig = Rig(
        collector=True, metrics=metrics(S6_ZSCORES), reasoner=FakeReasoner(answer())
    )
    detected(rig)
    drive(rig, at)
    assert rig.status()["evidence"]["anomalous"] == ["requestRate"]
    assert ended(rig) == ("Escalated", "verification_failed")
    _, obs = rig.rec.verifications[("uid-inc", "restart_v2")]
    assert obs["signals"]["requestRate"] == {"value": 120.0, "within": False}


def test_s6_limit_a_step_that_ends_during_verification_still_verifies():
    # The limit of any signal-based criterion: when the fault ends on its own while the
    # Verifier watches, the restart is credited. restart_v1 verified this case too.
    def at(values, t):
        values[RATE] = 120.0 if t is None or t < 30 else 80.0

    rig = Rig(
        collector=True, metrics=metrics(S6_ZSCORES), reasoner=FakeReasoner(answer())
    )
    detected(rig)
    drive(rig, at)
    assert ended(rig) == ("Resolved", "remediation_verified")


# ---------------------------------------------------------------- which signals are required
def test_anomalous_reads_the_bundles_zscores_and_counts_unknown_as_anomalous():
    def bundle(zscores):
        return {"metrics": {"zscores": zscores}}

    assert verifier.anomalous(bundle(S1_ZSCORES)) == ["inflight", "requestRate"]
    assert verifier.anomalous(bundle(S5_ZSCORES)) == ["errorRatio"]
    assert verifier.anomalous(bundle(S6_ZSCORES)) == ["requestRate"]
    assert verifier.anomalous(bundle({"latencyP95": 4.0})) == [
        "latencyP95",
        "inflight",
        "errorRatio",
        "requestRate",
    ]
    # later in a full hang the p95 and error-ratio series stop: null is unknown
    late = S1_ZSCORES | {"latencyP95": None, "errorRatio": None}
    assert verifier.anomalous(bundle(late)) == [
        "latencyP95",
        "inflight",
        "errorRatio",
        "requestRate",
    ]
    assert verifier.anomalous({}) == list(verifier.SIGNALS)
    # a drop in in-flight requests, errors or latency is not a fault; a rate drop is
    quiet = {"inflight": -5, "errorRatio": -5, "latencyP95": -5, "requestRate": -2.9}
    assert verifier.anomalous(bundle(quiet)) == []
    assert verifier.anomalous(bundle(quiet | {"requestRate": True})) == ["requestRate"]


def test_a_signal_that_was_not_anomalous_is_not_required():
    # the p95 branch: the request rate was normal in the bundle and has moved since
    rig = Rig()
    rig.metrics.values[RATE] = 90.0
    rig.proposed(proposal_doc(), anomalous=["latencyP95"])
    rig.run(ticks=90)
    assert ended(rig) == ("Resolved", "remediation_verified")
    _, obs = rig.rec.verifications[("uid-inc", "restart_v2")]
    assert set(obs["signals"]) == {"latencyP95"}


@pytest.mark.parametrize("recorded", [None, "inflight", {"inflight": True}])
def test_without_a_usable_record_every_signal_is_required(recorded):
    # an Incident proposed by an operator that did not write the list, or a damaged status
    rig = Rig()
    rig.metrics.values[RATE] = 90.0
    rig.proposed(proposal_doc(), anomalous=recorded)
    rig.run(ticks=90)
    assert ended(rig) == ("Escalated", "verification_failed")


def test_p95_is_required_even_when_the_record_leaves_it_out():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    rig.proposed(proposal_doc(), anomalous=[])
    rig.run(ticks=90)
    assert ended(rig) == ("Escalated", "verification_failed")


@pytest.mark.parametrize(
    "missing", ["baseline", "mean", "stddev", "value", "source", "nan"]
)
def test_an_unknown_value_or_baseline_of_a_required_signal_never_verifies(missing):
    baseline = {k: dict(v) for k, v in BASELINE.items()}
    rig = Rig()
    if missing == "baseline":
        del baseline["inflight"]
    elif missing in ("mean", "stddev"):
        baseline["inflight"][missing] = None
    elif missing == "nan":
        baseline["inflight"]["mean"] = float("nan")
    elif missing == "value":
        rig.metrics.values[INFLIGHT] = None
    else:  # Prometheus does not answer for this one query
        real = rig.metrics.scalar

        async def scalar(query, namespace):
            if query == INFLIGHT:
                raise TimeoutError("no answer")
            return await real(query, namespace)

        rig.metrics.scalar = scalar
    rig.proposed(proposal_doc(), baseline=baseline, anomalous=["inflight"])
    rig.run(ticks=90)
    assert ended(rig) == ("Escalated", "verification_failed")


@pytest.mark.parametrize(
    ("signal", "value", "ok"),
    [
        ("latencyP95", 0.08, True),  # 0.05 + 3 * 0.01
        ("latencyP95", 0.081, False),
        ("inflight", 3.0, True),  # 0 + 3 * max(0, 1)
        ("inflight", 3.1, False),
        ("errorRatio", 0.03, True),  # 0 + 3 * max(0, 0.01)
        ("errorRatio", 0.031, False),
        ("requestRate", 77.0, True),  # 80 +- 3 * max(1, 1): two-sided
        ("requestRate", 76.9, False),
        ("requestRate", 83.0, True),
        ("requestRate", 83.1, False),
        ("inflight", -1.0, True),  # one-sided: fewer is not a fault
        ("inflight", True, False),
        ("inflight", float("inf"), False),
    ],
)
def test_within_baseline_uses_each_signals_own_floor(signal, value, ok):
    assert verifier.within(BASELINE, signal, value) is ok


# ---------------------------------------------------------------- what Diagnosing records
def test_diagnosing_records_the_four_baselines_and_leaves_the_bundle_alone():
    rig = Rig(collector=True, metrics=metrics(S1_ZSCORES, S1_P95_BASELINE))
    detected(rig)
    rig.tick()
    rig.tick()
    evidence = rig.status()["evidence"]
    assert {k: v for k, v in evidence["baseline"].items() if k != "capturedAt"} == {
        "latencyP95": dict(zip(("mean", "stddev"), S1_P95_BASELINE, strict=True)),
        "inflight": {"mean": 0.0, "stddev": 0.0},
        "errorRatio": {"mean": 0.0, "stddev": 0.0},
        "requestRate": {"mean": 80.0, "stddev": 1.0},
    }
    assert evidence["anomalous"] == ["inflight", "requestRate"]
    # evidence/v1.1 is unchanged: neither the baseline nor the list is in the bundle
    _, bundle, _ = rig.rec.bundles[evidence["bundleHash"]]
    assert bundle["schema"] == "evidence/v1.1"
    assert set(bundle) == {
        "schema",
        "incident",
        "workload",
        "pods",
        "events",
        "logs",
        "metrics",
        "gitops",
        "collectedAt",
        "redaction",
        "hash",
    }
    assert set(bundle["metrics"]) == {"window", "zscores", "errorRatio"}
