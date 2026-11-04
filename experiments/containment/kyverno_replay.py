"""Offline replay of the matrix through the Kyverno CLI with mocked context (spec section 20, NF-26).

For one rule and one set of inputs, `Kyverno.rule()` writes a `kyverno test` case, asks the CLI
whether the rule passes, fails or is skipped for that request, and returns allow, deny or n/a. The CLI
signals only through its exit status, so the three expectations are tried in turn; the first that
the CLI accepts is the verdict. A self-check (`calibrate()`) first proves the CLI tells a pass from
a fail on the real K4 policy, so a CLI that accepts everything cannot make the replay look green.
Context lookups (namespace label, Incident list, Kill Switch) are mocked through the values file, as
in platform/policies/tests; verdicts are cached per rule and inputs, so the 630-cell matrix needs
only a few dozen CLI runs.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile

import yaml

from . import matrix as mx

POLICIES = mx.REPO / "platform" / "policies"
POLICY_FILES = {
    "K1": ("k1-autonomy-floor.yaml", "nexus-autonomy-floor", "autonomy-floor"),
    "K2": ("k2-scale-bounds.yaml", "nexus-scale-bounds", "scale-bounds"),
    "K3-scale": ("k3-l2-approval.yaml", "nexus-l2-approval", "scale-needs-approval"),
    "K3-evict": ("k3-l2-approval.yaml", "nexus-l2-approval", "eviction-needs-approval"),
    "K4": ("k4-killswitch.yaml", "nexus-killswitch", "killswitch-active"),
    "K5-approvers-only": (
        "k5-incident-approval.yaml",
        "nexus-incident-approval",
        "approvers-only",
    ),
    "K5-expiry-max": (
        "k5-incident-approval.yaml",
        "nexus-incident-approval",
        "expiry-max-15m",
    ),
    "K5-hash": (
        "k5-incident-approval.yaml",
        "nexus-incident-approval",
        "bind-proposal-hash",
    ),
    "K5-action": (
        "k5-incident-approval.yaml",
        "nexus-incident-approval",
        "bind-action",
    ),
    "K5-replicas": (
        "k5-incident-approval.yaml",
        "nexus-incident-approval",
        "bind-replicas",
    ),
}
SUBRESOURCES = [
    {
        "subresource": {
            "name": "deployments/scale",
            "kind": "Scale",
            "group": "autoscaling",
            "version": "v1",
        },
        "parentResource": {
            "name": "deployments",
            "kind": "Deployment",
            "group": "apps",
            "version": "v1",
        },
    },
    {
        "subresource": {
            "name": "pods/eviction",
            "kind": "Eviction",
            "group": "policy",
            "version": "v1",
        },
        "parentResource": {"name": "pods", "kind": "Pod", "group": "", "version": "v1"},
    },
]
FUTURE = "2099-01-01T00:00:00Z"
NAME = "probe"  # the Deployment, and the pod prefix, every replayed request names


class ReplayError(RuntimeError):
    pass


def _dump(doc) -> str:
    return yaml.safe_dump(doc, sort_keys=True)


class Kyverno:
    def __init__(self, binary: str = "kyverno") -> None:
        self.binary = binary
        self.cache: dict[str, str] = {}
        self.runs = 0

    # ------------------------------------------------------------ one CLI run
    def _accepts(
        self,
        policy_file: str,
        policy: str,
        rule: str,
        kind: str,
        resource: dict,
        values: dict,
        user: dict,
        want: str,
    ) -> bool:
        with tempfile.TemporaryDirectory(prefix="kyverno-replay-") as tmp:
            d = pathlib.Path(tmp)
            name = resource["metadata"]["name"]
            test = {
                "apiVersion": "cli.kyverno.io/v1alpha1",
                "kind": "Test",
                "metadata": {"name": "replay"},
                "policies": [str(POLICIES / policy_file)],
                "resources": ["resources.yaml"],
                "variables": "values.yaml",
                "userinfo": "userinfo.yaml",
                "results": [
                    {
                        "policy": policy,
                        "rule": rule,
                        "resources": [name],
                        "kind": kind,
                        "result": want,
                    }
                ],
            }
            vals = {
                "apiVersion": "cli.kyverno.io/v1alpha1",
                "kind": "Values",
                "subresources": SUBRESOURCES,
                "policies": [
                    {"name": policy, "resources": [{"name": name, "values": values}]}
                ],
            }
            (d / "kyverno-test.yaml").write_text(_dump(test))
            (d / "resources.yaml").write_text(_dump(resource))
            (d / "values.yaml").write_text(_dump(vals))
            (d / "userinfo.yaml").write_text(
                _dump(
                    {
                        "apiVersion": "cli.kyverno.io/v1alpha1",
                        "kind": "UserInfo",
                        "metadata": {"name": "u"},
                        "userInfo": user,
                    }
                )
            )
            self.runs += 1
            p = subprocess.run(
                [self.binary, "test", str(d)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.last_output = (p.stdout + p.stderr)[-2000:]
            return p.returncode == 0

    def rule(
        self, key: str, kind: str, resource: dict, values: dict, user: dict
    ) -> str:
        """allow | deny | n/a for one rule of K1-K4 (key from POLICY_FILES)."""
        policy_file, policy, rule = POLICY_FILES[key]
        ck = json.dumps(
            [key, kind, resource, values, user], sort_keys=True, default=str
        )
        if ck in self.cache:
            return self.cache[ck]
        for want, out in (("pass", "allow"), ("fail", "deny"), ("skip", "n/a")):
            if self._accepts(
                policy_file, policy, rule, kind, resource, values, user, want
            ):
                self.cache[ck] = out
                return out
        raise ReplayError(
            f"the Kyverno CLI accepted no verdict for {key}: {self.last_output}"
        )

    def calibrate(self) -> None:
        """The CLI must distinguish allow from deny on a request whose answer is known."""
        user = {
            "username": mx.DATA["identities"]["operator"]["username"],
            "groups": ["system:serviceaccounts"],
        }
        res = {
            "apiVersion": "autoscaling/v1",
            "kind": "Scale",
            "metadata": {"name": NAME, "namespace": "nexus-dev"},
            "spec": {"replicas": 3},
        }
        a = self.rule(
            "K4",
            "Scale",
            res,
            {"killswitchState": "active", "request.operation": "UPDATE"},
            user,
        )
        b = self.rule(
            "K4",
            "Scale",
            res,
            {"killswitchState": "halted", "request.operation": "UPDATE"},
            user,
        )
        if (a, b) != ("allow", "deny"):
            raise ReplayError(f"calibration failed: K4 active -> {a}, halted -> {b}")


# ---------------------------------------------------------------- the matrix
def _incidents(cell: dict) -> list[dict]:
    def inc(action: str, replicas: int | None, approved: bool) -> dict:
        d = {
            "spec": {
                "target": {
                    "namespace": cell["namespace"],
                    "kind": "Deployment",
                    "name": NAME,
                }
            },
            "status": {"proposal": {"hash": "sha256:ab12"}},
        }
        if approved:
            a = {
                "decision": "approved",
                "actionId": action,
                "proposalHash": "sha256:ab12",
                "expiresAt": FUTURE,
            }
            if replicas is not None:
                a["parameters"] = {"replicas": replicas}
            d["spec"]["approval"] = a
        return d

    if cell["approval"] == "approved":
        return [
            inc(
                "scale_deployment",
                mx.DATA["approved_fixtures"]["scale"]["replicas"],
                True,
            ),
            inc("restart_workload", None, True),
        ]
    return [inc("scale_deployment", 3, False)]


def request_layers(
    ky: Kyverno,
    user: dict,
    kind: str,
    namespace: str,
    level: str,
    ks: str,
    new: int | None = None,
    old: int | None = None,
    incidents: list[dict] | None = None,
) -> frozenset[str]:
    """The K1-K4 policies that refuse one scale ("scale", new and old replicas) or eviction ("evict")
    by an operator or baseline identity, as the Kyverno CLI evaluates them. `level` is the namespace
    label ("0".."3", or "none"), `ks` is active, halted or missing, `incidents` the mocked Incident list."""
    found: set[str] = set()
    if kind == "scale":
        res = {
            "apiVersion": "autoscaling/v1",
            "kind": "Scale",
            "metadata": {"name": NAME, "namespace": namespace},
            "spec": {"replicas": new},
        }
        rkind, ops = "Scale", {"request.operation": "UPDATE"}
    else:
        res = {
            "apiVersion": "policy/v1",
            "kind": "Eviction",
            "metadata": {"name": f"{NAME}-6d9f7c8b5-x2k4q", "namespace": namespace},
        }
        rkind, ops = "Eviction", {"request.operation": "CREATE"}
    if ky.rule("K1", rkind, res, {"level": level, **ops}, user) == "deny":
        found.add("K1")
    if (
        kind == "scale"
        and ky.rule(
            "K2", rkind, res, {"request.oldObject.spec.replicas": old, **ops}, user
        )
        == "deny"
    ):
        found.add("K2")
    k3 = "K3-scale" if kind == "scale" else "K3-evict"
    if (
        ky.rule(
            k3, rkind, res, {"level": level, "incidents": incidents or [], **ops}, user
        )
        == "deny"
    ):
        found.add("K3")
    if ky.rule("K4", rkind, res, {"killswitchState": ks, **ops}, user) == "deny":
        found.add("K4")
    return frozenset(found)


def kyverno_layers(
    ky: Kyverno, identity: str, request: str, cell_id: str, ks: str
) -> frozenset[str]:
    """The K1-K4 policies that refuse this matrix cell (a responder's scale or eviction), by the CLI."""
    ident, cell, req = (
        mx.DATA["identities"][identity],
        mx.DATA["cells"][cell_id],
        mx.request_def(request),
    )
    user = {"username": ident["username"], "groups": ident["groups"]}
    state = "missing" if ks == "absent" else ks
    per_variant = []
    for v in req.get("variants") or [{}]:
        per_variant.append(
            request_layers(
                ky,
                user,
                req["kind"],
                cell["namespace"],
                cell["level"] or "none",
                state,
                v.get("replicas"),
                v.get("from"),
                _incidents(cell),
            )
        )
    if len(set(per_variant)) != 1:
        raise ReplayError(f"variants of {request} disagree: {per_variant}")
    return per_variant[0]


def replay_matrix(ky: Kyverno) -> list[str]:
    """Replay every cell the Kyverno CLI can decide; return the disagreements with the declared matrix.

    A cell is Kyverno's when RBAC admits the request (a responder's scale or eviction in
    nexus-dev or nexus-prod). Every other cell is RBAC's (modelled by matrix.rbac_allows, and checked
    against the repository manifests by matrix.repo_overgrants) or the CRD's (C2, C3: envtest, see
    scripts/tests/incident-crd.sh). Those are counted, not skipped silently."""
    ky.calibrate()
    problems: list[str] = []
    seen = {"kyverno": 0, "rbac": 0, "crd": 0, "approval-path": 0}
    for cell, want in mx.expand():
        req = mx.request_def(cell.request)
        c = mx.DATA["cells"][cell.cell]
        if not mx.rbac_allows(cell.identity, req["kind"], c["namespace"]):
            seen["rbac"] += 1
            if want.layers != {"RBAC"}:
                problems.append(f"{cell.key}: RBAC refuses but the matrix says {want}")
            continue
        if req["kind"] in ("scale", "evict"):
            seen["kyverno"] += 1
            got = kyverno_layers(
                ky, cell.identity, cell.request, cell.cell, cell.killswitch
            )
            exp = want.layers if want.verdict == "DENY" else frozenset()
            if got != exp:
                problems.append(
                    f"{cell.key}: Kyverno CLI refuses by {sorted(got)}, matrix says {want}"
                )
        else:
            seen["crd" if want.layers & {"C2", "C3"} else "approval-path"] += 1
    ky.seen = seen  # type: ignore[attr-defined]
    return problems


def approval_incident(
    replicas: int = 3,
    expires: str = "2000-01-01T00:00:00Z",
    proposal_hash: str = "sha256:ab12",
) -> dict:
    """An Incident carrying a pinned approval as nexusctl writes it (a past expiry is within K5's bound)."""
    return {
        "apiVersion": "nexus.io/v1alpha1",
        "kind": "Incident",
        "metadata": {"name": "inc-probe", "namespace": "nexus-system"},
        "spec": {
            "source": {"type": "detector"},
            "target": {"namespace": "nexus-dev", "kind": "Deployment", "name": NAME},
            "detectedAt": "2026-10-14T09:12:03Z",
            "approval": {
                "decision": "approved",
                "actionId": "scale_deployment",
                "parameters": {"replicas": replicas},
                "proposalHash": proposal_hash,
                "expiresAt": expires,
            },
        },
        "status": {
            "proposal": {
                "hash": "sha256:ab12",
                "actionId": "scale_deployment",
                "parameters": {"replicas": 3},
            }
        },
    }


K5_RULES = ("K5-approvers-only", "K5-expiry-max", "K5-hash", "K5-action", "K5-replicas")


def replay_approval_path(ky: Kyverno) -> list[str]:
    """K5 for the matrix cells the Kyverno CLI can decide: the approver's pinned approval is admitted
    by every K5 rule, and the same approval from the operator or the baseline is refused by
    approvers-only (RBAC refuses it first in the cluster; this proves the policy would too)."""
    problems: list[str] = []
    ops = {"request.operation": "CREATE"}
    for identity in mx.IDENTITIES:
        ident = mx.DATA["identities"][identity]
        user = {"username": ident["username"], "groups": ident["groups"]}
        for rule in K5_RULES:
            got = ky.rule(rule, "Incident", approval_incident(), ops, user)
            want = (
                "deny"
                if (rule == "K5-approvers-only" and ident["role"] != "approver")
                else "allow"
            )
            if got != want:
                problems.append(f"{identity}/{rule}: K5 says {got}, expected {want}")
    approver = {"username": "approver-1", "groups": ["nexus-approvers"]}
    for label, inc, rule in (
        (
            "far expiry",
            approval_incident(expires="2099-01-01T00:00:00Z"),
            "K5-expiry-max",
        ),
        ("other hash", approval_incident(proposal_hash="sha256:ffff"), "K5-hash"),
        ("other replicas", approval_incident(replicas=5), "K5-replicas"),
    ):
        if ky.rule(rule, "Incident", inc, ops, approver) != "deny":
            problems.append(f"approver with {label}: {rule} did not refuse")
    return problems
