"""The live driver, offline: the plan, the classifier and the judge. LIVE CHECK PENDING for the rest."""

import contextlib
import datetime
import io
import json
import unittest

from .. import live
from .. import matrix as mx

NOW = datetime.datetime(2026, 11, 3, 9, 0, 0, tzinfo=datetime.UTC)


class Plan(unittest.TestCase):
    def test_every_cell_has_a_command_and_the_extra_variant(self):
        steps = live.plan(NOW)
        self.assertEqual(len({s.cell.key for s in steps}), 630)
        self.assertEqual(
            len(steps), 630 + 63
        )  # scale-6-or-plus-3 sends two requests per cell

    def test_one_pass_is_a_third(self):
        for ks in mx.KILLSWITCH:
            self.assertEqual(len({s.cell.key for s in live.plan(NOW, ks)}), 210)

    def test_every_request_is_a_server_side_dry_run_by_impersonation(self):
        for s in live.plan(NOW):
            joined = " ".join(s.argv)
            self.assertTrue(any(a.startswith("--as=") for a in s.argv), joined)
            if s.argv[:3] == ["kubectl", "create", "--raw"]:
                self.assertEqual(
                    json.loads(s.stdin)["deleteOptions"]["dryRun"], ["All"]
                )
            else:
                self.assertIn("--dry-run=server", s.argv, joined)

    def test_the_approver_is_impersonated_with_its_group_and_the_operator_without(self):
        by = {s.cell.identity: s for s in live.plan(NOW, "active")}
        self.assertIn("--as-group=nexus-approvers", by["approver"].argv)
        self.assertNotIn("--as-group=nexus-approvers", by["operator"].argv)

    def test_approval_requests_use_a_future_expiry_inside_k5s_bound(self):
        for s in live.plan(NOW, "active"):
            if s.cell.request == "set-approval":
                body = (
                    json.loads(s.stdin)
                    if s.stdin
                    else json.loads(s.argv[s.argv.index("-p") + 1])
                )
                exp = (
                    (body.get("spec") or body)["approval"]["expiresAt"]
                    if "spec" in body
                    else None
                )
                exp = datetime.datetime.strptime(exp, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.UTC
                )
                self.assertTrue(NOW < exp <= NOW + datetime.timedelta(minutes=15))

    def test_the_specials_have_commands(self):
        for sid in (
            "fail-closed",
            "killswitch-halted-compensation",
            "killswitch-absent-compensation",
        ):
            steps = live.special_steps(sid, NOW)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0].expected.verdict, "DENY")

    def test_plan_prints_without_touching_a_cluster(self):
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(live.main(["--plan", "--ks-state", "active"]), 0)


class Classify(unittest.TestCase):
    def test_answers(self):
        c = live.classify
        self.assertEqual(c(0, "scaled (server dry run)", "").verdict, "ADMIT")
        self.assertEqual(
            c(
                1,
                "",
                'Error from server (Forbidden): deployments.apps "x" is forbidden: User "u" cannot patch resource "deployments/scale"',
            ).layers,
            {"RBAC"},
        )
        k = c(
            1,
            "",
            'Error from server: admission webhook "validate.kyverno.svc-fail" denied the request: resource Scale/nexus-dev/x was blocked due to the following policies\n\nnexus-killswitch:\n  killswitch-active: "K4 nexus-killswitch: the Kill Switch state is halted"\nnexus-scale-bounds:\n  scale-bounds: "K2 nexus-scale-bounds: replicas"',
        )
        self.assertEqual((k.verdict, k.layers), ("DENY", {"K2", "K4"}))
        self.assertEqual(
            c(
                1,
                "",
                'The Incident "x" is invalid: spec.approval: Invalid value: "object": C2: spec.approval is write-once',
            ).layers,
            {"C2"},
        )
        self.assertEqual(
            c(
                1,
                "",
                "target must be a Deployment: C3: target must be a Deployment in nexus-dev",
            ).layers,
            {"C3"},
        )
        self.assertEqual(
            c(
                1,
                "",
                'Internal error occurred: failed calling webhook "validate.kyverno.svc-fail": failed to call webhook: no endpoints available',
            ).layers,
            {"webhook-unavailable"},
        )
        self.assertEqual(
            c(
                1,
                "",
                "Cannot evict pod as it would violate the pod's disruption budget.",
            ).verdict,
            "ADMIT",
        )

    def test_an_unplaceable_answer_is_never_a_pass(self):
        got = live.classify(1, "", "the connection to the server was refused")
        self.assertEqual(got.verdict, "UNKNOWN")
        ok, _ = live.judge(mx.Verdict("DENY", frozenset({"RBAC"})), got)
        self.assertFalse(ok)

    def test_judge(self):
        deny = mx.Verdict("DENY", frozenset({"K2", "K3"}))
        self.assertTrue(live.judge(deny, live.Observed("DENY", {"K3"}))[0])
        self.assertFalse(live.judge(deny, live.Observed("DENY", {"K1"}))[0])
        self.assertFalse(live.judge(deny, live.Observed("ADMIT"))[0])
        self.assertFalse(
            live.judge(mx.Verdict("ADMIT"), live.Observed("DENY", {"RBAC"}))[0]
        )
        self.assertTrue(live.judge(mx.Verdict("ADMIT"), live.Observed("ADMIT"))[0])


if __name__ == "__main__":
    unittest.main()
