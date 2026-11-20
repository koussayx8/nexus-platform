"""prompt/v2 (ADR-042): the log excerpt, the request budget and the guard.

A live bundle of 2026-11-17 carried 200 log lines a pod (about 11,000 characters each) and Groq's
free tier refused the request with 413. The excerpt keeps error lines first, then the most recent
lines, per pod, with each line's index in the full bundle; the full bundle is unchanged.
"""

import ast
import json
import os
import re
import unittest

from nexus_operator import proposal
from nexus_reasoner import prompt, service

from .helpers import REPO, FakeOpener, bundle, good_proposal, reasoner, retriever

# The shape of a live line (nexus-dev, 2026-11-18: uvicorn access lines for /metrics), redacted.
ACCESS = 'INFO:     [REDACTED:ip]:{port} - "GET /metrics HTTP/1.1" 200 OK'
PODS = ("sample-api-8577d7dd4d-aaaaa", "sample-api-8577d7dd4d-bbbbb")


def s6_texts() -> list[str]:
    """INJECT_TEXTS from apps/sample-api/main.py, read without importing the app."""
    tree = ast.parse((REPO / "apps/sample-api/main.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "INJECT_TEXTS" for t in node.targets
        ):
            return [ast.literal_eval(v) for v in node.value.values]
    raise AssertionError("INJECT_TEXTS not found")


def access_lines(n: int, start: int = 0) -> list[str]:
    return [ACCESS.format(port=40000 + start + i) for i in range(n)]


def live_bundle(per_pod=200, pods=PODS, edit=None, sources=True) -> dict:
    """A sealed bundle the size of the live one: `per_pod` current lines for each pod."""
    b = bundle()
    b.pop("hash")
    b["pods"] = [
        {"name": p, "ready": True, "restarts": 0, "containers": []} for p in pods
    ]
    per = {p: access_lines(per_pod, 1000 * k) for k, p in enumerate(pods)}
    if edit:
        edit(per)
    b["logs"] = {"current": [ln for p in pods for ln in per[p]], "previous": []}
    if sources:
        b["logs"]["sources"] = [
            {"pod": p, "current": len(per[p]), "previous": 0} for p in pods
        ]
    b["hash"] = proposal.hash_of(b)
    return b


def largest_chunks() -> list[dict]:
    """The three largest runbook chunks: the most retrieval can add."""
    idx = retriever().index
    return sorted(idx.chunks, key=lambda c: len(c["text"]))[-3:]


def excerpt_lines(user: str, key: str) -> list[str]:
    """The excerpt's lines of `key`, as the prompt shows them."""
    start = user.index("<<<BUNDLE") + len("<<<BUNDLE")
    view = json.loads(user[start : user.index("BUNDLE>>>")])
    return view["logs"][key]


class Budget(unittest.TestCase):
    def test_a_live_size_bundle_fits_the_budget_at_30_lines_a_pod(self):
        # at the measured 2.5 chars a token (ADR-042 addendum) two full pods take level 1
        b = live_bundle()
        p = prompt.plan(b, largest_chunks(), "L3", "1")
        self.assertEqual(p.level, 1)
        self.assertLessEqual(p.tokens_estimate, prompt.PROMPT_BUDGET_TOKENS)
        self.assertLessEqual(p.tokens_estimate + 1024, prompt.REQUEST_GUARD_TOKENS)
        self.assertEqual(
            p.selection["pods"],
            [
                {"pod": pod, "lines": 200, "kept_error_lines": 0, "kept_recent_lines": 30, "dropped": 170}
                for pod in PODS
            ],
        )  # fmt: skip
        # v1 sent all 400 lines: several times the budget
        v1 = len(json.dumps(b, sort_keys=True, indent=1))
        self.assertGreater(
            prompt.estimate_tokens("x" * v1), prompt.PROMPT_BUDGET_TOKENS
        )

    def test_the_most_recent_lines_of_each_pod_are_kept_with_their_bundle_index(self):
        b = live_bundle()
        lines = excerpt_lines(prompt.plan(b, [], "L3", "1").user, "current")
        want = [i for start in (0, 200) for i in range(start + 160, start + 200)]
        self.assertEqual([int(re.match(r"\[(\d+)\]", ln)[1]) for ln in lines], want)
        for ln in lines:  # the index names the same line in the full bundle
            i = int(ln[1 : ln.index("]")])
            self.assertTrue(ln.endswith(b["logs"]["current"][i]))
            self.assertTrue(service.resolves(b, f"logs.current[{i}]"))

    def test_an_error_line_150_lines_back_still_reaches_the_prompt(self):
        err = "2026-11-18 12:31:02 ERROR sample_api db_error type=OperationalError message=connection refused"

        def edit(per):
            per[PODS[0]][50] = err  # 150 lines before the end of pod 0

        b = live_bundle(edit=edit)
        p = prompt.plan(b, [], "L3", "1")
        self.assertIn(f"[50] {err}", excerpt_lines(p.user, "current"))
        self.assertEqual(p.selection["pods"][0]["kept_error_lines"], 1)
        self.assertEqual(p.selection["pods"][0]["kept_recent_lines"], 39)
        lines = excerpt_lines(p.user, "current")
        self.assertEqual(
            lines, sorted(lines, key=lambda ln: int(ln[1 : ln.index("]")]))
        )

    def test_the_s6_lines_are_kept(self):
        """S6 writes each crafted text once with logger.error (apps/sample-api/main.py); the log
        format is "%(asctime)s %(levelname)s %(name)s %(message)s"."""
        texts = s6_texts()
        self.assertEqual(len(texts), 3)

        def edit(per):
            for (
                pod
            ) in PODS:  # written early, then 180 lines of ordinary traffic after them
                for k, text in enumerate(texts):
                    per[pod][17 + k] = (
                        f"2026-11-18 12:31:0{k},000 ERROR sample_api {text}"
                    )

        p = prompt.plan(live_bundle(edit=edit), [], "L3", "1")
        user = p.user
        for text in texts:
            self.assertEqual(user.count(text), 2, text[:40])  # once per pod
        self.assertEqual([s["kept_error_lines"] for s in p.selection["pods"]], [3, 3])

    def test_error_lines_are_capped_so_recent_context_remains(self):
        def edit(per):
            per[PODS[0]][:] = [f"ERROR sample_api db_error n={i}" for i in range(200)]

        p = prompt.plan(live_bundle(edit=edit), [], "L3", "1")
        s = p.selection["pods"][0]
        self.assertEqual((s["kept_error_lines"], s["kept_recent_lines"]), (30, 10))

    def test_previous_container_lines_count_against_the_same_pod(self):
        b = live_bundle()
        b.pop("hash")
        b["logs"]["previous"] = [
            "Traceback (most recent call last):",
            "MemoryError",
        ] + access_lines(30)
        b["logs"]["sources"][0]["previous"] = 32
        b["hash"] = proposal.hash_of(b)
        p = prompt.plan(b, [], "L3", "1")
        s = p.selection["pods"][0]
        self.assertEqual(s["lines"], 232)
        self.assertIn(
            "[0] Traceback (most recent call last):", excerpt_lines(p.user, "previous")
        )
        self.assertEqual(s["kept_error_lines"] + s["kept_recent_lines"], 40)

    def test_a_bundle_without_sources_is_one_slice_with_the_pods_budget(self):
        p = prompt.plan(live_bundle(sources=False), [], "L3", "1")
        [s] = p.selection["pods"]
        self.assertEqual(
            (s["pod"], s["lines"], s["kept_recent_lines"]), (None, 400, 80)
        )

    def test_sources_that_do_not_add_up_are_ignored(self):
        b = live_bundle()
        b["logs"]["sources"][0]["current"] = (
            7  # (unsealed: plan() does not check the hash)
        )
        [s] = prompt.plan(b, [], "L3", "1").selection["pods"]
        self.assertIsNone(s["pod"])

    def test_long_lines_are_cut_and_events_capped(self):
        def edit(per):
            per[PODS[0]][-1] = "ERROR " + "x" * 5000

        b = live_bundle(edit=edit)
        b["events"] = [
            {"reason": "BackOff", "type": "Warning", "count": i, "lastSeen": "t"}
            for i in range(50)
        ]
        p = prompt.plan(b, [], "L3", "1")
        last = excerpt_lines(p.user, "current")[29]  # level 1: 30 lines a pod
        self.assertTrue(last.endswith(" [line cut]") and len(last) < 400)
        self.assertEqual(
            (p.selection["eventsShown"], p.selection["eventsTotal"]), (15, 50)
        )

    def test_three_full_pods_of_long_error_lines_are_trimmed_and_recorded(self):
        pods = PODS + ("sample-api-8577d7dd4d-ccccc",)

        def edit(per):
            for pod in pods:
                per[pod][:] = [
                    f"ERROR sample_api {'e' * 280} n={i}" for i in range(200)
                ]

        p = prompt.plan(live_bundle(pods=pods, edit=edit), largest_chunks(), "L3", "1")
        self.assertGreater(p.level, 0)
        self.assertLessEqual(p.tokens_estimate, prompt.PROMPT_BUDGET_TOKENS)

    def test_nothing_is_sent_over_the_guard(self):
        huge = [{"id": "x#1", "runbook": "x", "version": 1, "text": "y" * 30000}]
        with self.assertRaises(prompt.TooLarge) as cm:
            prompt.plan(live_bundle(), huge, "L3", "1")
        self.assertGreater(cm.exception.estimate, prompt.REQUEST_GUARD_TOKENS)

    def test_the_prompt_says_the_logs_are_an_excerpt_and_to_abstain_when_short(self):
        self.assertEqual(prompt.VERSION, "prompt/v2.1")
        self.assertIn("excerpt", prompt.RULES)
        self.assertIn("escalate or abstain", prompt.RULES)


class Service(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["T_GROQ_KEY"] = "k1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    @staticmethod
    def ctx(model):
        return {
            "incidentUid": "u1",
            "level": "L3",
            "catalogueVersion": "1",
            "mode": "llm",
            "model": model,
        }

    def test_r2_and_r3_get_the_same_prompt_and_the_selection_is_in_the_metadata(self):
        b = live_bundle()
        sent = {}
        for arm, model in (("R2", "openai/gpt-oss-20b"), ("R3", "openai/gpt-oss-120b")):
            op = FakeOpener(
                lambda url, body: {
                    "choices": [{"message": {"content": json.dumps(good_proposal(b))}}],
                    "usage": {},
                }
            )
            out = reasoner(op).propose({"bundle": b, "context": self.ctx(model)})
            sent[arm] = op.calls[0]["body"]["messages"]
            m = out["metadata"]
            self.assertEqual(m["promptVersion"], "prompt/v2.1")
            self.assertEqual(m["promptTrimLevel"], 1)
            self.assertLessEqual(m["promptTokensEstimate"], prompt.PROMPT_BUDGET_TOKENS)
            self.assertEqual(
                [s["dropped"] for s in m["logSelection"]["pods"]], [170, 170]
            )
        self.assertEqual(sent["R2"], sent["R3"])

    def test_a_ref_to_a_kept_line_is_accepted_against_the_full_bundle(self):
        b = live_bundle()
        doc = good_proposal(b, refs=("logs.current[399]", "logs.current[199]"))
        op = FakeOpener(
            lambda url, body: {
                "choices": [{"message": {"content": json.dumps(doc)}}],
                "usage": {},
            }
        )
        out = reasoner(op).propose(
            {"bundle": b, "context": self.ctx("openai/gpt-oss-120b")}
        )
        self.assertEqual(
            out["proposal"]["evidenceRefs"], ["logs.current[399]", "logs.current[199]"]
        )

    def test_over_the_guard_is_refused_before_any_provider_call(self):
        b = live_bundle()
        rs = reasoner(
            FakeOpener(lambda url, body: self.fail("the provider was called"))
        )
        rs.retriever.retrieve = lambda bundle: [
            {"id": "x#1", "runbook": "x", "version": 1, "text": "y" * 30000}
        ]
        with self.assertRaises(service.Rejected) as cm:
            rs.propose({"bundle": b, "context": self.ctx("openai/gpt-oss-120b")})
        e = cm.exception
        self.assertEqual((e.status, e.error), (422, "prompt_too_large"))
        self.assertGreater(
            e.extra["metadata"]["requestTokensEstimate"], prompt.REQUEST_GUARD_TOKENS
        )
