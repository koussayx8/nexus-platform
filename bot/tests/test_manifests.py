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


def _namespace() -> dict[str, Any]:
    [ns] = [d for d in docs(ROOT / "platform/namespaces/namespaces.yaml") if d["metadata"]["name"] == "nexus-bot"]
    return ns


def test_namespace_is_restricted_and_has_no_autonomy_level() -> None:
    labels = _namespace()["metadata"]["labels"]
    for mode in ("enforce", "warn", "audit"):
        assert labels[f"pod-security.kubernetes.io/{mode}"] == "restricted"
    assert "nexus.io/autonomy-level" not in labels


def test_namespace_has_one_owner_the_platform_application() -> None:
    # Declared once, in platform/namespaces: the bot's NetworkPolicies (platform Application) and
    # scripts/nexus-bot-secrets.sh need it before the nexus-bot Application syncs.
    assert "namespace.yaml" not in yaml.safe_load((K8S / "kustomization.yaml").read_text())["resources"]
    assert not (K8S / "namespace.yaml").exists()
    assert "namespaces" in yaml.safe_load((ROOT / "platform/kustomization.yaml").read_text())["resources"]


def test_allowlist_is_one_owner_id_mapped_to_the_one_impersonable_user() -> None:
    [cm] = docs(K8S / "configmap.yaml")
    cfg = yaml.safe_load(cm["data"]["config.yaml"])
    assert cfg["allowlist"] == {"8998653791": "koussay-approver"}
    assert cfg["approverGroup"] == "nexus-approvers"
    impersonable = {n for r in by_kind(docs(RBAC), "ClusterRole")["/nexus-bot-impersonate-approver"]["rules"]
                    for n in r["resourceNames"]}
    assert set(cfg["allowlist"].values()) | {cfg["approverGroup"]} == impersonable


@pytest.mark.parametrize(("parent", "entry"), [("platform/rbac/kustomization.yaml", "nexus-bot"),
                                               ("platform/network/kustomization.yaml", "nexus-bot")])
def test_rbac_and_network_are_wired_into_their_parents(parent: str, entry: str) -> None:
    assert entry in yaml.safe_load((ROOT / parent).read_text())["resources"]


def test_operator_application_does_not_carry_the_bot() -> None:
    # Decision 6: nothing of the bot goes through the nexus Application (operator/k8s, nexus-system).
    text = (ROOT / "operator/k8s/kustomization.yaml").read_text()
    assert "nexus-bot" not in text and "bot/k8s" not in text


def test_application_deploys_bot_k8s_in_its_own_namespace() -> None:
    app = docs(ROOT / "platform/argocd/applications/nexus-bot.yaml")[0]
    assert app["kind"] == "Application" and app["metadata"]["name"] == "nexus-bot"
    spec = app["spec"]
    assert spec["project"] == "nexus"
    assert spec["source"] == {"repoURL": "https://github.com/koussayx8/nexus-platform.git",
                              "targetRevision": "main", "path": "bot/k8s"}
    assert spec["destination"] == {"server": "https://kubernetes.default.svc", "namespace": "nexus-bot"}
    assert spec["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
    assert "ignoreDifferences" not in spec
    assert "resources-finalizer.argocd.argoproj.io" in app["metadata"]["finalizers"]


def test_only_the_nexus_bot_application_points_at_bot_k8s() -> None:
    hits = [a.name for a in (ROOT / "platform/argocd").rglob("*.yaml") if "bot/k8s" in a.read_text()]
    assert hits == ["nexus-bot.yaml"]


def test_appproject_admits_the_namespace() -> None:
    [proj] = docs(ROOT / "platform/argocd/projects/nexus.yaml")
    dests = proj["spec"]["destinations"]
    assert {"server": "https://kubernetes.default.svc", "namespace": "nexus-bot"} in dests


# C8 (ADR-041): the GitHub token, the scratch directory and the revert configuration.


def test_github_token_is_an_optional_read_only_secret_file() -> None:
    pod = _deployment()["spec"]["template"]["spec"]
    [c] = pod["containers"]
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["github"]["secret"] == {"secretName": "nexus-bot-github", "defaultMode": 0o440, "optional": True}
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["github"] == {"name": "github", "mountPath": "/var/run/secrets/nexus-bot/github", "readOnly": True}
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["NEXUS_REVERT_TOKEN_FILE"] == "/var/run/secrets/nexus-bot/github/token"
    # the token reaches the bot as this file only: no env value, no other mount of the Secret
    assert sum(1 for v in pod["volumes"] if v.get("secret", {}).get("secretName") == "nexus-bot-github") == 1
    assert all("nexus-bot-github" not in str(e) for e in c["env"])


def test_tmp_is_a_small_emptydir_and_the_root_stays_read_only() -> None:
    pod = _deployment()["spec"]["template"]["spec"]
    [c] = pod["containers"]
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["tmp"] == {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["tmp"] == {"name": "tmp", "mountPath": "/tmp"}
    assert {e["name"]: e["value"] for e in c["env"]}["NEXUS_REVERT_SCRATCH"] == "/tmp"
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    # every other mount is read-only
    assert all(m.get("readOnly") is True for m in c["volumeMounts"] if m["name"] != "tmp")


def test_revert_config_is_the_token_test_outcome_and_maps_nexus_dev_only() -> None:
    from nexus_revert.config import parse

    [cm] = docs(K8S / "configmap.yaml")
    raw = yaml.safe_load(cm["data"]["revert.yaml"])
    assert raw["machineAccount"] == "belhouchetkoussay" and raw["prMode"] == "pull_request"
    assert raw["upstream"] == "koussayx8/nexus-platform" and "fork" not in raw
    # the nexus-dev entry exactly as revert.example.yaml has it; nexus-prod (main) is not mapped
    example = yaml.safe_load((ROOT / "libs/nexus-revert/revert.example.yaml").read_text())
    assert raw["branches"] == example["branches"] == {"nexus-dev": raw["branches"]["nexus-dev"]}
    assert {k: v for k, v in raw.items() if k != "machineAccount"} == \
        {k: v for k, v in example.items() if k != "machineAccount"}
    cfg = parse(raw, "x" * 40)  # the library's own validation accepts it
    assert cfg.fork == "belhouchetkoussay/nexus-platform" and cfg.mode == "pull_request"
    env = {e["name"]: e["value"] for e in _deployment()["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["NEXUS_REVERT_CONFIG"] == "/etc/nexus-bot/revert.yaml"


def test_deployment_header_names_no_placeholder_digest() -> None:
    head = (K8S / "deployment.yaml").read_text().split("apiVersion:")[0]
    assert "placeholder digest until" not in head
    assert "sha256:" + "0" * 64 not in (K8S / "deployment.yaml").read_text()
