#!/usr/bin/env python3
"""G4 / I12: the ignoreDifferences boundary check (spec §9, §13).

Spec §13: "every field reachable through the Action Catalogue and the operator's RBAC is either
delegated by `ignoreDifferences` or never declared in Git". Offline: no cluster, no kustomize. It
reads the working tree and walks each kustomization's `resources` itself (patches never rename a
Deployment, so names are stable).

  B1  every ignoreDifferences entry delegates exactly /spec/replicas of an apps/Deployment, by name
      (no jqPathExpressions, no managedFieldsManagers, no other pointer);
  B2  every Application that has ignoreDifferences also has RespectIgnoreDifferences=true;
  B3  the operator's RBAC (platform/rbac) can write only what the catalogue reaches:
      deployments/scale (patch, update), pods/eviction (create), nexus.io incidents and
      incidents/status. No wildcard, no other write verb on any other resource;
  B4  every namespace where the operator may write deployments/scale: each Deployment that the
      Application tracking it declares has an ignoreDifferences entry for its name;
  B5  in those namespaces no bare Pod is declared (eviction removes a declared Pod for good, which
      would be a desired-state action).

sample-api-dev tracks experiment/dev-state (ADR-013); the check reads the same path in the working
tree, which the forward-merge keeps equal (ADR-021).

Usage: scripts/check-ignore-differences.py [repo-root]    exit 0 clean, 1 violations, 2 unreadable
"""
import json
import pathlib
import subprocess
import sys

try:
    import yaml
except ImportError:  # the repo-checks runner has mikefarah yq on PATH
    yaml = None

DELEGATED = ["/spec/replicas"]
READ_VERBS = {"get", "list", "watch"}
# (apiGroup, resource) -> the write verbs the catalogue and the operator's own CRs may use.
ALLOWED_WRITES = {
    ("apps", "deployments/scale"): {"patch", "update"},
    ("", "pods/eviction"): {"create"},
    ("nexus.io", "incidents"): {"create"},
    ("nexus.io", "incidents/status"): {"patch", "update"},
}


def load_all(path: pathlib.Path) -> list[dict]:
    if yaml is not None:
        docs = list(yaml.safe_load_all(path.read_text()))
    else:
        out = subprocess.run(
            ["yq", "-o=json", "-I=0", ".", str(path)],
            check=True, capture_output=True, text=True,
        ).stdout
        docs = [json.loads(line) for line in out.splitlines() if line.strip()]
    return [d for d in docs if isinstance(d, dict)]


def tree_docs(directory: pathlib.Path, seen: set[pathlib.Path] | None = None) -> list[dict]:
    """Every manifest a kustomization reaches through `resources` (files and directories)."""
    seen = set() if seen is None else seen
    directory = directory.resolve()
    if directory in seen:
        return []
    seen.add(directory)
    kfile = next((directory / n for n in ("kustomization.yaml", "kustomization.yml")
                  if (directory / n).exists()), None)
    if kfile is None:
        return []
    docs: list[dict] = []
    for ref in (load_all(kfile) or [{}])[0].get("resources", []) or []:
        target = (directory / ref).resolve()
        if target.is_dir():
            docs += tree_docs(target, seen)
        elif target.is_file():
            docs += load_all(target)
    return docs


def check(root: pathlib.Path) -> list[str]:
    errors: list[str] = []
    apps = []
    for f in sorted((root / "platform/argocd/applications").glob("*.yaml")):
        apps += [d for d in load_all(f) if d.get("kind") == "Application"]

    # B1, B2
    for app in apps:
        name = app["metadata"]["name"]
        entries = app["spec"].get("ignoreDifferences") or []
        for i, e in enumerate(entries):
            where = f"{name}: ignoreDifferences[{i}]"
            if e.get("group") != "apps" or e.get("kind") != "Deployment" or not e.get("name"):
                errors.append(f"B1 {where} must name an apps/Deployment, got {e.get('group')!r}/{e.get('kind')!r}/{e.get('name')!r}")
            if e.get("jsonPointers") != DELEGATED:
                errors.append(f"B1 {where} delegates {e.get('jsonPointers')!r}; only {DELEGATED} is delegated")
            extra = sorted(set(e) - {"group", "kind", "name", "namespace", "jsonPointers"})
            if extra:
                errors.append(f"B1 {where} has unexpected keys {extra}")
        opts = (app["spec"].get("syncPolicy") or {}).get("syncOptions") or []
        if entries and "RespectIgnoreDifferences=true" not in opts:
            errors.append(f"B2 {name}: ignoreDifferences without RespectIgnoreDifferences=true")

    # B3: what the operator can write
    scale_namespaces: set[str] = set()
    rbac_dir = root / "platform/rbac"
    for doc in tree_docs(rbac_dir):
        if doc.get("kind") not in ("Role", "ClusterRole"):
            continue
        rname = doc["metadata"]["name"]
        ns = doc["metadata"].get("namespace")
        for rule in doc.get("rules") or []:
            writes = set(rule.get("verbs") or []) - READ_VERBS
            if not writes:
                continue
            for g in rule.get("apiGroups") or []:
                for r in rule.get("resources") or []:
                    allowed = ALLOWED_WRITES.get((g, r))
                    if allowed is None or not writes <= allowed:
                        bad = sorted(writes if allowed is None else writes - allowed)
                        errors.append(f"B3 {rname}: write {bad} on {g or 'core'}/{r} is outside the catalogue")
                    elif (g, r) == ("apps", "deployments/scale"):
                        if ns is None:
                            errors.append(f"B3 {rname}: ClusterRole grants deployments/scale cluster-wide")
                        else:
                            scale_namespaces.add(ns)

    # B4, B5
    for ns in sorted(scale_namespaces):
        tracking = [a for a in apps if a["spec"]["destination"].get("namespace") == ns]
        for app in tracking:
            name = app["metadata"]["name"]
            delegated = {e.get("name") for e in app["spec"].get("ignoreDifferences") or []
                         if e.get("jsonPointers") == DELEGATED}
            docs = tree_docs(root / app["spec"]["source"]["path"])
            for d in docs:
                if d.get("kind") == "Deployment" and d["metadata"]["name"] not in delegated:
                    errors.append(f"B4 {name}: Deployment {d['metadata']['name']} in {ns} (operator may scale) has no ignoreDifferences for /spec/replicas")
                if d.get("kind") == "Pod":
                    errors.append(f"B5 {name}: bare Pod {d['metadata']['name']} declared in {ns} (operator may evict)")
    return errors


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    try:
        errors = check(root)
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as e:
        print(f"ERROR: cannot read the tree: {type(e).__name__}: {e}")
        return 2
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print("ok: ignoreDifferences boundary holds (B1-B5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
