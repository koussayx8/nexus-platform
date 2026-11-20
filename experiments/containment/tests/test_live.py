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
        # the baseline's 210 cells are offline only (review F14): 420 are sent
        steps = live.plan(NOW)
        self.assertEqual(len({s.cell.key for s in steps}), 420)
        self.assertEqual(
            len(steps), 420 + 42
        )  # scale-6-or-plus-3 sends two requests per cell

    def test_one_pass_is_a_third(self):
        for ks in mx.KILLSWITCH:
            self.assertEqual(len({s.cell.key for s in live.plan(NOW, ks)}), 140)

    def test_the_baseline_is_declared_and_never_sent(self):
        self.assertEqual(mx.live_identities(), ["operator", "approver"])
        self.assertEqual(sum(c.identity == "baseline" for c, _ in mx.expand()), 210)
        self.assertEqual(live.offline_only(), 210)
        self.assertEqual(live.offline_only("active", {"dev-l3"}), 10)
        for s in live.plan(NOW):
            self.assertNotIn("nexus-baseline", " ".join(s.argv))

    def test_a_k3_only_case_is_one_exact_dry_run_scale_at_level_2(self):
        live_ids = [c["id"] for c in mx.k3_isolation() if c.get("live")]
        self.assertEqual(live_ids, ["k3-control-approved", "k3-unapproved-replicas"])
        (s,) = live.special_steps("k3-unapproved-replicas", NOW)
        self.assertEqual(
            s.argv[:6],
            [
                "kubectl",
                "scale",
                "deployment/sample-api",
                "-n",
                "nexus-dev",
                "--replicas=4",
            ],
        )
        self.assertIn("--dry-run=server", s.argv)
        self.assertTrue(s.exact)
        self.assertEqual(s.expected, mx.Verdict("DENY", frozenset({"K3"})))
        with self.assertRaises(KeyError):
            live.special_steps("k3-expired", NOW)  # declared, offline only

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
        self.assertEqual(
            live.SPECIAL_IDS[3:], ["k3-control-approved", "k3-unapproved-replicas"]
        )

    def test_plan_prints_without_touching_a_cluster(self):
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(live.main(["--plan", "--ks-state", "active"]), 0)


class ExactAndScaleToZero(unittest.TestCase):
    K3 = 'admission webhook "validate.kyverno.svc-fail" denied the request: nexus-l2-approval: K3 nexus-l2-approval: no approved'
    K2_BOUNDS = 'admission webhook "validate.kyverno.svc-fail" denied the request: nexus-scale-bounds: K2 nexus-scale-bounds: replicas 2 -> 0 is outside [1, 5] or changes'
    K2_ERROR = 'admission webhook "validate.kyverno.svc-fail" denied the request: policy nexus-scale-bounds: failed to check deny conditions: failed to substitute variables in condition key: failed to resolve request.object.spec.replicas at path : JMESPath query failed: Unknown key "replicas" in path'

    def test_an_exact_case_fails_on_any_other_layer(self):
        want = mx.Verdict("DENY", frozenset({"K3"}))
        only = live.classify(1, "", self.K3)
        both = live.classify(1, "", self.K3 + " nexus-scale-bounds")
        self.assertEqual(live.judge(want, only, exact=True), (True, ""))
        self.assertTrue(
            live.judge(want, both)[0]
        )  # a matrix cell: an extra layer is a note
        ok, note = live.judge(want, both, exact=True)
        self.assertFalse(ok)
        self.assertIn("also ['K2']", note)

    def step(self, request="scale-to-0"):
        cell = mx.Cell("operator", request, "dev-l3", "active")
        return live.Step(cell, 0, [], None, mx.Verdict("DENY", frozenset({"K2"})))

    def test_the_scale_to_0_message_is_placed(self):
        for text, reason in ((self.K2_BOUNDS, "bounds"), (self.K2_ERROR, "rule_error")):
            got = live.classify(1, "", text)
            self.assertEqual((got.layers, got.k2_reason), ({"K2"}, reason))
        got = live.classify(1, "", self.K2_BOUNDS)
        self.assertEqual(
            live.judge_scale_to_0(self.step(), got), (True, "K2 by bounds")
        )

    def test_a_rule_error_on_scale_to_0_fails(self):
        """K2 defaults a missing `replicas` to 0: a rule error means an older K2 is deployed."""
        got = live.classify(1, "", self.K2_ERROR)
        ok, note = live.judge_scale_to_0(self.step(), got)
        self.assertFalse(ok)
        self.assertIn("rule_error", note)

    def test_a_k2_refusal_with_another_message_fails_scale_to_0_only(self):
        got = live.classify(
            1,
            "",
            'admission webhook "x" denied the request: nexus-scale-bounds: something else',
        )
        self.assertEqual(got.k2_reason, "other")
        self.assertFalse(live.judge_scale_to_0(self.step(), got)[0])
        self.assertEqual(
            live.judge_scale_to_0(self.step("scale-6-or-plus-3"), got), (True, "")
        )


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
