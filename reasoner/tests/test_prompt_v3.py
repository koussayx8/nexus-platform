"""prompt/v3 (ADR-045): the reference wording of rules 5 and 7, the per-model output reservation,
and the ref check, unchanged and strict, against the four near-misses bake-off round 3 found."""

import json
import os
import unittest

from nexus_reasoner import config, prompt, service

from .helpers import FakeOpener, bundle, good_proposal, reasoner


class RulesTest(unittest.TestCase):
    def rule(self, n):
        return next(ln for ln in prompt.RULES.splitlines() if ln.startswith(f"{n}. "))

    def test_rule_5_says_exact_paths_single_indexes_and_an_empty_json_list(self):
        r5 = self.rule(5)
        for text in (
            "exact paths into the bundle, one per entry, with single indexes",
            "pods[0].containers[0].reason",
            "Never a range such as logs.current[0:53]",
            "never a path that is not in the bundle",
            "the chunk ids written in square brackets before each runbook chunk",
            "rb-bad-rollout#3",
            "cite only chunks you used",
            '"runbookRefs": [], never a list holding the text "[]"',
        ):
            self.assertIn(text, r5)
        self.assertNotIn("use [] when", r5)  # the v2 wording models copied literally

    def test_rule_7_says_logs_excerpt_is_never_cited(self):
        self.assertIn(
            "it is metadata about the excerpt and is never cited", self.rule(7)
        )

    def test_every_other_rule_is_unchanged(self):
        self.assertEqual(prompt.VERSION, "prompt/v3")
        for n in (1, 2, 3, 4, 6, 8):
            self.rule(n)  # still there
        self.assertEqual(
            len(
                [ln for ln in prompt.RULES.splitlines() if ln[:2].rstrip(".").isdigit()]
            ),
            8,
        )

    def test_the_chunk_ids_appear_in_square_brackets_as_rule_5_says(self):
        text = prompt.chunks_text(
            [
                {
                    "id": "rb-bad-rollout#3",
                    "runbook": "rb-bad-rollout",
                    "version": 2,
                    "text": "t",
                }
            ]
        )
        self.assertTrue(text.startswith("[rb-bad-rollout#3] "))


class ReservationTest(unittest.TestCase):
    def test_the_guard_reserves_the_models_own_output_cap(self):
        b = bundle()
        big = [
            {"id": "x#1", "runbook": "x", "version": 1, "text": "y" * 65000}
        ]  # ~26K tokens
        p = prompt.plan(b, big, "L3", "1", output_tokens=1024)
        self.assertGreater(
            p.tokens_estimate, prompt.PROMPT_BUDGET_TOKENS
        )  # over the budget,
        self.assertLessEqual(
            p.tokens_estimate + 1024, prompt.REQUEST_GUARD_TOKENS
        )  # under the guard
        with self.assertRaises(prompt.TooLarge):
            prompt.plan(b, big, "L3", "1", output_tokens=16384)


class StrictRefsTest(unittest.TestCase):
    """The near-misses of round 3 are still refused: the wording changed, the check did not."""

    def setUp(self):
        self.env = dict(os.environ)
        os.environ["T_GROQ_KEY"] = "k1"
        self.b = bundle()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_the_paths_round_3_cited_do_not_resolve(self):
        for ref in (
            "logs.current[0:53]",
            "logs.excerpt",
            "logs.excerpt.pods[0]",
            "pods[0].state",
        ):
            self.assertFalse(service.resolves(self.b, ref), ref)
        self.assertTrue(service.resolves(self.b, "metrics.zscores.latencyP95"))

    def call(self, **over):
        doc = good_proposal(self.b) | over
        op = FakeOpener(
            lambda u, body: {
                "choices": [{"message": {"content": json.dumps(doc)}}],
                "usage": {},
            }
        )
        ctx = {"incidentUid": "u-1", "level": "L3", "catalogueVersion": "1", "mode": "llm",
               "model": "openai/gpt-oss-120b"}  # fmt: skip
        return reasoner(op).propose({"bundle": self.b, "context": ctx})

    def test_a_list_holding_the_text_brackets_is_refused(self):
        with self.assertRaises(service.Rejected) as cm:
            self.call(runbookRefs=["[]"])
        self.assertEqual(
            (cm.exception.status, cm.exception.error), (422, "invalid_refs")
        )

    def test_a_range_and_an_excerpt_citation_are_refused(self):
        for refs in (["logs.current[0:53]"], ["logs.excerpt"]):
            with self.assertRaises(service.Rejected, msg=refs) as cm:
                self.call(evidenceRefs=refs)
            self.assertEqual(cm.exception.error, "invalid_refs")

    def test_an_empty_list_is_accepted(self):
        self.assertEqual(self.call(runbookRefs=[])["proposal"]["runbookRefs"], [])


class ServiceReservationTest(StrictRefsTest):
    def test_the_service_reserves_the_entrys_cap(self):
        cfg = config.parse({"indexPath": "x", "scoreFloor": 1.0, "models": {
            "big": {"provider": "groq", "model": "m", "baseUrl": "https://g.test/v1", "apiKeyEnv": "T_GROQ_KEY",
                    "maxOutputTokens": 16384}}})  # fmt: skip
        rs = service.Reasoner(
            cfg, reasoner().retriever, FakeOpener(lambda u, b: self.fail("called"))
        )
        rs.retriever.retrieve = lambda bundle: [
            {"id": "x#1", "runbook": "x", "version": 1, "text": "y" * 65000}
        ]
        ctx = {
            "incidentUid": "u-1",
            "level": "L3",
            "catalogueVersion": "1",
            "mode": "llm",
            "model": "big",
        }
        with self.assertRaises(service.Rejected) as cm:
            rs.propose({"bundle": self.b, "context": ctx})
        self.assertEqual(cm.exception.error, "prompt_too_large")

    test_a_list_holding_the_text_brackets_is_refused = None
    test_a_range_and_an_excerpt_citation_are_refused = None
    test_an_empty_list_is_accepted = None
    test_the_paths_round_3_cited_do_not_resolve = None


if __name__ == "__main__":
    unittest.main()
