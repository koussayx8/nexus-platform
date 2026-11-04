"""The operator's RBAC boundary, read from the manifests in Git (spec §11, "frozen").

This is the offline half of the boundary test; the live half (a request the API server refuses)
is pending a cluster. It fails if any manifest grants the operator more than the staged §11 rows.
"""

import pathlib

import yaml

RBAC = pathlib.Path(__file__).resolve().parents[3] / "platform" / "rbac"


def docs():
    out = []
    for path in sorted(RBAC.glob("nexus-operator*.yaml")):
        out += [d for d in yaml.safe_load_all(path.read_text()) if d]
    return out


def operator_roles():
    """(namespace or None for a ClusterRole, rule) for every rule bound to the operator."""
    docs_ = docs()
    roles = {
        (d["kind"], d["metadata"].get("namespace"), d["metadata"]["name"]): d
        for d in docs_
    }
    out = []
    for b in docs_:
        if b["kind"] not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        if not any(
            s["kind"] == "ServiceAccount" and s["name"] == "nexus-operator"
            for s in b["subjects"]
        ):
            continue
        ns = b["metadata"].get("namespace")
        ref = b["roleRef"]
        role = roles[(ref["kind"], ns if ref["kind"] == "Role" else None, ref["name"])]
        out += [(ns, rule) for rule in role["rules"]]
    return out


def grants():
    """{(group, resource, verb): namespaces} for the operator."""
    g: dict[tuple, set] = {}
    for ns, rule in operator_roles():
        for group in rule["apiGroups"]:
            for res in rule["resources"]:
                for verb in rule["verbs"]:
                    g.setdefault((group, res, verb), set()).add(ns)
    return g


def test_mutating_rights_are_scale_and_eviction_in_nexus_dev_only():
    g = grants()
    scale = {k: v for k, v in g.items() if k[1] == "deployments/scale"}
    assert {k[2] for k in scale} == {"get", "patch", "update"}
    assert all(v == {"nexus-dev"} for v in scale.values())
    evict = {k: v for k, v in g.items() if k[1] == "pods/eviction"}
    assert set(evict) == {("", "pods/eviction", "create")}
    assert all(v == {"nexus-dev"} for v in evict.values())


def test_no_namespaced_right_reaches_nexus_prod_or_nexus_data():
    for ns, _ in operator_roles():
        assert ns not in ("nexus-prod", "nexus-data")


def test_no_delete_no_wildcards_no_privilege_escalation_verbs():
    banned = {"delete", "deletecollection", "*", "impersonate", "bind", "escalate"}
    for _, rule in operator_roles():
        assert not banned & set(rule["verbs"]), rule
        assert "*" not in rule["resources"] and "*" not in rule["apiGroups"], rule


def test_no_writes_to_secrets_configmaps_namespaces_rbac_or_incident_spec():
    forbidden = {
        "secrets",
        "namespaces",
        "roles",
        "rolebindings",
        "clusterroles",
        "deployments",
    }
    for _group, res, verb in grants():
        if verb in ("create", "patch", "update") and res in forbidden:
            raise AssertionError(f"{verb} on {res}")
    g = grants()
    assert not [k for k in g if k[1] == "configmaps" and k[2] != "get"]
    assert not [
        k for k in g if k[1] == "incidents" and k[2] in ("patch", "update", "delete")
    ]
    assert ("nexus.io", "incidents/status", "patch") in g


def test_every_mutating_rule_names_exactly_two_subresources():
    mutating = set()
    for _ns, rule in operator_roles():
        if set(rule["verbs"]) & {"create", "patch", "update"}:
            mutating |= {(g, r) for g in rule["apiGroups"] for r in rule["resources"]}
    assert mutating == {
        ("apps", "deployments/scale"),
        ("", "pods/eviction"),
        ("nexus.io", "incidents"),
        ("nexus.io", "incidents/status"),
    }


def test_the_evidence_reads_are_cluster_wide_and_read_only():
    cluster = [rule for ns, rule in operator_roles() if ns is None]
    read = {
        (g, r)
        for rule in cluster
        for g in rule["apiGroups"]
        for r in rule["resources"]
        if "pods" in rule["resources"]
        or "deployments" in rule["resources"]
        or "endpointslices" in rule["resources"]
    }
    assert {
        ("", "pods"),
        ("", "pods/log"),
        ("", "events"),
        ("apps", "deployments"),
        ("apps", "replicasets"),
        ("discovery.k8s.io", "endpointslices"),
    } <= read
    for rule in cluster:
        assert set(rule["verbs"]) <= {"get", "list", "watch"}, rule


def test_argocd_applications_are_read_only():
    rules = [rule for ns, rule in operator_roles() if ns == "argocd"]
    assert len(rules) == 1 and rules[0]["resources"] == ["applications"]
    assert set(rules[0]["verbs"]) == {"get", "list", "watch"}


# ---- platform/m3-live: the nexus-prod grants (G8), not deployed until the M3-live PR ----------
M3_LIVE = RBAC.parent / "m3-live"


def prod_docs():
    return [
        d
        for d in yaml.safe_load_all((M3_LIVE / "nexus-operator-prod.yaml").read_text())
        if d
    ]


def test_deployed_rbac_still_stops_at_nexus_dev():
    # the existing tests above read platform/rbac only; m3-live must not leak into it
    assert not any(d["metadata"].get("namespace") == "nexus-prod" for d in docs())


def test_m3_live_grants_scale_and_eviction_in_nexus_prod_only():
    roles = [d for d in prod_docs() if d["kind"] == "Role"]
    bindings = [d for d in prod_docs() if d["kind"] == "RoleBinding"]
    assert len(roles) == len(bindings) == 1
    assert {d["metadata"]["namespace"] for d in prod_docs()} == {"nexus-prod"}
    rules = {
        (g, r): set(rule["verbs"])
        for rule in roles[0]["rules"]
        for g in rule["apiGroups"]
        for r in rule["resources"]
    }
    assert rules == {
        ("apps", "deployments/scale"): {"get", "patch", "update"},
        ("", "pods/eviction"): {"create"},
    }
    assert bindings[0]["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "nexus-operator",
            "namespace": "nexus-system",
        }
    ]
    assert bindings[0]["roleRef"]["name"] == roles[0]["metadata"]["name"]


def test_m3_live_prod_role_equals_the_dev_role():
    dev = next(
        d
        for d in docs()
        if d["kind"] == "Role" and d["metadata"]["name"] == "nexus-operator-act"
    )
    prod = next(d for d in prod_docs() if d["kind"] == "Role")
    assert prod["rules"] == dev["rules"]


def test_m3_live_prod_role_has_no_delete_wildcard_or_nexus_data():
    for d in prod_docs():
        assert d["metadata"]["namespace"] != "nexus-data"
        for rule in d.get("rules", []):
            assert not {
                "delete",
                "deletecollection",
                "*",
                "impersonate",
                "bind",
                "escalate",
            } & set(rule["verbs"])
            assert "*" not in rule["resources"] and "*" not in rule["apiGroups"]


def test_m3_live_kustomization_lists_the_prod_grants_and_platform_does_not_list_m3_live():
    k = yaml.safe_load((M3_LIVE / "kustomization.yaml").read_text())
    assert "nexus-operator-prod.yaml" in k["resources"]
    platform = yaml.safe_load((RBAC.parent / "kustomization.yaml").read_text())
    assert "m3-live" not in platform["resources"]
