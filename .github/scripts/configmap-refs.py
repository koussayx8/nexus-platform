#!/usr/bin/env python3
"""configmap-refs.py — every ConfigMap an Application's workloads reference exists in its render.

Usage: configmap-refs.py <render meta dir>   (the <app>.objects.jsonl files render-apps.sh writes)

A kustomize configMapGenerator renames its ConfigMap with a content hash and rewrites the
references it recognises. When it does not rewrite one (the generator and the workload differ in
namespace), the pod references a name that does not exist and stays in ContainerCreating
(flight-recorder-db-init, promotion 1). kubeconform cannot see that, so this check compares names.

Checked per Application: pod volumes (configMap, projected configMap), envFrom configMapRef and
env valueFrom configMapKeyRef, on every object except a ConfigMap. A reference with optional: true
is skipped. EXTERNAL lists, by name, the ConfigMaps created outside Git-synced manifests.
Exit codes: 0 every reference resolves; 1 at least one does not; 2 usage error.
"""
import glob
import json
import os
import sys

EXTERNAL = {
    "nexus-operator-config",  # admin-owned, created by scripts/bootstrap.sh (ADR-019)
    "nexus-killswitch",       # created by scripts/bootstrap.sh
    "kube-root-ca.crt",       # injected into every namespace by the API server
}


def refs(node, path=""):
    """Yield (path, configmap name, optional) for every ConfigMap reference under node."""
    if isinstance(node, dict):
        for key, val in node.items():
            here = f"{path}.{key}" if path else key
            if key in ("configMap", "configMapRef", "configMapKeyRef") and isinstance(val, dict):
                if val.get("name"):
                    yield here, val["name"], bool(val.get("optional"))
            else:
                yield from refs(val, here)
    elif isinstance(node, list):
        for i, val in enumerate(node):
            yield from refs(val, f"{path}[{i}]")


def check(objects):
    """Return the list of problems for one Application's objects."""
    objects = [o for o in objects if isinstance(o, dict)]
    defined = {o["metadata"]["name"] for o in objects
               if o.get("kind") == "ConfigMap" and o.get("metadata", {}).get("name")}
    problems = []
    for o in objects:
        if o.get("kind") == "ConfigMap":
            continue
        ident = f"{o.get('kind')}/{o.get('metadata', {}).get('name')}"
        for path, name, optional in refs(o.get("spec", {}), "spec"):
            if optional or name in defined or name in EXTERNAL:
                continue
            problems.append(f"{ident}: {path} references ConfigMap '{name}', not in the render"
                            + (f" (did you mean '{sorted(d for d in defined if d.startswith(name))[0]}'?"
                               " a generated name means the generator did not rewrite this reference)"
                               if any(d.startswith(name + "-") for d in defined) else ""))
    return problems


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print(__doc__, file=sys.stderr)
        return 2
    files = sorted(glob.glob(os.path.join(argv[1], "*.objects.jsonl")))
    if not files:
        print("configmap-refs: no rendered Applications found", file=sys.stderr)
        return 2
    failed = 0
    for f in files:
        app = os.path.basename(f)[: -len(".objects.jsonl")]
        with open(f, encoding="utf-8") as fh:
            objs = [json.loads(line) for line in fh if line.strip()]
        problems = check(objs)
        print(f"{app}: {len(objs)} objects, {len(problems)} unresolved ConfigMap reference(s)")
        for p in problems:
            print(f"  FAIL {p}")
        failed += len(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
