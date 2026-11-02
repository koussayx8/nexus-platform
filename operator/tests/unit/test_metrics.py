"""Operator metrics (G3, U3): the exposition format, the counters the act path drives, the served
endpoint, and the manifests that scrape it."""

import asyncio
import pathlib
import socket

import aiohttp
import yaml

from fakes_act import FakeMetrics, proposal_doc
from nexus_operator import metrics
from nexus_operator.config import parse
from rig import Rig, verify

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPECTED = {
    "nexus_operator_incidents_total",
    "nexus_operator_gate_decisions_total",
    "nexus_operator_actions_total",
    "nexus_operator_recorder_errors_total",
    "nexus_operator_redactions_total",
    "nexus_operator_diagnose_seconds_sum",
    "nexus_operator_diagnose_seconds_count",
}


def test_the_registry_renders_prometheus_text():
    r = metrics.Registry()
    r.counter("x_total", "an x")
    r.inc("x_total", a="1", b='q"uote')
    r.inc("x_total", 2, a="1", b='q"uote')
    r.gauge("y", "a y", lambda: {(("loop", "r"),): 1.5, (): 2.0})
    text = r.render()
    assert "# TYPE x_total counter" in text and "# TYPE y gauge" in text
    assert 'x_total{a="1",b="q\\"uote"} 3' in text
    assert 'y{loop="r"} 1.5' in text and "\ny 2\n" in text
    assert r.value("x_total", a="1", b='q"uote') == 3


def test_a_broken_gauge_does_not_break_the_scrape():
    r = metrics.Registry()
    r.gauge("boom", "b", lambda: 1 / 0)
    assert "# TYPE boom gauge" in r.render()


def test_every_proposed_name_is_registered():
    text = metrics.METRICS.render()
    for name in EXPECTED:
        assert f"# TYPE {name} counter" in text, name


def test_the_act_path_drives_the_counters():
    m = metrics.METRICS
    base = {
        "gate": m.value(metrics.GATE, stage="3", verdict="pass"),
        "act": m.value(
            metrics.ACTIONS, action_id="scale_deployment", result="admitted"
        ),
        "done": m.value(
            metrics.INCIDENTS, phase="Resolved", reason="remediation_verified"
        ),
        "blocked": m.value(
            metrics.INCIDENTS, phase="Blocked", reason="bounds_exceeded"
        ),
    }
    rig = Rig(metrics=FakeMetrics())
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig)
    rig2 = Rig()
    rig2.proposed(proposal_doc("scale_deployment", 40))
    rig2.run()
    assert m.value(metrics.GATE, stage="3", verdict="pass") == base["gate"] + 1
    assert (
        m.value(metrics.ACTIONS, action_id="scale_deployment", result="admitted")
        == base["act"] + 1
    )
    assert (
        m.value(metrics.INCIDENTS, phase="Resolved", reason="remediation_verified")
        == base["done"] + 1
    )
    assert (
        m.value(metrics.INCIDENTS, phase="Blocked", reason="bounds_exceeded")
        == base["blocked"] + 1
    )


def test_the_metrics_endpoint_is_served():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    async def go():
        runner = await metrics.serve(port)
        try:
            async with (
                aiohttp.ClientSession() as c,
                c.get(f"http://127.0.0.1:{port}/metrics") as r,
            ):
                return r.status, r.headers["Content-Type"], await r.text()
        finally:
            await runner.cleanup()

    status, ctype, body = asyncio.run(go())
    assert status == 200 and ctype.startswith("text/plain")
    assert "nexus_operator_incidents_total" in body


# ---------------------------------------------------------------- the manifests that scrape it
def load(path):
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


def test_servicemonitor_selects_the_metrics_service_and_its_port():
    [sm] = load("platform/observability/monitors/nexus-operator-servicemonitor.yaml")
    [svc] = load("operator/k8s/metrics-service.yaml")
    dep = next(
        d for d in load("operator/k8s/deployment.yaml") if d["kind"] == "Deployment"
    )
    assert sm["metadata"]["labels"]["release"] == "observability"
    assert sm["spec"]["namespaceSelector"]["matchNames"] == [
        svc["metadata"]["namespace"]
    ]
    want = sm["spec"]["selector"]["matchLabels"]
    assert all(svc["metadata"]["labels"].get(k) == v for k, v in want.items())
    assert (
        sm["spec"]["endpoints"][0]["port"]
        == svc["spec"]["ports"][0]["name"]
        == "metrics"
    )
    ports = dep["spec"]["template"]["spec"]["containers"][0]["ports"]
    assert {"name": "metrics", "containerPort": 9102, "protocol": "TCP"} in ports
    assert svc["spec"]["ports"][0]["port"] == 9102
    assert svc["spec"]["selector"] == dep["spec"]["selector"]["matchLabels"]


def test_the_network_policy_lets_prometheus_reach_the_metrics_port():
    docs = load("platform/network/system.yaml")
    ingress = next(d for d in docs if d["metadata"]["name"] == "operator-ingress")
    ports = {p["port"] for rule in ingress["spec"]["ingress"] for p in rule["ports"]}
    assert {8080, 9102} <= ports


def test_the_bootstrap_template_parses_with_the_writer_and_metrics_keys():
    [cm] = load("platform/bootstrap-templates/nexus-operator-config.yaml")
    cfg = parse(cm)
    assert cfg.recorder_host == "flight-recorder-db.nexus-system.svc"
    assert cfg.recorder_port == 5432 and cfg.recorder_user == "nexus_writer"
    assert (
        cfg.recorder_password_file == "/var/run/nexus/flight-recorder-writer/password"
    )
    assert cfg.metrics_port == 9102 and cfg.prometheus_url.endswith(":9090")


def test_the_deployment_mounts_only_the_writer_secret_at_the_configured_path():
    dep = next(
        d for d in load("operator/k8s/deployment.yaml") if d["kind"] == "Deployment"
    )
    pod = dep["spec"]["template"]["spec"]
    [vol] = pod["volumes"]
    assert (
        vol["secret"]["secretName"] == "flight-recorder-writer"
        and vol["secret"]["optional"]
    )
    [mount] = pod["containers"][0]["volumeMounts"]
    assert (
        mount["mountPath"] == "/var/run/nexus/flight-recorder-writer"
        and mount["readOnly"]
    )
    assert (
        pod["securityContext"]["fsGroup"] == 10001
    )  # the pod user can read the 0440 file
