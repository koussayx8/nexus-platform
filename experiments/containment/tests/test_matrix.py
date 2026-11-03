"""The declared matrix: 630 cells, every one decided, the headline cells as written by hand."""

import pathlib
import shutil
import tempfile
import unittest
from collections import Counter

from .. import matrix as mx


class Matrix(unittest.TestCase):
    def test_630_cells_each_decided(self):
        table = mx.expand()
        self.assertEqual(len(table), 630)
        self.assertEqual(len({c.key for c, _ in table}), 630)
        self.assertEqual(
            (len(mx.IDENTITIES), len(mx.REQUESTS), len(mx.CELLS), len(mx.KILLSWITCH)),
            (3, 10, 7, 3),
        )

    def test_headline_cells_agree_with_the_derived_table(self):
        for cell, want in mx.expected_headlines():
            self.assertEqual(
                mx.verdict(cell.identity, cell.request, cell.cell, cell.killswitch),
                want,
                cell.key,
            )

    def test_exactly_the_admitted_cells(self):
        admitted = sorted(c.key for c, v in mx.expand() if v.verdict == "ADMIT")
        want = []
        for ident in ("operator", "baseline"):
            for req in ("scale-in-bounds", "evict"):
                for cell in ("dev-l3", "dev-l2-approved"):
                    want.append(f"{ident}/{req}/{cell}/active")
        for cell in ("dev-l3", "dev-l2-none", "prod-l1", "data-l0"):
            for ks in mx.KILLSWITCH:
                want.append(f"approver/set-approval/{cell}/{ks}")
        self.assertEqual(admitted, sorted(want))
        self.assertEqual(
            Counter(v.verdict for _, v in mx.expand()), {"DENY": 610, "ADMIT": 20}
        )

    def test_a_halted_or_absent_kill_switch_refuses_every_responder_mutation(self):
        for cell, v in mx.expand():
            req = mx.request_def(cell.request)
            if (
                mx.DATA["identities"][cell.identity]["role"] == "responder"
                and cell.killswitch != "active"
            ):
                self.assertEqual(v.verdict, "DENY", cell.key)
                if req["kind"] in ("scale", "evict") and mx.DATA["cells"][cell.cell][
                    "namespace"
                ] in ("nexus-dev", "nexus-prod"):
                    self.assertIn("K4", v.layers, cell.key)

    def test_the_approver_can_mutate_nothing_but_an_approval(self):
        for cell, v in mx.expand():
            if cell.identity == "approver" and cell.request != "set-approval":
                self.assertEqual(v, mx.Verdict("DENY", frozenset({"RBAC"})), cell.key)

    def test_nobody_may_delete_patch_relabel_halt_or_bind(self):
        for cell, v in mx.expand():
            if cell.request in (
                "delete-deployment",
                "patch-deployment",
                "change-ns-label",
                "change-killswitch",
                "create-rolebinding",
            ):
                self.assertEqual(v, mx.Verdict("DENY", frozenset({"RBAC"})), cell.key)

    def test_the_responders_are_treated_alike(self):
        for cell, v in mx.expand():
            if cell.identity == "operator":
                twin = mx.verdict("baseline", cell.request, cell.cell, cell.killswitch)
                self.assertEqual(v, twin, cell.key)

    def test_no_responder_mutation_outside_dev_and_prod_reaches_admission(self):
        for cell, v in mx.expand():
            if mx.DATA["identities"][cell.identity]["role"] == "responder" and mx.DATA[
                "cells"
            ][cell.cell]["namespace"] not in ("nexus-dev", "nexus-prod"):
                self.assertEqual(v.layers, {"RBAC"}, cell.key)

    def test_the_two_variants_of_the_beyond_bounds_request_agree(self):
        for cell in mx.CELLS:
            mx.verdict(
                "operator", "scale-6-or-plus-3", cell, "active"
            )  # raises when they disagree

    def test_specials(self):
        ids = {sid for sid, _, _ in mx.specials()}
        self.assertEqual(
            ids,
            {
                "fail-closed",
                "killswitch-halted-compensation",
                "killswitch-absent-compensation",
            },
        )
        for sid, cell, want in mx.specials():
            self.assertEqual(mx.special_verdict(cell), want, sid)


class RepoRbac(unittest.TestCase):
    def test_the_repository_grants_no_more_than_section_11(self):
        self.assertEqual(mx.repo_overgrants(), [])

    def test_an_extra_grant_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            shutil.copytree(mx.REPO / "platform" / "rbac", root / "platform" / "rbac")
            (root / "platform" / "policies").mkdir(parents=True)
            shutil.copy(
                mx.REPO / "platform" / "policies" / "approvers-rbac.yaml",
                root / "platform" / "policies",
            )
            bad = root / "platform" / "rbac" / "extra.yaml"
            bad.write_text(
                "apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata: {name: x, namespace: nexus-dev}\n"
                "rules:\n- {apiGroups: [apps], resources: [deployments], verbs: [delete]}\n---\n"
                "apiVersion: rbac.authorization.k8s.io/v1\nkind: RoleBinding\nmetadata: {name: x, namespace: nexus-dev}\n"
                "roleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: x}\n"
                "subjects: [{kind: ServiceAccount, name: nexus-operator, namespace: nexus-system}]\n"
            )
            found = mx.repo_overgrants(root)
            self.assertEqual(len(found), 1)
            self.assertIn("delete apps/deployments", found[0])

    def test_scale_in_prod_is_a_declared_grant_the_repo_may_add_later(self):
        a = next(
            g
            for g in mx.DATA["rbac_mutations"]["operator"]
            if g["resource"] == "deployments/scale"
        )
        self.assertEqual(sorted(a["namespaces"]), ["nexus-dev", "nexus-prod"])


if __name__ == "__main__":
    unittest.main()
