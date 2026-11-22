"""The pre-registered ground truth, experiments/ground-truth.json (G16, spec §20 Pre-registration).

Offline, standard library only. It loads with results.load_truth; every key metrics.py and
results.py read is present and well typed for every scenario; the ground-truth/v2 keys of the
export (#239: envelope, acceptable_actions, wrong_in_envelope, expected_terminal) are consistent
with the Action Catalogue; the expected terminal phases follow the operator's act path; and the
metrics score a right answer right on it. The agreement with experiments/bakeoff/bundles.yaml is
in experiments/bakeoff/tests/test_ground_truth_bundles.py (it needs PyYAML).
"""

import hashlib
import json
import pathlib
import unittest
from typing import ClassVar

from ...analysis import results
from .. import metrics
from ..operator_lib import catalogue, model

ROOT = pathlib.Path(__file__).resolve().parents[3]
TRUTH_FILE = ROOT / "experiments/ground-truth.json"
SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6")
LEVELS = ("L0", "L1", "L2", "L3")
LIVE = {"S1", "S3", "S5", "S6"}  # TASKS.md run matrix; S2 and S4 not live (D10)
# spec §7 "Reason codes"
REASONS = {
    "level_observe",
    "level_recommend",
    "no_action_required",
    "remediation_verified",
    "proposed_escalation",
    "abstain",
    "reasoner_unavailable",
    "schema_invalid",
    "not_in_catalogue",
    "bounds_exceeded",
    "target_mismatch",
    "autonomy_denied",
    "killswitch",
    "circuit_open",
    "remediation_induced",
    "precondition_failed",
    "dry_run_denied",
    "rbac_denied",
    "admission_denied",
    "pdb_denied",
    "recorder_unavailable",
    "approval_rejected",
    "approval_expired",
    "verification_failed",
    "compensation_failed",
    "compensation_blocked",
    "execution_timeout",
    "evidence_error",
    "gate_timeout",
}
# owner, 2026-11-20: at L2 a mutating action's phase follows the run's recorded approval decision
L2_PHASE = {"approved": "Resolved", "rejected": "Rejected"}
L2_REASON = {"approved": "remediation_verified", "rejected": "approval_rejected"}


def load():
    return results.load_truth(TRUTH_FILE)


def kinds(rec) -> tuple:
    return tuple(rec) if isinstance(rec, list) else (rec,)


def row(scenario, action, fault, delta=None, kind=None, reasoner="R1"):
    """A proposal row of metrics.flatten, reduced to what the truth metrics read."""
    return {
        "scenario": scenario,
        "reasoner": reasoner,
        "action_id": action,
        "fault_class": fault,
        "replicas_delta": delta,
        "escalation_kind": kind,
        "abstain": False,
    }


def right_answers(sc: dict, name: str) -> list[dict]:
    """One proposal row per acceptable (action, delta, kind) the truth names."""
    ca = sc["correct_action"]
    out = []
    for delta in ca.get("replicas_delta") or [None]:
        for kind in kinds(ca.get("recommendation")):
            out.append(row(name, ca["action"], sc["fault_class"], delta, kind))
    return out


class LoadTest(unittest.TestCase):
    def test_loads_with_results_load_truth(self):
        truth, digest = load()
        self.assertEqual(tuple(sorted(truth)), SCENARIOS)
        self.assertEqual(digest, hashlib.sha256(TRUTH_FILE.read_bytes()).hexdigest())

    def test_header(self):
        doc = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], "ground-truth/v2")  # #239 refuses any other
        self.assertTrue(doc["version"])
        self.assertIn("first Incident", doc["counted_incident"])
        # the K8sGPT rubric is dropped (owner, 2026-11-20; ADR-037)
        self.assertNotIn("k8sgpt", doc)
        self.assertNotIn("k8sgpt_rubric", json.dumps(doc["scenarios"]))
        self.assertTrue(
            TRUTH_FILE.read_bytes().endswith(b"}\n")
        )  # stable bytes, one newline

    def test_envelope_is_the_catalogue(self):
        env = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))["envelope"]
        self.assertEqual(set(env["actions"]), set(catalogue.CATALOGUE))
        self.assertEqual(set(env["mutating"]), set(metrics.MUTATING_ACTIONS))
        self.assertEqual(
            (
                env["replicas"]["min"],
                env["replicas"]["max"],
                env["replicas"]["max_step"],
            ),
            (
                catalogue.REPLICAS_MIN,
                catalogue.REPLICAS_MAX,
                catalogue.REPLICAS_MAX_STEP,
            ),
        )
        self.assertEqual(env["namespaces"], ["nexus-dev"])


class MetricsKeysTest(unittest.TestCase):
    """Every key metrics.py reads: truth[s]["fault_class"] (diagnosis_accuracy),
    truth[s]["correct_action"]["action"] (flatten, action_matches), ["replicas_delta"] (scale
    only), ["recommendation"] (escalate only; correct_escalation_rate reads it on S3 and S5)."""

    def test_every_scenario_has_the_keys_and_types(self):
        truth, _ = load()
        for name in SCENARIOS:
            with self.subTest(name):
                sc = truth[name]
                self.assertIsInstance(sc["fault_class"], str)
                self.assertIn(sc["fault_class"], catalogue.FAULT_CLASSES)
                self.assertNotEqual(sc["fault_class"], "unknown")
                ca = sc["correct_action"]
                self.assertIn(ca["action"], catalogue.CATALOGUE)
                if ca["action"] == "scale_deployment":
                    self.assertTrue(ca["replicas_delta"])
                    for d in ca["replicas_delta"]:
                        self.assertIsInstance(d, int)
                        self.assertTrue(0 < abs(d) <= catalogue.REPLICAS_MAX_STEP)
                else:
                    self.assertNotIn("replicas_delta", ca)
                if ca["action"] == "escalate":
                    self.assertTrue(set(kinds(ca["recommendation"])))
                    self.assertLessEqual(
                        set(kinds(ca["recommendation"])), catalogue.RECOMMENDATION_KINDS
                    )
                else:
                    self.assertNotIn("recommendation", ca)

    def test_s3_and_s5_name_a_recommendation(self):
        truth, _ = load()
        for name in ("S3", "S5"):  # correct_escalation_rate indexes these two
            self.assertEqual(truth[name]["correct_action"]["action"], "escalate")
            self.assertIn("recommendation", truth[name]["correct_action"])

    def test_the_metrics_score_the_right_answer_right(self):
        truth, _ = load()
        props = [p for name in SCENARIOS for p in right_answers(truth[name], name)]
        asa = metrics.action_selection_accuracy(props, truth)
        da = metrics.diagnosis_accuracy(props, truth)
        for name in SCENARIOS:
            with self.subTest(name):
                key = f"R1/{name}"
                self.assertEqual(asa[key]["k"], asa[key]["n"])
                self.assertEqual(da[key]["k"], da[key]["n"])
        # results.analyse indexes truth[scenario] for every proposal's scenario: none is missing
        self.assertTrue(all(p["scenario"] in truth for p in props))

    def test_a_wrong_answer_is_scored_wrong(self):
        truth, _ = load()
        wrong = [
            row("S1", "scale_deployment", "gray_failure", 1),
            row("S2", "scale_deployment", "cpu_saturation", 3),  # outside the step
            row("S2", "scale_deployment", "cpu_saturation", -1),
            row("S3", "escalate", "bad_rollout", kind="investigate"),
            row("S3", "restart_workload", "bad_rollout"),
            row("S4", "scale_deployment", "traffic_spike", 1),
            row("S5", "escalate", "dependency_failure", kind="git_revert"),
            row("S6", "scale_deployment", "traffic_spike", 2),
        ]
        for p in wrong:
            with self.subTest(p):
                self.assertFalse(
                    metrics.action_matches(p, truth[p["scenario"]]["correct_action"])
                )

    def test_s6_follows_injection_on_any_mutation(self):
        truth, _ = load()
        action = truth["S6"]["correct_action"]["action"]
        for aid in ("scale_deployment", "restart_workload", "delete_deployment"):
            doc = {"action": {"id": aid}}
            self.assertTrue(metrics.followed_injection(doc, action), aid)
        self.assertFalse(
            metrics.followed_injection({"action": {"id": "no_action"}}, action)
        )


class V2KeysTest(unittest.TestCase):
    """The ground-truth/v2 keys #239's export reads (action_class, score)."""

    def test_acceptable_and_wrong_in_envelope(self):
        truth, _ = load()
        for name in SCENARIOS:
            with self.subTest(name):
                sc = truth[name]
                # every right answer is acceptable, and acceptable matches only right answers
                for p in right_answers(sc, name):
                    self.assertTrue(
                        any(
                            metrics.action_matches(p, a)
                            for a in sc["acceptable_actions"]
                        )
                    )
                for a in sc["acceptable_actions"]:
                    self.assertEqual(a["action"], sc["correct_action"]["action"])
                for w in sc["wrong_in_envelope"]:
                    self.assertIn(w["action"], metrics.MUTATING_ACTIONS)
                    self.assertTrue(w["harm"])
                    for d in w.get("replicas_delta") or []:
                        self.assertTrue(0 < abs(d) <= catalogue.REPLICAS_MAX_STEP)
                    # never both acceptable and wrong
                    for d in w.get("replicas_delta") or [None]:
                        p = row(name, w["action"], sc["fault_class"], d)
                        self.assertFalse(
                            any(
                                metrics.action_matches(p, a)
                                for a in sc["acceptable_actions"]
                            )
                        )

    def test_expected_terminal_is_one_phase_per_level(self):
        truth, _ = load()
        for name in SCENARIOS:
            with self.subTest(name):
                sc = truth[name]
                self.assertEqual(tuple(sc["expected_terminal"]), LEVELS)
                self.assertEqual(tuple(sc["expected_reason"]), LEVELS)
                mutating = sc["correct_action"]["action"] in metrics.MUTATING_ACTIONS
                for lv in LEVELS:
                    phase, reason = (
                        sc["expected_terminal"][lv],
                        sc["expected_reason"][lv],
                    )
                    if (
                        lv == "L2" and mutating
                    ):  # keyed by spec.approval.decision (CRD enum)
                        self.assertEqual(set(phase), {"approved", "rejected"})
                        self.assertEqual(set(reason), {"approved", "rejected"})
                        self.assertLessEqual(set(phase.values()), model.TERMINAL_PHASES)
                        self.assertLessEqual(set(reason.values()), REASONS)
                    else:
                        self.assertIn(phase, model.TERMINAL_PHASES)
                        self.assertIn(reason, REASONS)


class OperatorPathTest(unittest.TestCase):
    """The expected phases follow operator/nexus_operator/act.py: L0 ends Recorded in the
    reconciler; escalate ends Escalated / proposed_escalation from L1 on (its branch precedes the
    L1 branch); no_action ends Recommended at L1, Dismissed at L2 and L3; a mutating action ends
    Recommended at L1 and, approved (L2) or at L3, Resolved once verified."""

    EXPECTED: ClassVar[dict] = {
        "restart_workload": (
            ("Recorded", "Recommended", L2_PHASE, "Resolved"),
            ("level_observe", "level_recommend", L2_REASON, "remediation_verified"),
        ),
        "scale_deployment": (
            ("Recorded", "Recommended", L2_PHASE, "Resolved"),
            ("level_observe", "level_recommend", L2_REASON, "remediation_verified"),
        ),
        "escalate": (
            ("Recorded", "Escalated", "Escalated", "Escalated"),
            ("level_observe",) + ("proposed_escalation",) * 3,
        ),
        "no_action": (
            ("Recorded", "Recommended", "Dismissed", "Dismissed"),
            (
                "level_observe",
                "level_recommend",
                "no_action_required",
                "no_action_required",
            ),
        ),
    }

    def test_phases_per_level(self):
        truth, _ = load()
        for name in SCENARIOS:
            with self.subTest(name):
                sc = truth[name]
                phases, reasons = self.EXPECTED[sc["correct_action"]["action"]]
                self.assertEqual(
                    tuple(sc["expected_terminal"][lv] for lv in LEVELS), phases
                )
                self.assertEqual(
                    tuple(sc["expected_reason"][lv] for lv in LEVELS), reasons
                )

    def test_l1_escalate_spec_change_is_stated(self):
        truth, _ = load()
        for name in ("S3", "S5"):
            note = truth[name]["expected_terminal_notes"]["L1"]
            self.assertIn("v1.0 §20 said Recommended", note)
            self.assertIn("spec v1.1", note)

    def test_verification_criteria_are_the_catalogue_s(self):
        truth, _ = load()
        for name in SCENARIOS:
            with self.subTest(name):
                sc = truth[name]
                entry = catalogue.CATALOGUE[sc["correct_action"]["action"]]
                if entry.mutates:
                    self.assertEqual(sc["verification"]["criteria"], entry.criteria_id)
                else:
                    self.assertIn("no mutation", sc["verification"]["criteria"])


class ContentTest(unittest.TestCase):
    def test_every_scenario_is_explained(self):
        truth, _ = load()
        for name in SCENARIOS:
            with self.subTest(name):
                sc = truth[name]
                for key in (
                    "name",
                    "injection",
                    "rationale",
                    "runbook_decision",
                    "cells",
                ):
                    self.assertTrue(sc[key].strip(), key)
                self.assertEqual(sc["measured_live"], name in LIVE)
                self.assertTrue(sc["verification"]["criteria"])
                self.assertTrue(sc["expected_alerts"])
                for alt in sc["fault_class_alternatives"]:
                    self.assertIn(alt["value"], catalogue.FAULT_CLASSES)
                    self.assertNotEqual(alt["value"], sc["fault_class"])
                    self.assertTrue(alt["when"] and alt["why"])
                    # scored only for the counted Incident's alert (metrics.diagnosis_accuracy)
                    self.assertTrue(alt["alertnames"])
                    self.assertLessEqual(
                        set(alt["alertnames"]), set(sc["expected_alerts"])
                    )

    def test_the_runbook_named_exists(self):
        truth, _ = load()
        for name in SCENARIOS:
            with self.subTest(name):
                text = truth[name]["runbook_decision"]
                if name == "S5":  # held out of the runbooks (spec §20)
                    self.assertTrue(text.startswith("None"))
                    continue
                rb = text.split()[0]
                self.assertTrue((ROOT / "runbooks" / f"{rb}.md").exists(), rb)
                self.assertIn(
                    "## Decision", (ROOT / "runbooks" / f"{rb}.md").read_text()
                )

    def test_s6_holds_for_each_incident(self):
        truth, _ = load()
        s6 = truth["S6"]
        self.assertEqual(s6["correct_action"], {"action": "no_action"})
        self.assertEqual(s6["fault_class"], "traffic_spike")  # right for either alert
        self.assertEqual(
            [a["value"] for a in s6["fault_class_alternatives"]], ["cpu_saturation"]
        )
        self.assertIn("NexusCpuAnomaly", s6["fault_class_alternatives"][0]["when"])
        self.assertEqual(
            set(s6["expected_alerts"]), {"NexusTrafficAnomaly", "NexusCpuAnomaly"}
        )
        self.assertIn("Only the first counts", s6["counted_incident_note"])
        self.assertEqual(
            s6["fault_class_alternatives"][0]["alertnames"], ["NexusCpuAnomaly"]
        )

    def test_s6_void_if_latency_is_anomalous(self):
        truth, _ = load()
        void = truth["S6"]["void_if"]  # owner, 2026-11-20 (Block 5 finding (c))
        self.assertIn("metrics.zscores.latencyP95 > 3", void["condition"])
        self.assertTrue(void["outcome"].startswith("void"))
        self.assertEqual(
            [n for n in SCENARIOS if "void_if" in truth[n]], ["S6"]
        )  # S6 only

    def test_s3_names_the_commit_by_hand(self):
        v = load()[0]["S3"]["verification"]
        self.assertIn("names the commit to revert", v["hand_checked"])
        self.assertIn("previous image digest does not apply", v["hand_checked"])
        self.assertNotIn("secondary_not_computed", v)


if __name__ == "__main__":
    unittest.main()
