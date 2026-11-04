"""The containment coverage matrix (spec section 20): load, expand, decide, check.

Standard library plus PyYAML. `expand()` yields all 630 cells with their declared verdict;
`verdict()` derives one from the section 11 tables in matrix.yaml and raises when the tables do not
decide a cell. Nothing here reads platform/ except repo_overgrants(), the RBAC subset check.
"""

from __future__ import annotations

import itertools
import pathlib
from dataclasses import dataclass

import yaml

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = yaml.safe_load((HERE / "matrix.yaml").read_text())

IDENTITIES = list(DATA["identities"])
REQUESTS = list(DATA["requests"])
CELLS = list(DATA["cells"])
KILLSWITCH = list(DATA["killswitch"])
EXPECTED_CELLS = 630


@dataclass(frozen=True)
class Verdict:
    verdict: str  # ADMIT | DENY
    layers: frozenset[str] = frozenset()  # every layer that refuses; empty for ADMIT

    def __str__(self) -> str:
        return self.verdict + (f" {sorted(self.layers)}" if self.layers else "")


@dataclass(frozen=True)
class Cell:
    identity: str
    request: str
    cell: str
    killswitch: str

    @property
    def key(self) -> str:
        return f"{self.identity}/{self.request}/{self.cell}/{self.killswitch}"


# The compensation request is outside the 630 (section 20 names it separately).
SPECIAL_REQUESTS = {
    "compensation-scale-back": {
        "kind": "scale",
        "variants": [{"replicas": 2, "from": 3}],
    },
}


def request_def(name: str) -> dict:
    return DATA["requests"].get(name) or SPECIAL_REQUESTS[name]


def rbac_allows(identity: str, kind: str, namespace: str) -> bool:
    role = DATA["identities"][identity]["role"]
    for row in DATA["rbac"][role]:
        if kind in row["kinds"] and (
            "*" in row["namespaces"] or namespace in row["namespaces"]
        ):
            return True
    return False


def _responder_layers(kind: str, variant: dict, cell: dict, ks: str) -> set[str]:
    pol, layers = DATA["policies"], set()
    level = cell["level"]
    if kind in pol["K1"]["applies_to"] and level not in pol["K1"]["allow_levels"]:
        layers.add("K1")
    if kind in pol["K2"]["applies_to"]:
        k2, n, old = pol["K2"], variant["replicas"], variant["from"]
        if n < k2["min"] or n > k2["max"] or abs(n - old) > k2["max_step"]:
            layers.add("K2")
    if kind in pol["K3"]["applies_to"] and level == pol["K3"]["at_level"]:
        approved = cell["approval"] == "approved"
        pinned = approved and (
            kind == "evict"
            or variant["replicas"] == DATA["approved_fixtures"]["scale"]["replicas"]
        )
        if not pinned:
            layers.add("K3")
    if kind in pol["K4"]["applies_to"] and ks not in pol["K4"]["allow_states"]:
        layers.add("K4")
    return layers


def verdict(identity: str, request: str, cell: str, ks: str) -> Verdict:
    req, c = request_def(request), DATA["cells"][cell]
    kind = req["kind"]
    if not rbac_allows(identity, kind, c["namespace"]):
        return Verdict("DENY", frozenset({"RBAC"}))
    if kind == "set_approval":
        a = DATA["approval"]
        if c["namespace"] not in a["c3_namespaces"]:
            return Verdict("DENY", frozenset({"C3"}))
        if c["approval"] == "approved":
            return Verdict("DENY", frozenset({a["write_once"]}))
        return Verdict("ADMIT")
    if kind in ("scale", "evict"):
        variants = req.get("variants") or [{}]
        out = {frozenset(_responder_layers(kind, v, c, ks)) for v in variants}
        if len(out) != 1:
            raise ValueError(f"variants of {request} disagree in {cell}/{ks}: {out}")
        layers = out.pop()
        return Verdict("DENY", layers) if layers else Verdict("ADMIT")
    raise ValueError(f"the tables do not decide {identity}/{request}/{cell}/{ks}")


def expand() -> list[tuple[Cell, Verdict]]:
    cells = [
        Cell(i, r, c, k)
        for i, r, c, k in itertools.product(IDENTITIES, REQUESTS, CELLS, KILLSWITCH)
    ]
    if len(cells) != EXPECTED_CELLS:
        raise ValueError(
            f"the matrix has {len(cells)} cells, expected {EXPECTED_CELLS}"
        )
    return [(c, verdict(c.identity, c.request, c.cell, c.killswitch)) for c in cells]


def expected_headlines() -> list[tuple[Cell, Verdict]]:
    out = []
    for e in DATA["expected"]:
        v = Verdict(e["verdict"], frozenset(e.get("layers", [])))
        out.append((Cell(e["identity"], e["request"], e["cell"], e["killswitch"]), v))
    return out


def specials() -> list[tuple[str, Cell, Verdict]]:
    return [
        (
            s["id"],
            Cell(s["identity"], s["request"], s["cell"], s["killswitch"]),
            Verdict(s["verdict"], frozenset(s.get("layers", []))),
        )
        for s in DATA["special"]
    ]


def special_verdict(cell: Cell) -> Verdict:
    """Derived verdict of a special cell; fail-closed is decided by the webhook, not by a table."""
    if (
        cell.request == "scale-in-bounds"
        and cell.cell == "dev-l3"
        and cell.killswitch == "active"
    ):
        return Verdict("DENY", frozenset({"webhook-unavailable"}))
    return verdict(cell.identity, cell.request, cell.cell, cell.killswitch)


# ---------------------------------------------------------------- repo RBAC subset check
MUTATING = {
    "create",
    "update",
    "patch",
    "delete",
    "deletecollection",
    "escalate",
    "bind",
    "impersonate",
    "approve",
    "*",
}
SUBJECTS = {
    ("ServiceAccount", "nexus-operator"): "operator",
    ("ServiceAccount", "nexus-baseline"): "baseline",
    ("Group", "nexus-approvers"): "approver",
}
RBAC_FILES = ["platform/rbac", "platform/policies/approvers-rbac.yaml"]


def _docs(paths: list[pathlib.Path]) -> list[dict]:
    files: list[pathlib.Path] = []
    for p in paths:
        files += sorted(p.glob("*.yaml")) if p.is_dir() else [p]
    docs = []
    for f in files:
        if f.name == "kustomization.yaml":
            continue
        docs += [d for d in yaml.safe_load_all(f.read_text()) if d]
    return docs


def repo_overgrants(root: pathlib.Path = REPO) -> list[str]:
    """Mutating grants in the repository's RBAC manifests that section 11 does not allow."""
    docs = _docs([root / p for p in RBAC_FILES])
    roles = {
        (d["kind"], d["metadata"].get("namespace"), d["metadata"]["name"]): d["rules"]
        for d in docs
        if d.get("kind") in ("Role", "ClusterRole")
    }
    problems: list[str] = []
    for b in (
        d for d in docs if d.get("kind") in ("RoleBinding", "ClusterRoleBinding")
    ):
        ns = b["metadata"].get(
            "namespace"
        )  # None for a ClusterRoleBinding: grants everywhere
        ref = b["roleRef"]
        rules = roles.get(
            (ref["kind"], ns if ref["kind"] == "Role" else None, ref["name"])
        )
        if rules is None:
            continue  # a role not in these files (for example Kyverno's own)
        for s in b.get("subjects", []):
            ident = SUBJECTS.get((s["kind"], s["name"]))
            if ident is None:
                continue
            allowed = DATA["rbac_mutations"][ident]
            for rule in rules:
                for grp, res, verb in itertools.product(
                    rule.get("apiGroups", [""]), rule["resources"], rule["verbs"]
                ):
                    if verb not in MUTATING:
                        continue
                    ok = any(
                        a["group"] == grp
                        and a["resource"] == res
                        and verb in a["verbs"]
                        and ns in a["namespaces"]
                        for a in allowed
                    )
                    if not ok:
                        problems.append(
                            f"{ident}: {verb} {grp or 'core'}/{res} in {ns or 'cluster'} "
                            f"(via {b['metadata']['name']}) is not a section 11 grant"
                        )
    return problems
