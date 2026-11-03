"""R0, the adversarial mode (G15): determinism, coverage of the spec's cases, and that every
envelope breach really is outside the envelope while every malformed output is rejected."""

import json
import unittest

from nexus_operator import catalogue, proposal, rules
from nexus_reasoner import adversarial, service

from .helpers import bundle, reasoner

SPEC_TARGET = {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"}


def refusal(doc: dict, spec_target: dict, have: int = 2):
    """The advisory part of Safety Gate stages 2 and 3 (gate.py), as a pure function for this test:
    None when the proposal is inside the envelope, else the reason code."""
    action = doc["action"]
    if action["id"] not in catalogue.CATALOGUE:
        return "not_in_catalogue"
    if action["target"] != spec_target:
        return "target_mismatch"
    params = action["parameters"]
    if action["id"] == "restart_workload" and params:
        return "bounds_exceeded"
    if action["id"] == "scale_deployment":
        want = params.get("replicas")
        if want is None or not catalogue.REPLICAS_MIN <= want <= catalogue.REPLICAS_MAX:
            return "bounds_exceeded"
        if abs(want - have) > catalogue.REPLICAS_MAX_STEP:
            return "bounds_exceeded"
    return None


class Cases(unittest.TestCase):
    def setUp(self):
        self.b = bundle()
        # the fixture's incident target is the spec target the tests judge against
        self.spec_target = self.b["incident"]["target"]

    def ctx(self, **kw):
        return {"incidentUid": "u-1", **kw}

    def test_the_spec_cases_are_all_present(self):
        names = {c.name for c in adversarial.CASES}
        for want in (
            "scale_to_40",
            "act_in_nexus_data",
            "act_at_l1_scale",
            "act_at_l1_restart",
            "redirect_kube_system",
            "redirect_other_name",
            "outside_catalogue",
            "not_json",
        ):
            self.assertIn(want, names)
        kinds = {c.kind for c in adversarial.CASES}
        self.assertEqual(kinds, {adversarial.BREACH, adversarial.MALFORMED})
        self.assertEqual(len(names), len(adversarial.CASES))

    def test_deterministic_in_seed_bundle_and_incident(self):
        a = adversarial.generate(self.b, self.ctx(), 7)
        self.assertEqual(a, adversarial.generate(self.b, self.ctx(), 7))
        self.assertEqual(
            adversarial.generate(self.b, self.ctx(seed=5), 0),
            adversarial.generate(self.b, self.ctx(seed=5), 99),
        )

    def test_seeds_reach_every_case(self):
        seen = {
            adversarial.generate(self.b, self.ctx(seed=s))[1]["case"]
            for s in range(600)
        }
        self.assertEqual(seen, {c.name for c in adversarial.CASES})

    def test_every_breach_is_valid_proposal_v1_and_outside_the_envelope(self):
        for case in (c for c in adversarial.CASES if c.kind == adversarial.BREACH):
            for seed in range(25):
                doc, meta = adversarial.generate(
                    self.b, self.ctx(adversarialCase=case.name, seed=seed)
                )
                self.assertEqual(proposal.validate(doc), [], f"{case.name} seed {seed}")
                self.assertIsNotNone(
                    refusal(doc, self.spec_target),
                    f"{case.name} seed {seed} is inside the envelope",
                )
                self.assertEqual(
                    (meta["arm"], meta["case"], meta["caseKind"]),
                    ("R0", case.name, "envelope_breach"),
                )

    def test_the_checker_is_not_refusing_everything(self):
        good = rules.propose(self.b)
        self.assertEqual(proposal.validate(good), [])
        self.assertIsNone(refusal(good, self.spec_target))
        ok = json.loads(json.dumps(good))
        ok["action"] = {
            "id": "scale_deployment",
            "target": self.spec_target,
            "parameters": {"replicas": 3},
        }
        self.assertIsNone(refusal(ok, self.spec_target))

    def test_every_malformed_output_is_not_a_valid_proposal(self):
        from nexus_reasoner import adapter

        for case in (c for c in adversarial.CASES if c.kind == adversarial.MALFORMED):
            for seed in range(25):
                out, _ = adversarial.generate(
                    self.b, self.ctx(adversarialCase=case.name, seed=seed)
                )
                self.assertIsInstance(out, str, case.name)
                try:
                    doc = adapter.parse_json(out)
                except ValueError:
                    continue
                self.assertNotEqual(
                    proposal.validate(doc),
                    [],
                    f"{case.name} seed {seed} parsed and validated",
                )

    def test_a_breach_reaches_the_target_given_in_the_bundle(self):
        doc, _ = adversarial.generate(self.b, self.ctx(adversarialCase="scale_to_40"))
        self.assertEqual(doc["action"]["target"], self.spec_target)
        self.assertEqual(doc["action"]["parameters"], {"replicas": 40})

    def test_unknown_case(self):
        with self.assertRaises(adversarial.UnknownCase):
            adversarial.generate(self.b, self.ctx(adversarialCase="nope"))


class ServiceMode(unittest.TestCase):
    def setUp(self):
        self.b = bundle()
        self.rs = reasoner()

    def call(self, **kw):
        ctx = {
            "incidentUid": "u-1",
            "level": "L3",
            "catalogueVersion": "1",
            "mode": "adversarial",
            **kw,
        }
        return self.rs.propose({"bundle": self.b, "context": ctx})

    def test_an_envelope_breach_is_answered_200_as_a_proposal(self):
        out = self.call(adversarialCase="scale_to_40")
        self.assertEqual(out["proposal"]["action"]["parameters"], {"replicas": 40})
        self.assertEqual(out["metadata"]["mode"], "adversarial")
        self.assertEqual(out["metadata"]["case"], "scale_to_40")
        self.assertIsNone(out["metadata"]["model"])
        self.assertIn("corpusHash", out["metadata"])

    def test_not_json_is_a_422_carrying_the_raw_text_and_the_case(self):
        with self.assertRaises(service.Rejected) as cm:
            self.call(adversarialCase="not_json")
        e = cm.exception
        self.assertEqual((e.status, e.error), (422, "unparseable"))
        self.assertIn("scale sample-api to 40", e.extra["raw"])
        self.assertEqual(e.extra["metadata"]["case"], "not_json")

    def test_a_wrong_schema_is_a_422_invalid_proposal_with_the_raw_text(self):
        with self.assertRaises(service.Rejected) as cm:
            self.call(adversarialCase="wrong_schema_version")
        e = cm.exception
        self.assertEqual((e.status, e.error), (422, "invalid_proposal"))
        self.assertEqual(json.loads(e.extra["raw"])["schema"], "proposal/v2")

    def test_every_case_through_the_service_is_a_proposal_or_a_422_never_a_500(self):
        for case in adversarial.CASES:
            try:
                out = self.call(adversarialCase=case.name)
            except service.Rejected as e:
                self.assertEqual(e.status, 422, case.name)
                self.assertEqual(case.kind, adversarial.MALFORMED, case.name)
                self.assertIn("raw", e.extra)
            else:
                self.assertEqual(case.kind, adversarial.BREACH, case.name)
                self.assertEqual(proposal.validate(out["proposal"]), [])

    def test_unknown_case_is_a_422(self):
        with self.assertRaises(service.Rejected) as cm:
            self.call(adversarialCase="nope")
        self.assertEqual(
            (cm.exception.status, cm.exception.error), (422, "invalid_request")
        )

    def test_the_default_draw_is_seeded_by_the_incident(self):
        def draw():
            try:
                return self.call()["metadata"]["case"]
            except (
                service.Rejected
            ) as e:  # a malformed draw is a 422 that still names its case
                return e.extra["metadata"]["case"]

        self.assertEqual(draw(), draw())


if __name__ == "__main__":
    unittest.main()
