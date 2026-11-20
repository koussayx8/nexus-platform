"""evidence/v1.2 (ADR-044): the pod-spec summary, the rollout diff and the other active alerts.
Read-only, capped, env var names only (never values), no command lines, nothing from Git."""

import json

from fakes_act import DEP, NS, FakeMetrics
from nexus_operator import evidence
from nexus_operator.evidence import EvidenceCollector, template_diff, template_summary
from rig import Rig
from test_diagnose import detected

PROBE = {
    "httpGet": {"path": "/ready", "port": 8000},
    "periodSeconds": 5,
    "failureThreshold": 3,
}


def rs(
    revision,
    *,
    path="/ready",
    image="reg/sample-api@sha256:" + "a" * 64,
    env=None,
    **extra,
):
    c = {
        "name": "sample-api",
        "image": image,
        "ports": [{"containerPort": 8000}],
        "readinessProbe": PROBE | {"httpGet": {"path": path, "port": 8000}},
        "livenessProbe": {
            "exec": {"command": ["sh", "-c", "curl -H 'Authorization: Bearer x'"]}
        },
        "resources": {"limits": {"cpu": "200m"}, "requests": {"cpu": "50m"}},
        "env": env
        if env is not None
        else [
            {"name": "DB_HOST", "value": "dependency-db.nexus-data.svc"},
            {
                "name": "DB_PASSWORD",
                "valueFrom": {"secretKeyRef": {"name": "db", "key": "pw"}},
            },
        ],
        "envFrom": [{"secretRef": {"name": "app-secrets"}}],
        "args": ["--token", "s3cr3t-arg"],
    } | extra
    return {
        "metadata": {"name": f"{DEP}-r{revision}",
                     "annotations": {"deployment.kubernetes.io/revision": str(revision)}},
        "spec": {"template": {"spec": {"containers": [c]}}},
    }  # fmt: skip


def test_the_summary_keeps_names_and_shapes_never_values_or_commands():
    [c] = template_summary(rs(5))
    assert c["envNames"] == ["DB_HOST", "DB_PASSWORD"]
    assert c["envFrom"] == ["secretRef:app-secrets"]
    assert c["readinessProbe"] == "httpGet /ready :8000, period 5s, failures 3"
    assert c["livenessProbe"] == "exec (command not shown)"
    assert c["ports"] == [8000]
    text = json.dumps(c)
    for secret in (
        "dependency-db.nexus-data.svc",
        "Bearer",
        "s3cr3t-arg",
        "secretKeyRef",
        '"pw"',
    ):
        assert secret not in text, secret


def test_the_s3_change_is_named_in_the_diff():
    # the S3 patch: readiness /ready -> /readyz on a new revision
    diff = template_diff(
        template_summary(rs(1)), template_summary(rs(2, path="/readyz"))
    )
    want = (
        "sample-api.readinessProbe: httpGet /ready :8000, period 5s, failures 3"
        " -> httpGet /readyz :8000, period 5s, failures 3"
    )
    assert diff == [want]


def test_image_resources_and_env_names_are_diffed():
    old = template_summary(rs(1, env=[{"name": "A", "value": "1"}]))
    new = template_summary(rs(2, image="reg/sample-api:v2", env=[{"name": "B", "value": "2"}],
                              resources={"limits": {"cpu": "100m"}}))  # fmt: skip
    diff = template_diff(old, new)
    assert any(d.startswith("sample-api.image:") for d in diff)
    assert any(d.startswith("sample-api.resources:") for d in diff)
    assert (
        "sample-api.envNames added: B" in diff
        and "sample-api.envNames removed: A" in diff
    )
    assert not any("1" == d or "2" == d for d in diff)


def test_no_previous_revision_is_null_and_no_change_is_empty():
    assert template_diff(None, template_summary(rs(1))) is None
    assert template_diff(template_summary(rs(1)), template_summary(rs(2))) == []
    assert template_summary(None) is None


def test_everything_is_capped():
    many = [{"name": f"V{i:03d}", "value": "x"} for i in range(100)]
    [c] = template_summary(rs(1, env=many, image="i" * 1000))
    assert len(c["envNames"]) == evidence.MAX_ENV_NAMES
    assert len(c["image"]) <= evidence.MAX_FIELD_CHARS + len(" [cut]")
    old = template_summary({"spec": {"template": {"spec": {"containers": [
        {"name": f"c{i}", "image": "a"} for i in range(40)]}}}})  # fmt: skip
    new = template_summary({"spec": {"template": {"spec": {"containers": [
        {"name": f"c{i}", "image": "b"} for i in range(40)]}}}})  # fmt: skip
    assert len(template_diff(old, new)) == evidence.MAX_DIFF_ENTRIES


class AlertMetrics(FakeMetrics):
    def __init__(self, series, **kw):
        super().__init__(**kw)
        self.series, self.queries = series, []

    async def vector(self, query):
        self.queries.append(query)
        return self.series


def collect(rig, own_alert="NexusCpuAnomaly"):
    """The raw bundle for an Incident raised by `own_alert` on nexus-dev/sample-api."""
    detected(rig)
    inc = rig.inc()
    inc["spec"]["source"]["alertname"] = own_alert
    raw, _ = rig.call(EvidenceCollector(rig.api, rig.metrics, rig.clock).collect(inc))
    return raw, f"{inc['spec']['target']['namespace']}/{inc['spec']['target']['name']}"


def test_the_bundle_carries_the_spec_the_diff_and_the_other_alerts():
    rig = Rig(collector=True, metrics=AlertMetrics([], latencyP95=4.0))
    rig.api.replica_sets = [rs(1), rs(2, path="/readyz")]
    own_target = f"{NS}/{DEP}"
    rig.metrics.series = [
        {
            "alertname": "NexusCpuAnomaly",
            "nexus_target": own_target,
        },  # the Incident's own
        {"alertname": "NexusTrafficAnomaly", "nexus_target": own_target},
        {"alertname": "NexusCpuAnomaly", "nexus_target": "nexus-prod/sample-api"},
        {
            "alertname": "NexusCpuAnomaly",
            "nexus_target": "nexus-prod/sample-api",
        },  # twice
        {"alertname": "NexusLatencyAnomaly", "namespace": "nexus-prod", "x": "y"},
        {"not": "an alert"},
    ]
    raw, target = collect(rig)
    assert target == own_target
    assert raw["schema"] == "evidence/v1.2"
    assert raw["workload"]["podSpec"][0]["readinessProbe"].startswith("httpGet /readyz")
    assert raw["workload"]["rolloutDiff"][0].startswith("sample-api.readinessProbe:")
    assert raw["cluster"]["activeAlerts"] == [
        {"alertname": "NexusCpuAnomaly", "target": "nexus-prod/sample-api"},
        {"alertname": "NexusLatencyAnomaly", "target": "nexus-prod"},
        {"alertname": "NexusTrafficAnomaly", "target": own_target},
    ]
    assert rig.metrics.queries == [evidence.ACTIVE_ALERTS_QUERY]


def test_the_list_is_capped():
    series = [
        {"alertname": f"NexusX{i:02d}", "nexus_target": "nexus-prod/sample-api"}
        for i in range(30)
    ]
    rig = Rig(collector=True, metrics=AlertMetrics(series, latencyP95=4.0))
    raw, _ = collect(rig)
    assert len(raw["cluster"]["activeAlerts"]) == evidence.MAX_ACTIVE_ALERTS


def test_without_a_vector_query_the_alerts_are_null():
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    assert collect(rig)[0]["cluster"] == {"activeAlerts": None}


def test_prometheus_vector_returns_the_label_sets():
    import asyncio

    from nexus_operator.verifier import Prometheus

    class Resp:
        def __init__(self, body):
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self):
            return self.body

    class Session:
        def __init__(self, body):
            self.body, self.params = body, []

        def get(self, url, params=None):
            self.params.append(params)
            return Resp(self.body)

        async def close(self):
            pass

    async def go(body):
        prom = Prometheus("http://prom.test")
        await prom._session.close()
        prom._session = fake = Session(body)
        return await prom.vector(evidence.ACTIVE_ALERTS_QUERY), fake.params

    ok = {
        "status": "success",
        "data": {
            "result": [{"metric": {"alertname": "NexusCpuAnomaly"}, "value": [0, "1"]}]
        },
    }
    got, params = asyncio.run(go(ok))
    assert got == [{"alertname": "NexusCpuAnomaly"}]
    assert params == [{"query": evidence.ACTIVE_ALERTS_QUERY}]
    assert asyncio.run(go({"status": "error"}))[0] is None
