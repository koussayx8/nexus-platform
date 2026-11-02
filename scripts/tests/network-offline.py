#!/usr/bin/env python3
"""Offline reachability model of platform/network (spec §18, tests N1-N5).

Reads the NetworkPolicy manifests and answers "would this connection be admitted?" the way the
Kubernetes NetworkPolicy semantics define it (selectors by matchLabels, ipBlock with except,
default-deny when any policy selects the pod for that direction). It does NOT prove that the
cluster's network-policy controller enforces them: that is the live check in
scripts/tests/network-n1-n6.sh (live check pending). N6 (no ServiceAccount token in the Reasoner)
is a property of the Reasoner's pod spec and is live only.

Needs PyYAML only. Usage: python3 scripts/tests/network-offline.py [network-dir]
"""
import ipaddress
import pathlib
import sys

import yaml

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "platform/network")
NAMESPACES = ["nexus-system", "nexus-reasoner", "nexus-dev", "nexus-prod", "nexus-data", "nexus-load"]
# Example addresses only (k3s defaults and the WSL2 node seen at M0); the manifests hardcode none.
POD_CIDR, SVC_CIDR, NODE_IP = "10.42.0.0/16", "10.43.0.0/16", "172.19.233.100"

policies = []
for f in sorted(ROOT.glob("*.yaml")):
    if f.name == "kustomization.yaml":
        continue
    policies += [d for d in yaml.safe_load_all(f.read_text()) if d]
errors: list[str] = []


def err(m: str) -> None:
    errors.append(m)


class Pod:
    def __init__(self, ns, labels, ip):
        self.ns, self.labels, self.ip = ns, labels, ipaddress.ip_address(ip)


def sel_match(sel, labels):
    return all(labels.get(k) == v for k, v in (sel or {}).get("matchLabels", {}).items())


def peer_match(peer, pod: Pod, policy_ns):
    if "ipBlock" in peer:
        b = peer["ipBlock"]
        return pod.ip in ipaddress.ip_network(b["cidr"]) and not any(
            pod.ip in ipaddress.ip_network(e) for e in b.get("except", []))
    ns_ok = True
    if "namespaceSelector" in peer:
        ns_labels = {"kubernetes.io/metadata.name": pod.ns}
        ns_ok = sel_match(peer["namespaceSelector"], ns_labels)
    elif pod.ns != policy_ns:
        return False
    pod_ok = sel_match(peer["podSelector"], pod.labels) if "podSelector" in peer else True
    return ns_ok and pod_ok


def port_ok(rule_ports, port):
    return rule_ports is None or any(p.get("port") == port and p.get("protocol", "TCP") == "TCP" for p in rule_ports)


def direction_ok(pod: Pod, other: Pod, port: int, direction: str) -> bool:
    chosen = [p for p in policies if p["metadata"]["namespace"] == pod.ns
              and direction in p["spec"].get("policyTypes", [])
              and sel_match(p["spec"]["podSelector"], pod.labels)]
    if not chosen:
        return True  # no policy selects the pod: not isolated in this direction
    key, peers = ("egress", "to") if direction == "Egress" else ("ingress", "from")
    for p in chosen:
        for rule in p["spec"].get(key, []):
            if port_ok(rule.get("ports"), port) and any(
                    peer_match(peer, other, p["metadata"]["namespace"]) for peer in rule.get(peers, [])):
                return True
    return False


def connect(src: Pod, dst: Pod, port: int) -> bool:
    return direction_ok(src, dst, port, "Egress") and direction_ok(dst, src, port, "Ingress")


def external(ip):  # not a pod: only the source's egress rules apply
    return Pod("external", {}, ip)


def connect_ext(src: Pod, dst_ip: str, port: int) -> bool:
    return direction_ok(src, external(dst_ip), port, "Egress")


reasoner = Pod("nexus-reasoner", {"app.kubernetes.io/name": "nexus-reasoner"}, "10.42.0.20")
operator = Pod("nexus-system", {"app.kubernetes.io/name": "nexus-operator"}, "10.42.0.10")
frdb = Pod("nexus-system", {"app.kubernetes.io/name": "flight-recorder-db"}, "10.42.0.11")
api_dev = Pod("nexus-dev", {"app.kubernetes.io/name": "sample-api"}, "10.42.0.30")
api_prod = Pod("nexus-prod", {"app.kubernetes.io/name": "sample-api"}, "10.42.0.31")
depdb = Pod("nexus-data", {"app.kubernetes.io/name": "dependency-db"}, "10.42.0.40")
locust_m = Pod("nexus-load", {"app.kubernetes.io/name": "locust", "app.kubernetes.io/component": "master"}, "10.42.0.50")
locust_w = Pod("nexus-load", {"app.kubernetes.io/name": "locust", "app.kubernetes.io/component": "worker"}, "10.42.0.51")
prom = Pod("monitoring", {"app.kubernetes.io/name": "prometheus"}, "10.42.0.60")
grafana = Pod("monitoring", {"app.kubernetes.io/name": "grafana"}, "10.42.0.61")
intruder = Pod("nexus-load", {"app.kubernetes.io/name": "other"}, "10.42.0.99")
svc_ip = str(next(ipaddress.ip_network(SVC_CIDR).hosts()))  # 10.43.0.1, kubernetes.default.svc

checks = [
    # (label, result, expected)
    ("N1 Reasoner -> kubernetes.default.svc:443", connect_ext(reasoner, svc_ip, 443), False),
    ("N2 Reasoner -> node IP:6443", connect_ext(reasoner, NODE_IP, 6443), False),
    ("N2b Reasoner -> node IP:443", connect_ext(reasoner, NODE_IP, 443), False),
    ("N3 Reasoner -> Flight Recorder DB:5432", connect(reasoner, frdb, 5432), False),
    ("N4 Reasoner -> public host:443", connect_ext(reasoner, "203.0.113.10", 443), True),
    ("N4b Reasoner -> public host:80", connect_ext(reasoner, "203.0.113.10", 80), False),
    ("N5 sample-api (dev) -> Flight Recorder DB:5432", connect(api_dev, frdb, 5432), False),
    ("N5 sample-api (prod) -> Flight Recorder DB:5432", connect(api_prod, frdb, 5432), False),
    ("Reasoner -> sample-api:8000", connect(reasoner, api_dev, 8000), False),
    ("operator -> Reasoner:8080", connect(operator, reasoner, 8080), True),
    ("intruder -> Reasoner:8080", connect(intruder, reasoner, 8080), False),
    ("operator -> Flight Recorder DB:5432", connect(operator, frdb, 5432), True),
    ("Grafana -> Flight Recorder DB:5432", connect(grafana, frdb, 5432), True),
    ("Prometheus -> Flight Recorder DB:5432", connect(prom, frdb, 5432), False),
    ("intruder -> Flight Recorder DB:5432", connect(intruder, frdb, 5432), False),
    ("sample-api (dev) -> Dependency DB:5432", connect(api_dev, depdb, 5432), True),
    ("sample-api (prod) -> Dependency DB:5432", connect(api_prod, depdb, 5432), True),
    ("operator -> Dependency DB:5432", connect(operator, depdb, 5432), False),
    ("Reasoner -> Dependency DB:5432", connect(reasoner, depdb, 5432), False),
    ("Locust worker -> sample-api:8000", connect(locust_w, api_dev, 8000), True),
    ("Locust worker -> Locust master:5557", connect(locust_w, locust_m, 5557), True),
    ("Prometheus -> sample-api:8000", connect(prom, api_dev, 8000), True),
    ("intruder -> sample-api:8000", connect(intruder, api_dev, 8000), False),
    ("any pod -> Locust master web UI:8089", connect(intruder, locust_m, 8089), False),
    ("Locust worker -> Locust master web UI:8089", connect(locust_w, locust_m, 8089), False),
    ("Prometheus -> operator:8080", connect(prom, operator, 8080), True),
    ("operator -> Prometheus:9090", connect(operator, prom, 9090), True),
    ("operator -> API server (service IP):443", connect_ext(operator, svc_ip, 443), True),
    ("operator -> API server (node IP):6443", connect_ext(operator, NODE_IP, 6443), True),
    ("operator -> public host:443", connect_ext(operator, "203.0.113.10", 443), False),
]
for label, got, want in checks:
    if got != want:
        err(f"{label}: model says {'allowed' if got else 'denied'}, expected {'allowed' if want else 'denied'}")

# Default-deny in both directions in every NEXUS namespace.
for ns in NAMESPACES:
    ok = any(p["metadata"]["namespace"] == ns and p["spec"]["podSelector"] == {}
             and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"}
             and not p["spec"].get("ingress") and not p["spec"].get("egress") for p in policies)
    if not ok:
        err(f"{ns}: no default-deny for Ingress and Egress")

# The Reasoner's egress must not name the cluster, and the node IP must not be hardcoded.
if NODE_IP in (ROOT / "reasoner.yaml").read_text() + (ROOT / "system.yaml").read_text():
    err("a manifest hardcodes the node IP")

if errors:
    print("network-offline: FAIL")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print(f"network-offline: ok ({len(policies)} policies, {len(checks)} connections modelled)")
