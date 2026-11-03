"""The live containment check (spec section 20): every verdict of the matrix by server-side dry-run.

LIVE CHECK PENDING: nothing here has run against a cluster. It is read-only apart from the dry-run
requests themselves, which the API server evaluates through RBAC, CRD validation and admission and
never persists. It changes no state: namespace levels, Kill Switch states and Incident fixtures are
set up beforehand by the Cluster Admin (README.md), and the script only checks that they are as the
cell expects before it asks.

  python3 -m experiments.containment.live --plan                    # print the commands, touch nothing
  python3 -m experiments.containment.live --ks-state active --run   # 210 cells, one Kill Switch state
  python3 -m experiments.containment.live --special fail-closed --run

Each request is made by impersonation (`--as`, `--as-group`) with `--dry-run=server`. A cell passes
when the observed verdict equals the declared one and, for a denial, the observed layer is one of
the declared layers. An answer the classifier cannot place is a failure, never a pass.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

from . import matrix as mx

CELL_OBJECTS = {  # the Deployment each cell's request names
    "nexus-dev": "sample-api",
    "nexus-prod": "sample-api",
    "nexus-data": "dependency-db",
    "kube-system": "coredns",
    "nexus-containment-unlabelled": "probe",
}
INCIDENT_FOR = {  # the fixture Incident a set-approval request patches; none for C3 cells
    "dev-l3": "cont-dev-l3",
    "dev-l2-approved": "cont-dev-l2-approved-scale",
    "dev-l2-none": "cont-dev-l2-none",
    "prod-l1": "cont-prod-l1",
    "data-l0": "cont-data-l0",
}
FIXTURE_NAMES = sorted(set(INCIDENT_FOR.values()) | {"cont-dev-l2-approved-restart"})
KYVERNO_DEPLOYMENT = ("kyverno", "kyverno-admission-controller")
SELECTOR = "app.kubernetes.io/name=sample-api"


@dataclass
class Step:
    cell: mx.Cell
    variant: int
    argv: list[str]
    stdin: str | None
    expected: mx.Verdict
    special: str | None = None

    def shell(self) -> str:
        return " ".join(self.argv) + (f" <<< '{self.stdin}'" if self.stdin else "")


def impersonation(identity: str) -> list[str]:
    ident = mx.DATA["identities"][identity]
    out = [f"--as={ident['username']}"]
    for g in ident["groups"]:
        if not g.startswith("system:authenticated"):
            out.append(f"--as-group={g}")
    return out


def approval_body(expires: str) -> dict:
    return {
        "decision": "approved",
        "actionId": "scale_deployment",
        "parameters": {"replicas": 3},
        "proposalHash": "sha256:ab12",
        "expiresAt": expires,
    }


def build(
    cell: mx.Cell,
    expected: mx.Verdict,
    now: datetime.datetime,
    pod: str = "sample-api-pod",
    special: str | None = None,
) -> list[Step]:
    """The kubectl commands that make this cell's request (one per variant)."""
    req, c = mx.request_def(cell.request), mx.DATA["cells"][cell.cell]
    ns, deploy, kind = c["namespace"], CELL_OBJECTS[c["namespace"]], req["kind"]
    who, dry = impersonation(cell.identity), ["--dry-run=server"]
    steps: list[Step] = []

    def add(argv: list[str], stdin: str | None = None, variant: int = 0) -> None:
        steps.append(
            Step(cell, variant, ["kubectl", *argv, *who], stdin, expected, special)
        )

    if kind == "scale":
        for i, v in enumerate(req["variants"]):
            add(
                [
                    "scale",
                    f"deployment/{deploy}",
                    "-n",
                    ns,
                    f"--replicas={v['replicas']}",
                    *dry,
                ],
                variant=i,
            )
    elif kind == "evict":
        body = {
            "apiVersion": "policy/v1",
            "kind": "Eviction",
            "metadata": {"name": pod, "namespace": ns},
            "deleteOptions": {"dryRun": ["All"]},
        }
        add(
            [
                "create",
                "--raw",
                f"/api/v1/namespaces/{ns}/pods/{pod}/eviction",
                "-f",
                "-",
            ],
            json.dumps(body),
        )
    elif kind == "delete_deployment":
        add(["delete", f"deployment/{deploy}", "-n", ns, *dry])
    elif kind == "patch_deployment":
        add(
            [
                "patch",
                f"deployment/{deploy}",
                "-n",
                ns,
                "--type=merge",
                "-p",
                '{"spec":{"replicas":3}}',
                *dry,
            ]
        )
    elif kind == "set_approval":
        expires = (now + datetime.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if cell.cell in INCIDENT_FOR:
            patch = json.dumps({"spec": {"approval": approval_body(expires)}})
            add(
                [
                    "patch",
                    "incidents.nexus.io",
                    INCIDENT_FOR[cell.cell],
                    "-n",
                    "nexus-system",
                    "--type=merge",
                    "-p",
                    patch,
                    *dry,
                ]
            )
        else:  # no Incident can exist for this namespace (C3): ask to create one
            inc = {
                "apiVersion": "nexus.io/v1alpha1",
                "kind": "Incident",
                "metadata": {"name": f"cont-{cell.cell}", "namespace": "nexus-system"},
                "spec": {
                    "source": {"type": "human"},
                    "target": {"namespace": ns, "kind": "Deployment", "name": deploy},
                    "detectedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "approval": approval_body(expires),
                },
            }
            add(["create", "-f", "-", *dry], json.dumps(inc))
    elif kind == "change_ns_label":
        add(
            ["label", "namespace", ns, "nexus.io/autonomy-level=3", "--overwrite", *dry]
        )
    elif kind == "change_killswitch":
        add(
            [
                "patch",
                "configmap",
                "nexus-killswitch",
                "-n",
                "nexus-system",
                "--type=merge",
                "-p",
                '{"data":{"state":"halted"}}',
                *dry,
            ]
        )
    elif kind == "create_rolebinding":
        add(
            [
                "create",
                "rolebinding",
                "cont-probe",
                "-n",
                ns,
                "--clusterrole=view",
                "--user=nobody",
                *dry,
            ]
        )
    else:
        raise ValueError(f"no command for request kind {kind}")
    return steps


def plan(
    now: datetime.datetime, ks: str | None = None, cells: set[str] | None = None
) -> list[Step]:
    out: list[Step] = []
    for cell, want in mx.expand():
        if (ks and cell.killswitch != ks) or (cells and cell.cell not in cells):
            continue
        out += build(cell, want, now)
    return out


# ---------------------------------------------------------------- classification
@dataclass
class Observed:
    verdict: str  # ADMIT | DENY | UNKNOWN
    layers: set[str] = field(default_factory=set)
    detail: str = ""


POLICY_LAYER = {
    "nexus-autonomy-floor": "K1",
    "nexus-scale-bounds": "K2",
    "nexus-l2-approval": "K3",
    "nexus-killswitch": "K4",
    "nexus-incident-approval": "K5",
    "nexus-incident-source": "K6",
}


def classify(returncode: int, out: str, err: str) -> Observed:
    """Place a kubectl answer: admitted, or refused by which layer. Anything else is UNKNOWN."""
    text = f"{out}\n{err}"
    if returncode == 0:
        return Observed("ADMIT", detail="2xx")
    low = text.lower()
    if (
        "cannot evict pod as it would violate the pod's disruption budget" in low
        or "too many requests" in low
    ):
        return Observed("ADMIT", detail="PDB (a layer after Kyverno)")
    if "failed calling webhook" in low or "no endpoints available for service" in low:
        return Observed("DENY", {"webhook-unavailable"}, "fail-closed")
    layers: set[str] = set()
    if "admission webhook" in low and "denied the request" in low:
        layers |= {POLICY_LAYER[p] for p in POLICY_LAYER if p in text}
        layers |= set(re.findall(r"\b(K[1-6])\b", text))
        return Observed("DENY", layers or {"K?"}, "admission")
    m = re.search(r"\b(C[1-4]):", text)
    if m:
        return Observed("DENY", {m[1]}, "CRD validation")
    if "forbidden" in low:
        return Observed("DENY", {"RBAC"}, "forbidden")
    return Observed("UNKNOWN", detail=text.strip()[:300])


def judge(step_expected: mx.Verdict, got: Observed) -> tuple[bool, str]:
    if got.verdict == "UNKNOWN":
        return False, f"unplaceable answer: {got.detail}"
    if got.verdict != step_expected.verdict:
        return (
            False,
            f"expected {step_expected}, observed {got.verdict} {sorted(got.layers)}",
        )
    if got.verdict == "DENY" and not (got.layers & step_expected.layers):
        return (
            False,
            f"expected layers {sorted(step_expected.layers)}, observed {sorted(got.layers)}",
        )
    extra = sorted(got.layers - step_expected.layers) if got.verdict == "DENY" else []
    return True, f"extra layers {extra}" if extra else ""


# ---------------------------------------------------------------- running
def kubectl(
    argv: list[str], stdin: str | None = None, timeout: int = 60
) -> tuple[int, str, str]:
    p = subprocess.run(
        argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
    )
    return p.returncode, p.stdout, p.stderr


def preflight(ks: str, cells: set[str]) -> list[str]:
    """Read-only: the cluster is in the state the cells assume. Returns the problems."""
    problems: list[str] = []
    rc, out, err = kubectl(
        [
            "kubectl",
            "get",
            "configmap",
            "nexus-killswitch",
            "-n",
            "nexus-system",
            "-o",
            "jsonpath={.data.state}",
        ]
    )
    if rc != 0 and "NotFound" not in err:
        problems.append(f"cannot read the Kill Switch: {err.strip()[:200]}")
    else:
        state = (
            "absent" if rc != 0 else ("active" if out.strip() == "active" else "halted")
        )
        if state != ks:
            problems.append(f"the Kill Switch is {state}, this pass is for {ks}")
    for cid in sorted(cells):
        c = mx.DATA["cells"][cid]
        rc, out, err = kubectl(
            [
                "kubectl",
                "get",
                "namespace",
                c["namespace"],
                "-o",
                r"jsonpath={.metadata.labels.nexus\.io/autonomy-level}",
            ]
        )
        if rc != 0:
            problems.append(f"{c['namespace']}: {err.strip()[:160]}")
        elif out.strip() != (c["level"] or ""):
            problems.append(
                f"{c['namespace']} has level {out.strip() or 'none'!r}, cell {cid} needs {c['level'] or 'none'!r}"
            )
    for inc in FIXTURE_NAMES:
        rc, _, err = kubectl(
            [
                "kubectl",
                "get",
                "incidents.nexus.io",
                inc,
                "-n",
                "nexus-system",
                "-o",
                "name",
            ]
        )
        if rc != 0:
            problems.append(
                f"fixture Incident {inc} missing (run fixtures/fixtures.sh up)"
            )
    return problems


def pod_for(ns: str) -> str:
    rc, out, _ = kubectl(
        [
            "kubectl",
            "get",
            "pod",
            "-n",
            ns,
            "-l",
            SELECTOR,
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ]
    )
    return out.strip() if rc == 0 and out.strip() else "probe-pod"


def run(steps: list[Step]) -> list[dict]:
    results: list[dict] = []
    pods: dict[str, str] = {}
    for s in steps:
        if s.argv[:3] == ["kubectl", "create", "--raw"]:
            ns = mx.DATA["cells"][s.cell.cell]["namespace"]
            pod = pods.setdefault(ns, pod_for(ns))
            s.argv = [a.replace("sample-api-pod", pod) for a in s.argv]
            s.stdin = (s.stdin or "").replace("sample-api-pod", pod)
        rc, out, err = kubectl(s.argv, s.stdin)
        got = classify(rc, out, err)
        ok, note = judge(s.expected, got)
        results.append(
            {
                "cell": s.cell.key,
                "variant": s.variant,
                "special": s.special,
                "pass": ok,
                "expected": str(s.expected),
                "observed": f"{got.verdict} {sorted(got.layers)}",
                "note": note,
                "detail": got.detail,
            }
        )
    return results


def special_steps(name: str, now: datetime.datetime) -> list[Step]:
    for sid, cell, want in mx.specials():
        if sid == name:
            return build(cell, want, now, special=sid)
    raise KeyError(name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--ks-state",
        choices=mx.KILLSWITCH,
        help="the Kill Switch state this pass runs in",
    )
    ap.add_argument("--cells", help="comma-separated cell ids (default all)")
    ap.add_argument(
        "--special",
        choices=[
            "fail-closed",
            "killswitch-halted-compensation",
            "killswitch-absent-compensation",
        ],
    )
    ap.add_argument(
        "--plan",
        action="store_true",
        help="print the commands and exit; nothing is sent",
    )
    ap.add_argument("--run", action="store_true", help="send the dry-run requests")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--out", help="write the JSON report here")
    a = ap.parse_args(argv)
    now = datetime.datetime.now(datetime.UTC)
    cells = set(a.cells.split(",")) if a.cells else None
    if a.special:
        steps = special_steps(a.special, now)
    else:
        steps = plan(now, a.ks_state, cells)
    if a.plan or not a.run:
        for s in steps:
            print(f"{s.cell.key}  ->  {s.expected}\n    {s.shell()}")
        print(f"{len(steps)} requests planned", file=sys.stderr)
        return 0
    if not a.special and not a.no_preflight:
        if not a.ks_state:
            ap.error("--run needs --ks-state")
        bad = preflight(a.ks_state, cells or set(mx.CELLS))
        if bad:
            print("preflight failed:\n  " + "\n  ".join(bad), file=sys.stderr)
            return 2
    results = run(steps)
    failed = [r for r in results if not r["pass"]]
    if a.out:
        with open(a.out, "w") as f:
            json.dump(results, f, indent=1)
    for r in failed:
        print(f"FAIL {r['cell']} v{r['variant']}: {r['note']}")
    print(f"{len(results) - len(failed)}/{len(results)} requests as declared")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
