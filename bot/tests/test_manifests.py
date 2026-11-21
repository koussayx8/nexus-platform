"""The bot's manifests (ADR-040 decisions 1-6): read-only Roles, impersonation of exactly one user
and one group, outbound-only network, restricted pod. Static checks of the YAML in Git."""

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RBAC = ROOT / "platform/rbac/nexus-bot/nexus-bot.yaml"
NET = ROOT / "platform/network/nexus-bot/nexus-bot.yaml"
K8S = ROOT / "bot/k8s"
SA = {"kind": "ServiceAccount", "name": "nexus-bot", "namespace": "nexus-bot"}
READ = {"get", "list"}


def docs(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def by_kind(items: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    return {f"{d['metadata'].get('namespace', '')}/{d['metadata']['name']}": d for d in items if d["kind"] == kind}


def test_impersonation_is_exactly_one_user_and_one_group() -> None:
    roles = by_kind(docs(RBAC), "ClusterRole")
    rules = roles["/nexus-bot-impersonate-approver"]["rules"]
    assert rules == [
        {"apiGroups": [""], "resources": ["users"], "verbs": ["impersonate"], "resourceNames": ["koussay-approver"]},
        {"apiGroups": [""], "resources": ["groups"], "verbs": ["impersonate"], "resourceNames": ["nexus-approvers"]},
    ]


def test_impersonate_appears_nowhere_else() -> None:
    for d in docs(RBAC):
        if d["kind"] in ("Role", "ClusterRole") and d["metadata"]["name"] != "nexus-bot-impersonate-approver":
            assert all("impersonate" not in r["verbs"] for r in d["rules"])


def test_every_other_rule_is_read_only_and_named() -> None:
    for d in docs(RBAC):
        if d["kind"] not in ("Role", "ClusterRole") or d["metadata"]["name"] == "nexus-bot-impersonate-approver":
            continue
        for r in d["rules"]:
            assert set(r["verbs"]) <= READ, (d["metadata"]["name"], r)
            assert "*" not in r["verbs"] + r["resources"] + r["apiGroups"]
            assert not {"secrets", "pods/log", "pods/exec", "serviceaccounts/token", "events"} & set(r["resources"])


def test_read_scope_matches_the_commands() -> None:
    d = docs(RBAC)
    role = by_kind(d, "Role")["nexus-system/nexus-bot-read"]["rules"]
    assert {"apiGroups": ["nexus.io"], "resources": ["incidents"], "verbs": ["get", "list"]} in role
    assert {"apiGroups": [""], "resources": ["configmaps"], "resourceNames": ["nexus-killswitch"], "verbs": ["get"]} in role
    ns = by_kind(d, "ClusterRole")["/nexus-bot-read-namespaces"]["rules"]
    assert ns == [{"apiGroups": [""], "resources": ["namespaces"], "resourceNames": ["nexus-dev", "nexus-prod"],
                   "verbs": ["get"]}]
    # the workload reader is a ClusterRole bound by RoleBinding in exactly the two namespaces
    bindings = by_kind(d, "RoleBinding")
    workload_ns = sorted(k.split("/")[0] for k, b in bindings.items() if b["roleRef"]["name"] == "nexus-bot-read-workloads")
    assert workload_ns == ["nexus-dev", "nexus-prod"]
    assert not any(b["roleRef"]["name"] == "nexus-bot-read-workloads" for b in by_kind(d, "ClusterRoleBinding").values())


def test_every_binding_names_only_the_bot() -> None:
    for d in docs(RBAC):
        if d["kind"] in ("RoleBinding", "ClusterRoleBinding"):
            assert d["subjects"] == [SA]


def test_network_outbound_only() -> None:
    pols = by_kind(docs(NET), "NetworkPolicy")
    deny = pols["nexus-bot/default-deny"]["spec"]
    assert deny == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
    assert not any("ingress" in p["spec"] for p in pols.values())
    egress = pols["nexus-bot/bot-egress"]["spec"]["egress"]
    ports = sorted({(p["protocol"], p["port"]) for rule in egress for p in rule["ports"]})
    assert ports == [("TCP", 53), ("TCP", 443), ("TCP", 6443), ("UDP", 53)]
    public = [r for r in egress if r["to"][0].get("ipBlock", {}).get("cidr") == "0.0.0.0/0"
              and r["ports"] == [{"protocol": "TCP", "port": 443}]]
    assert len(public) == 1 and "10.0.0.0/8" in public[0]["to"][0]["ipBlock"]["except"]


def _deployment() -> dict[str, Any]:
    return docs(K8S / "deployment.yaml")[0]


def test_deployment_is_restricted_single_and_portless() -> None:
    d = _deployment()
    spec = d["spec"]
    assert spec["replicas"] == 1 and spec["strategy"] == {"type": "Recreate"}
    pod = spec["template"]["spec"]
    assert pod["serviceAccountName"] == "nexus-bot"
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"]["runAsUser"] == 10001
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    [c] = pod["containers"]
    assert "ports" not in c
    assert c["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}}
    assert c["image"].startswith("ghcr.io/koussayx8/nexus-platform/nexus-bot@sha256:")


def test_token_is_a_mounted_secret_file_never_an_env_value() -> None:
    pod = _deployment()["spec"]["template"]["spec"]
    [c] = pod["containers"]
    assert all("valueFrom" not in e for e in c.get("env", []))
    assert "envFrom" not in c
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["telegram"]["secret"]["secretName"] == "nexus-bot-telegram"
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["telegram"]["readOnly"] is True
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["NEXUS_BOT_TOKEN_FILE"].startswith(mounts["telegram"]["mountPath"] + "/")


def test_namespace_is_restricted() -> None:
    labels = docs(K8S / "namespace.yaml")[0]["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"


@pytest.mark.parametrize("parent", ["platform/rbac/kustomization.yaml", "platform/network/kustomization.yaml",
                                    "operator/k8s/kustomization.yaml"])
def test_not_wired_into_any_parent(parent: str) -> None:
    text = (ROOT / parent).read_text()
    assert "nexus-bot" not in text and "bot/k8s" not in text


def test_not_wired_into_any_application() -> None:
    for app in (ROOT / "platform/argocd").rglob("*.yaml"):
        text = app.read_text()
        assert "bot/k8s" not in text and "nexus-bot" not in text, app
