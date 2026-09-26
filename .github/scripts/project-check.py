#!/usr/bin/env python3
"""Check that every rendered object is admitted by its Application's AppProject (ADR-012).

Reads <meta>/<app>.app.json, <meta>/<app>.objects.jsonl and <meta>/project.<name>.json, as written
by render-apps.sh. For each Application it checks the destination namespace, the source
repositories and, for every rendered object, the cluster or namespace resource lists and the
object's namespace. Standard library only. Exit 1 on any violation.
"""
import fnmatch
import json
import pathlib
import sys
from collections import Counter

# Built-in cluster-scoped kinds; cluster-scoped CRDs are added from the rendered CRDs.
CLUSTER_SCOPED = {
    ("", "Namespace"), ("", "Node"), ("", "PersistentVolume"),
    ("rbac.authorization.k8s.io", "ClusterRole"), ("rbac.authorization.k8s.io", "ClusterRoleBinding"),
    ("apiextensions.k8s.io", "CustomResourceDefinition"),
    ("admissionregistration.k8s.io", "MutatingWebhookConfiguration"),
    ("admissionregistration.k8s.io", "ValidatingWebhookConfiguration"),
    ("admissionregistration.k8s.io", "ValidatingAdmissionPolicy"),
    ("admissionregistration.k8s.io", "ValidatingAdmissionPolicyBinding"),
    ("scheduling.k8s.io", "PriorityClass"), ("storage.k8s.io", "StorageClass"),
    ("storage.k8s.io", "CSIDriver"), ("networking.k8s.io", "IngressClass"),
    ("node.k8s.io", "RuntimeClass"), ("apiregistration.k8s.io", "APIService"),
}


def group_of(obj):
    api = obj.get("apiVersion", "")
    return api.split("/")[0] if "/" in api else ""


def matches(rules, group, kind):
    return any(fnmatch.fnmatchcase(group, r.get("group", "")) and fnmatch.fnmatchcase(kind, r.get("kind", ""))
               for r in rules or [])


def is_helm_test(obj):
    hook = ((obj.get("metadata") or {}).get("annotations") or {}).get("helm.sh/hook", "")
    return "test" in hook  # ArgoCD ignores Helm test hooks


def main(meta):
    meta = pathlib.Path(meta)
    projects = {p.name[len("project."):-len(".json")]: json.loads(p.read_text()) for p in meta.glob("project.*.json")}
    apps = {}
    for a in sorted(meta.glob("*.app.json")):
        name = a.name[:-len(".app.json")]
        objs = [json.loads(line) for line in (meta / f"{name}.objects.jsonl").read_text().splitlines() if line.strip()]
        apps[name] = (json.loads(a.read_text()), [o for o in objs if isinstance(o, dict) and o.get("kind")])

    cluster_scoped = set(CLUSTER_SCOPED)
    for _, objs in apps.values():
        for o in objs:
            if o["kind"] == "CustomResourceDefinition" and (o.get("spec") or {}).get("scope") == "Cluster":
                cluster_scoped.add((o["spec"]["group"], o["spec"]["names"]["kind"]))

    failures = 0
    for name, (app, objs) in apps.items():
        spec = app["spec"]
        proj_name = spec.get("project", "default")
        proj = projects.get(proj_name)
        problems = []
        if proj is None:
            problems.append(f"AppProject {proj_name!r} is not defined in platform/argocd/projects/")
            pspec = {}
        else:
            pspec = proj["spec"]
        dests = pspec.get("destinations", [])
        dest_ns = (spec.get("destination") or {}).get("namespace", "")

        def ns_allowed(ns):
            return any(fnmatch.fnmatchcase(ns, d.get("namespace", "")) for d in dests)

        if proj is not None and dest_ns and not ns_allowed(dest_ns):
            problems.append(f"destination namespace {dest_ns!r} not in project destinations")
        sources = spec.get("sources") or [spec.get("source")]
        for s in sources:
            if proj is not None and not any(fnmatch.fnmatchcase(s["repoURL"], r) for r in pspec.get("sourceRepos", [])):
                problems.append(f"source repository {s['repoURL']} not in project sourceRepos")

        kinds = Counter()
        for o in objs:
            if is_helm_test(o):
                continue
            g, k = group_of(o), o["kind"]
            kinds[k] += 1
            if proj is None:
                continue
            md = o.get("metadata") or {}
            ident = f"{k}/{md.get('name', '?')}"
            if (g, k) in cluster_scoped:
                if not matches(pspec.get("clusterResourceWhitelist"), g, k):
                    problems.append(f"cluster-scoped {g or 'core'}/{k} not in clusterResourceWhitelist ({ident})")
                if matches(pspec.get("clusterResourceBlacklist"), g, k):
                    problems.append(f"cluster-scoped {g or 'core'}/{k} is blacklisted ({ident})")
            else:
                wl = pspec.get("namespaceResourceWhitelist")
                if wl is not None and not matches(wl, g, k):
                    problems.append(f"namespaced {g or 'core'}/{k} not in namespaceResourceWhitelist ({ident})")
                if matches(pspec.get("namespaceResourceBlacklist"), g, k):
                    problems.append(f"namespaced {g or 'core'}/{k} is blacklisted ({ident})")
                ns = md.get("namespace") or dest_ns
                if not ns:
                    problems.append(f"namespaced {ident} has no namespace and the Application has no destination namespace")
                elif not ns_allowed(ns):
                    problems.append(f"{ident} targets namespace {ns!r}, not in project destinations")

        summary = ", ".join(f"{k}:{c}" for k, c in sorted(kinds.items()))
        print(f"{name}: project={proj_name} objects={sum(kinds.values())} [{summary}]")
        for p in sorted(set(problems)):
            print(f"  VIOLATION: {p}")
        failures += len(set(problems))
    if failures:
        print(f"AppProject check: {failures} violation(s)")
        return 1
    print("AppProject check: every rendered object is admitted by its project")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
