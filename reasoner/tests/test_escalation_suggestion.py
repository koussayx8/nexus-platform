"""ADR-044 on the Reasoner side: the output schema offers escalation.suggestedChange and runbookGap
(optional), rule 8 asks for them when escalating, and an escalation that carries them passes."""

import json
import os
import unittest

from nexus_reasoner import prompt, service

from .helpers import FakeOpener, bundle, good_proposal, reasoner


class SchemaAndRuleTest(unittest.TestCase):
    def test_the_schema_offers_both_fields_as_optional_strings(self):
        esc = next(
            s
            for s in prompt.PROPOSAL_SCHEMA["properties"]["escalation"]["anyOf"]
            if s.get("type") == "object"
        )
        self.assertEqual(
            esc["properties"]["suggestedChange"], {"type": "string", "maxLength": 4000}
        )
        self.assertEqual(
            esc["properties"]["runbookGap"], {"type": "string", "maxLength": 1000}
        )
        self.assertEqual(
            esc["required"], ["reason", "recommendation"]
        )  # still optional

    def test_rule_8_asks_for_them_and_says_they_are_never_applied(self):
        self.assertIn(
            "8. When you escalate, also fill escalation.suggestedChange", prompt.RULES
        )
        self.assertIn("never applied", prompt.RULES)
        self.assertEqual(prompt.VERSION, "prompt/v3")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["T_GROQ_KEY"] = "k1"
        self.b = bundle()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_an_escalation_with_a_suggestion_passes_unchanged(self):
        p = good_proposal(self.b)
        p["action"]["id"] = "escalate"
        p["escalation"] = {
            "reason": "the new revision fails readiness",
            "recommendation": {
                "kind": "git_revert",
                "detail": "revert the probe change",
            },
            "suggestedChange": "readinessProbe:\n  httpGet:\n    path: /ready\n",
            "runbookGap": "no runbook names the probe path",
        }
        op = FakeOpener(
            lambda u, b: {
                "choices": [{"message": {"content": json.dumps(p)}}],
                "usage": {},
            }
        )
        out = reasoner(op).propose({"bundle": self.b, "context": {
            "incidentUid": "u-1", "level": "L3", "catalogueVersion": "1", "mode": "llm",
            "model": "openai/gpt-oss-120b"}})  # fmt: skip
        self.assertEqual(out["proposal"]["escalation"], p["escalation"])
        self.assertEqual(out["metadata"]["promptVersion"], "prompt/v3")

    def test_a_non_string_suggestion_is_an_invalid_proposal(self):
        p = good_proposal(self.b)
        p["action"]["id"] = "escalate"
        p["escalation"] = {"reason": "r", "recommendation": {"kind": "investigate", "detail": "d"},
                           "suggestedChange": {"apply": True}}  # fmt: skip
        op = FakeOpener(
            lambda u, b: {
                "choices": [{"message": {"content": json.dumps(p)}}],
                "usage": {},
            }
        )
        with self.assertRaises(service.Rejected) as cm:
            reasoner(op).propose({"bundle": self.b, "context": {
                "incidentUid": "u-1", "level": "L3", "catalogueVersion": "1", "mode": "llm",
                "model": "openai/gpt-oss-120b"}})  # fmt: skip
        self.assertEqual(
            (cm.exception.status, cm.exception.error), (422, "invalid_proposal")
        )


if __name__ == "__main__":
    unittest.main()


class EvidenceV12Test(ServiceTest):
    """ADR-044: an evidence/v1.2 bundle (podSpec, rolloutDiff, cluster.activeAlerts) is accepted and
    shown to the model as data inside the bundle markers."""

    def test_a_v12_bundle_is_answered_and_its_additions_are_in_the_bundle_block(self):
        from nexus_operator import proposal as op_proposal

        b = dict(self.b)
        b.pop("hash")
        b["schema"] = "evidence/v1.2"
        b["workload"] = dict(b["workload"], podSpec=[{"name": "sample-api", "readinessProbe": "httpGet /readyz :8000"}],
                             rolloutDiff=["sample-api.readinessProbe: httpGet /ready :8000 -> httpGet /readyz :8000"])  # fmt: skip
        b["cluster"] = {
            "activeAlerts": [
                {"alertname": "NexusCpuAnomaly", "target": "nexus-prod/sample-api"}
            ]
        }
        b["hash"] = op_proposal.hash_of(b)
        seen = []
        p = good_proposal(b)

        def reply(url, body):
            seen.append(body["messages"][1]["content"])
            return {"choices": [{"message": {"content": json.dumps(p)}}], "usage": {}}

        out = reasoner(FakeOpener(reply)).propose({"bundle": b, "context": {
            "incidentUid": "u-1", "level": "L3", "catalogueVersion": "1", "mode": "llm",
            "model": "openai/gpt-oss-120b"}})  # fmt: skip
        self.assertEqual(out["proposal"]["action"]["id"], "restart_workload")
        user = seen[0]
        block = user[user.index("<<<BUNDLE") : user.index("BUNDLE>>>")]
        for text in ("rolloutDiff", "/readyz", "activeAlerts", "nexus-prod/sample-api"):
            self.assertIn(text, block)

    # the parent's tests are not run twice
    test_an_escalation_with_a_suggestion_passes_unchanged = None
    test_a_non_string_suggestion_is_an_invalid_proposal = None
