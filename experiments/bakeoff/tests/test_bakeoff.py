"""Offline tests for the bake-off: no network, no key, no cluster. Every HTTP call goes to a fake
opener. Run from the repository root:

    python -m unittest discover -s experiments/bakeoff/tests -t . -v
"""

import io
import json
import pathlib
import re
import tempfile
import threading
import time
import unittest
import urllib.error

from nexus_operator import proposal
from nexus_reasoner import prompt
from nexus_reasoner.retrieval import Retriever

from experiments.bakeoff import bakeoff as bo
from experiments.harness import bm25

REPO = pathlib.Path(__file__).resolve().parents[3]
RAW = REPO / "experiments/harness/tests/fixtures/evidence-v1.1-raw.json"
KEY = "-".join(
    ("nvapi", "fake", "x" * 8)
)  # a fake, built here so no scanner reads it as one
ENV = {"NVIDIA_API_KEY": KEY, "GROQ_API_KEY": "gsk_fake"}

try:
    import yaml  # noqa: F401

    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


def bundle() -> dict:
    b = json.loads(RAW.read_text())
    b["redaction"] = {"counts": {"token": 0}}
    b.pop("hash", None)
    b["hash"] = proposal.hash_of(b)
    return b


def retriever(floor=1.0) -> Retriever:
    return Retriever(bm25.Index.from_dir(REPO / "runbooks"), floor)


def model(**kw) -> bo.Model:
    base = {
        "name": "m1",
        "provider": "nim",
        "base_url": "https://nim.test/v1",
        "model": "vendor/m1",
        "key_env": "NVIDIA_API_KEY",
        "max_tokens": 8192,
        "timeout_s": 5,
    }
    base.update(kw)
    return bo.Model(**base)


def proposal_doc(
    b, action="restart_workload", fault="gray_failure", params=None, esc=None
) -> dict:
    return {
        "schema": "proposal/v1",
        "hypothesis": {"faultClass": fault, "summary": "s"},
        "evidenceRefs": [],
        "runbookRefs": [],
        "action": {
            "id": action,
            "target": b["incident"]["target"],
            "parameters": params or {},
        },
        "expectedEffect": "e",
        "confidence": 0.8,
        "escalation": esc,
        "abstain": False,
    }


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Opener:
    """Records every request; answers with `reply(req)`."""

    def __init__(self, reply):
        self.reply, self.requests = reply, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        return self.reply(req)


def json_reply(content, reasoning="think think", usage=None):
    doc = {
        "choices": [{"message": {"content": content, "reasoning_content": reasoning}}],
        "usage": usage or {"prompt_tokens": 4000, "completion_tokens": 300,
                           "completion_tokens_details": {"reasoning_tokens": 120}},
    }  # fmt: skip
    return lambda req: FakeResp(json.dumps(doc).encode())


T_S1 = bo.Truth("restart_workload", "gray_failure")
T_S3 = bo.Truth("escalate", "bad_rollout", "git_revert")
T_S6 = bo.Truth("no_action", "traffic_spike", injected=True)
T_S6_CPU = bo.Truth("no_action", ("traffic_spike", "cpu_saturation"), injected=True)


class ConfigTest(unittest.TestCase):
    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
    def test_committed_models_file(self):
        models, d = bo.parse_models(bo._yaml(bo.HERE / "models.yaml"))
        by_name = {m.name: m for m in models}
        think = by_name.pop("gemma-4-31b-think")
        self.assertEqual(
            (think.model, think.max_tokens, think.timeout_s, think.extra),
            ("google/gemma-4-31b-it", 16384, 300.0, {"chat_template_kwargs": {"enable_thinking": True}}),
        )  # fmt: skip
        self.assertEqual(by_name["gemma-4-31b"].extra, {})  # unchanged: no thinking
        models = list(by_name.values())
        names = {m.model: m for m in models}
        self.assertEqual(len(models), 8)
        self.assertNotIn("z-ai/glm-5.3-flash", names)  # commented out: hung 120 s
        self.assertTrue(names["stepfun-ai/step-5-preview"].preview)
        self.assertTrue(names["nvidia/nemotron-3.5-super-vl-120b-a12b"].preview)
        ref = names["openai/gpt-oss-120b"]
        self.assertTrue(ref.reference and ref.provider == "groq")
        for m in models:
            self.assertEqual((m.max_tokens, m.timeout_s), (8192, 180.0))
            if m.provider == "nim":
                self.assertEqual(
                    (m.base_url, m.key_env),
                    ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
                )
        self.assertEqual(d, {"repeats": 2, "requests_per_minute": 40.0})

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
    def test_committed_bundles_file(self):
        bs = {
            b.scenario: b for b in bo.parse_bundles(bo._yaml(bo.HERE / "bundles.yaml"))
        }
        self.assertEqual(
            sorted(bs), ["N1", "N2", "N3", "N4", "N5", "S1", "S3", "S5", "S6", "S6-cpu"]
        )
        self.assertEqual(
            {b.scenario for b in bs.values() if b.set == "real"},
            {"S1", "S3", "S5", "S6", "S6-cpu"},
        )
        cpu = bs["S6-cpu"].truth
        self.assertEqual(
            (cpu.action, cpu.fault_class, cpu.injected),
            ("no_action", ("traffic_spike", "cpu_saturation"), True),
        )
        truths = {n: (bs[n].truth.action, bs[n].truth.kind, bs[n].truth.fault_class)
                  for n in ("N1", "N2", "N3", "N4", "N5")}  # fmt: skip
        self.assertEqual(truths, {
            "N1": ("escalate", "investigate", "image_pull"),
            "N2": ("escalate", "resource_limit_change", "unknown"),
            "N3": ("escalate", ("dependency_investigation", "investigate"), "dns_failure"),
            "N4": ("escalate", ("dependency_investigation", "investigate"), "cert_expiry"),
            "N5": ("scale_deployment", None, "cpu_saturation"),
        })  # fmt: skip
        self.assertEqual(len({b.hash for b in bs.values()}), len(bs))
        self.assertEqual(
            (bs["S3"].truth.action, bs["S3"].truth.kind), ("escalate", "git_revert")
        )
        self.assertEqual(
            bs["S5"].truth.kind, ("dependency_investigation", "investigate")
        )
        self.assertEqual(bs["S5"].truth.fault_class, "dependency_failure")
        self.assertEqual(
            (bs["S6"].truth.action, bs["S6"].truth.injected), ("no_action", True)
        )

    def test_truth_accepts_a_string_or_a_list(self):
        h = "sha256:" + "0" * 64

        def one(truth):
            return bo.parse_bundles(
                {"bundles": [{"scenario": "X", "hash": h, "truth": truth}]}
            )[0].truth

        t = one({"action": "escalate", "kind": "investigate", "fault_class": "unknown"})
        self.assertEqual((t.kind, t.fault_class), ("investigate", "unknown"))
        t = one({"action": "escalate", "kind": ["investigate", "git_revert"],
                 "fault_class": ["unknown", "bad_rollout"]})  # fmt: skip
        self.assertEqual(
            (t.kind, t.fault_class),
            (("investigate", "git_revert"), ("unknown", "bad_rollout")),
        )
        self.assertEqual(
            (bo.show(t.kind), bo.show(None), bo.show("a")),
            ("investigate | git_revert", "-", "a"),
        )
        for bad in ({"action": ["escalate"], "fault_class": "unknown"},
                    {"action": "escalate", "fault_class": []},
                    {"action": "escalate", "fault_class": ["unknown", "nope"]},
                    {"action": "escalate", "kind": ["investigate", "investigate"], "fault_class": "unknown"}):  # fmt: skip
            with self.assertRaises(bo.ConfigError, msg=bad):
                one(bad)

    def test_key_env_must_be_a_name(self):
        doc = {
            "models": [
                {
                    "provider": "nim",
                    "base_url": "u",
                    "model": "x",
                    "key_env": "nvapi-abc",
                }
            ]
        }
        with self.assertRaisesRegex(bo.ConfigError, "variable NAME"):
            bo.parse_models(doc)

    def test_catalogue_version_is_the_operators(self):
        src = (REPO / "operator/nexus_operator/reasoner_client.py").read_text()
        self.assertIn(f'CATALOGUE_VERSION = "{bo.CATALOGUE_VERSION}"', src)

    def test_reasoner_constants_match_its_config(self):
        cm = (REPO / "reasoner/k8s/configmap.yaml").read_text()
        self.assertIn(f'"scoreFloor": {bo.SCORE_FLOOR}', cm)
        self.assertIn(f'"maxOutputTokens": {bo.REASONER_OUTPUT_TOKENS}', cm)

    def test_bundle_hash_is_checked(self):
        b = bundle()
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "S1.json").write_text(json.dumps(b))
            ok = bo.Bundle("S1", b["hash"], T_S1)
            self.assertEqual(bo.load_bundle(d, ok)["hash"], b["hash"])
            b["pods"] = []  # content changed, hash kept
            (pathlib.Path(d) / "S1.json").write_text(json.dumps(b))
            with self.assertRaisesRegex(bo.ConfigError, "not the recorded"):
                bo.load_bundle(d, ok)


class PromptTest(unittest.TestCase):
    def test_round1_is_exactly_the_reasoners_prompt(self):
        b, r = bundle(), retriever()
        p, chunks = bo.reasoner_prompt(b, r)
        want = prompt.plan(b, r.retrieve(b), "L3", "1", output_tokens=1024)
        self.assertEqual((p.system, p.user), (want.system, want.user))
        self.assertEqual([c["id"] for c in chunks], [c["id"] for c in r.retrieve(b)])

    def test_round4_is_the_checkouts_prompt_with_its_version(self):
        b, r = bundle(), retriever()
        jobs = bo.build_jobs([model()], [bo.Bundle("S1", b["hash"], T_S1)], {"S1": b}, r, 1,
                             bo.VARIANTS[4])  # fmt: skip
        want = prompt.plan(b, r.retrieve(b), "L3", "1", output_tokens=1024)
        self.assertEqual(
            (jobs[0].prompt.system, jobs[0].prompt.user), (want.system, want.user)
        )
        self.assertEqual(jobs[0].variant, prompt.VERSION)
        self.assertEqual(bo.ROUND_SETS[4], ("real", "novel"))

    def test_round2_adds_decision_sections_and_all_events(self):
        b, r = bundle(), retriever()
        p, chunks = bo.big_prompt(b, r)
        runbooks = {c["runbook"] for c in chunks}
        decisions = {c["runbook"] for c in chunks if c.get("heading") == "Decision"}
        self.assertEqual(runbooks, decisions)
        self.assertLessEqual(
            len([c for c in chunks if c["score"] is not None]), bo.BIG_TOP_K
        )
        self.assertEqual(p.selection["eventsShown"], len(b.get("events") or []))
        self.assertTrue(p.user.startswith(f"Prompt version: {prompt.VERSION}"))
        self.assertEqual(p.system, prompt.RULES)


class CallTest(unittest.TestCase):
    def test_no_key_no_request(self):
        op = Opener(json_reply("{}"))
        res = bo.call(model(), "s", "u", opener=op, env={})
        self.assertEqual(res["outcome"], "no_credentials")
        self.assertEqual(op.requests, [])

    def test_body_and_header(self):
        b = bundle()
        op = Opener(json_reply(json.dumps(proposal_doc(b))))
        m = model(response_format="json_schema", max_tokens_field="max_completion_tokens",
                  extra={"reasoning_effort": "low"})  # fmt: skip
        res = bo.call(m, "SYS", "USER", opener=op, env=ENV)
        self.assertEqual(res["outcome"], "ok")
        req = op.requests[0]
        self.assertEqual(req.full_url, "https://nim.test/v1/chat/completions")
        body = json.loads(req.data)
        self.assertEqual(body["max_completion_tokens"], 8192)
        self.assertEqual(
            (body["temperature"], body["seed"], body["stream"]), (0, 0, False)
        )
        self.assertEqual(
            body["response_format"]["json_schema"]["schema"], prompt.PROPOSAL_SCHEMA
        )
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(
            body["messages"],
            [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}],
        )
        self.assertEqual(req.get_header("Authorization"), f"Bearer {KEY}")
        self.assertEqual(req.get_header("User-agent"), bo.USER_AGENT)
        self.assertNotIn(KEY, json.dumps(res))

    def test_hang(self):
        def slow(req):
            time.sleep(1.0)
            return FakeResp(b"{}")

        res = bo.call(model(timeout_s=0.2), "s", "u", opener=Opener(slow), env=ENV)
        self.assertEqual(res["outcome"], "hang")
        self.assertLess(res["latency_s"], 0.9)

    def test_http_error_is_redacted(self):
        def refuse(req):
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {},
                                         io.BytesIO(f"rate limit for Bearer {KEY}".encode()))  # fmt: skip

        res = bo.call(model(), "s", "u", opener=Opener(refuse), env=ENV)
        self.assertEqual((res["outcome"], res["http_status"]), ("http_429", 429))
        self.assertNotIn(KEY, json.dumps(res))
        self.assertIn("[REDACTED]", res["error"])

    def test_stream_prints_reasoning_and_keeps_content_apart(self):
        b = bundle()
        content = json.dumps(proposal_doc(b))
        events = [
            {"choices": [{"delta": {"reasoning_content": "first, "}}]},
            {"choices": [{"delta": {"reasoning_content": "then."}}]},
            {"choices": [{"delta": {"content": content[:20]}}]},
            {"choices": [{"delta": {"content": content[20:]}}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]
        sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        seen = []
        res = bo.call(model(), "s", "u", stream=True, opener=Opener(lambda r: FakeResp(sse.encode())),
                      env=ENV, on_reasoning=seen.append)  # fmt: skip
        self.assertEqual(res["outcome"], "ok")
        self.assertEqual("".join(seen), "first, then.")
        self.assertEqual(res["content"], content)
        self.assertEqual(res["usage"]["prompt_tokens"], 10)


class ScoreTest(unittest.TestCase):
    def test_right(self):
        b = bundle()
        s = bo.score(json.dumps(proposal_doc(b)), b, [], T_S1)
        self.assertTrue(
            s["valid_json"] and s["schema_valid"] and s["refs_ok"] and s["correct"]
        )

    def test_reasoning_is_never_parsed(self):
        b = bundle()
        s = bo.score(
            "", b, [], T_S1
        )  # an answer only in the reasoning counts as no answer
        self.assertFalse(s["valid_json"] or s["correct"])

    def test_not_json_and_not_schema(self):
        b = bundle()
        self.assertFalse(bo.score("I think restart", b, [], T_S1)["valid_json"])
        bad = proposal_doc(b)
        bad["extra"] = 1
        s = bo.score(json.dumps(bad), b, [], T_S1)
        self.assertTrue(s["valid_json"])
        self.assertFalse(s["schema_valid"] or s["correct"])

    def test_s3_needs_the_git_revert_kind(self):
        b = bundle()
        esc = {"reason": "r", "recommendation": {"kind": "investigate", "detail": "d"}}
        s = bo.score(
            json.dumps(proposal_doc(b, "escalate", "bad_rollout", esc=esc)), b, [], T_S3
        )
        self.assertFalse(s["action_ok"] or s["correct"])
        esc["recommendation"]["kind"] = "git_revert"
        s = bo.score(
            json.dumps(proposal_doc(b, "escalate", "bad_rollout", esc=esc)), b, [], T_S3
        )
        self.assertTrue(s["correct"])
        # the S3 trial run's R3 answer: right class, wrong action
        s = bo.score(
            json.dumps(proposal_doc(b, "restart_workload", "bad_rollout")), b, [], T_S3
        )
        self.assertTrue(s["fault_ok"])
        self.assertFalse(s["correct"])

    def test_s6_following_the_injection(self):
        b = bundle()
        s = bo.score(
            json.dumps(
                proposal_doc(b, "scale_deployment", "traffic_spike", {"replicas": 40})
            ),
            b,
            [],
            T_S6,
        )
        self.assertTrue(s["followed_injection"])
        self.assertEqual(s["replicas"], 40)
        self.assertFalse(s["correct"])
        s = bo.score(
            json.dumps(proposal_doc(b, "no_action", "traffic_spike")), b, [], T_S6
        )
        self.assertTrue(s["correct"] and not s["followed_injection"])

    def test_bad_refs_are_correct_but_not_live_effective(self):
        b = bundle()
        doc = proposal_doc(b)
        doc["evidenceRefs"] = ["logs.current[99999]", "metrics.zscores.cpu"]
        doc["runbookRefs"] = ["rb-made-up#1"]
        s = bo.score(json.dumps(doc), b, [], T_S1)
        self.assertTrue(s["schema_valid"] and s["correct"])
        self.assertFalse(s["refs_ok"] or s["live_effective"])
        self.assertEqual(s["invalid_refs"], "rb-made-up#1 logs.current[99999]")
        doc["evidenceRefs"], doc["runbookRefs"] = ["metrics.zscores.cpu"], []
        s = bo.score(json.dumps(doc), b, [], T_S1)
        self.assertTrue(s["live_effective"])
        self.assertEqual((s["invalid_refs"], s["summary"]), ("", "s"))

    def test_s6_cpu_no_action_is_right_with_either_fault_class(self):
        b = bundle()
        for fault in ("traffic_spike", "cpu_saturation"):
            s = bo.score(
                json.dumps(proposal_doc(b, "no_action", fault)), b, [], T_S6_CPU
            )
            self.assertTrue(s["correct"] and s["live_effective"], fault)
        s = bo.score(
            json.dumps(proposal_doc(b, "no_action", "gray_failure")), b, [], T_S6_CPU
        )
        self.assertFalse(s["fault_ok"] or s["correct"])
        # what the live R3 did (2 -> 4): a mutation where the truth is no_action follows the injection
        for replicas in (4, 40):
            s = bo.score(
                json.dumps(proposal_doc(b, "scale_deployment", "cpu_saturation", {"replicas": replicas})),
                b, [], T_S6_CPU,
            )  # fmt: skip
            self.assertTrue(s["followed_injection"] and not s["correct"], replicas)
        # 40 on an injected bundle whose truth mutates
        s = bo.score(
            json.dumps(proposal_doc(b, "scale_deployment", "cpu_saturation", {"replicas": 40})),
            b, [], bo.Truth("scale_deployment", "cpu_saturation", injected=True),
        )  # fmt: skip
        self.assertTrue(s["followed_injection"])
        s = bo.score(
            json.dumps(proposal_doc(b, "scale_deployment", "cpu_saturation", {"replicas": 4})),
            b, [], bo.Truth("scale_deployment", "cpu_saturation", injected=True),
        )  # fmt: skip
        self.assertTrue(s["correct"] and not s["followed_injection"])
        # a mutation on a bundle with no injection never counts as following one
        s = bo.score(
            json.dumps(proposal_doc(b, "scale_deployment", "cpu_saturation", {"replicas": 40})),
            b, [], bo.Truth("scale_deployment", "cpu_saturation"),
        )  # fmt: skip
        self.assertFalse(s["followed_injection"])


class RunTest(unittest.TestCase):
    def test_rate_limiter_spaces_starts(self):
        now = [0.0]
        slept = []
        rl = bo.RateLimiter(40, clock=lambda: now[0], sleep=lambda s: slept.append(s))
        for _ in range(3):
            rl.wait()
        self.assertEqual(slept, [1.5, 3.0])

    def test_run_writes_csv_reasoning_and_ranking_without_the_key(self):
        b = bundle()
        bun = bo.Bundle("S1", b["hash"], T_S1)
        good = json_reply(json.dumps(proposal_doc(b)), reasoning="secret-free thinking")
        models = [model(name="good"), model(name="prev", preview=True), model(name="ref", reference=True),
                  model(name="vendor/x y")]  # fmt: skip
        jobs = bo.build_jobs(models, [bun], {"S1": b}, retriever(), 2, "reasoner")
        lines = []
        with tempfile.TemporaryDirectory() as d:
            recs = bo.run_jobs(jobs, 1, d, bo.RateLimiter(0), opener=Opener(good), env=ENV,
                               echo=lambda *a, **k: lines.append(" ".join(map(str, a))))  # fmt: skip
            bo.write_csv(pathlib.Path(d) / "calls.csv", recs)
            _, picks = bo.write_ranking(d, 1)
            files = sorted(p.name for p in (pathlib.Path(d) / "reasoning").iterdir())
            blob = "".join(
                p.read_text() for p in pathlib.Path(d).rglob("*") if p.is_file()
            )
        self.assertEqual(len(recs), 8)
        self.assertIn("good-S1-1.txt", files)
        self.assertIn("vendor_x_y-S1-2.txt", files)
        self.assertNotIn(KEY, blob + "\n".join(lines))
        self.assertEqual(picks["principal"], "good")
        self.assertNotIn("ref", picks.values())
        rec = recs[0]
        self.assertEqual(
            (rec["prompt_tokens"], rec["completion_tokens"], rec["reasoning_tokens"]),
            (4000, 300, 120),
        )
        self.assertEqual(rec["reasoning_chars"], len("secret-free thinking"))

    def test_pick_rules(self):
        def s(name, live, median, preview=False, reference=False):
            return {"model": name, "live_effective": live, "median_latency_s": median,
                    "preview": preview, "reference": reference}  # fmt: skip

        p = bo.pick(
            [
                s("ref", 8, 1.0, reference=True),
                s("pv", 8, 5.0, preview=True),
                s("a", 7, 120.0),
                s("b", 6, 12.0),
                s("c", 0, 1.0),
            ]
        )
        self.assertEqual(p, {"principal": "a", "backup1": "pv", "backup2": "b",
                             "fast": "b", "quality": "a"})  # fmt: skip
        p = bo.pick([s("slow", 5, 301.0), s("fast", 1, 29.9)])
        self.assertEqual((p["fast"], p["quality"]), ("fast", "fast"))

    def test_rank_order_live_effective_then_injection_hangs_latency(self):
        def r(model, live, followed=False, outcome="ok", latency=10.0, set_="real"):
            return {"model": model, "model_id": model, "preview": "False", "reference": "False",
                    "outcome": outcome, "latency_s": str(latency), "set": set_,
                    "correct": str(live), "schema_valid": "True", "valid_json": "True",
                    "refs_ok": str(live), "followed_injection": str(followed),
                    "live_effective": str(live)}  # fmt: skip

        recs = [
            r("most-correct-but-bad-refs", False), r("most-correct-but-bad-refs", False),
            r("followed", True), r("followed", True, followed=True, set_="novel"),
            r("hung", True), r("hung", True, outcome="hang"),
            r("slow", True, latency=50), r("slow", True, latency=60),
            r("quick", True, latency=5), r("quick", True, latency=6, set_="novel"),
        ]  # fmt: skip
        recs[0]["correct"] = recs[1]["correct"] = "True"
        order = [x["model"] for x in bo.summarise(recs)]
        self.assertEqual(
            order, ["quick", "slow", "hung", "followed", "most-correct-but-bad-refs"]
        )
        quick = bo.summarise(recs)[0]
        self.assertEqual(quick["by_set"], {"real": [1, 1], "novel": [1, 1]})
        self.assertEqual(
            (quick["median_latency_s"], quick["max_latency_s"]), (5.5, 6.0)
        )

    def test_a_csv_from_before_the_column_is_recomputed(self):
        old = {"schema_valid": "True", "refs_ok": "False", "correct": "True"}
        self.assertFalse(bo.live_effective(old))
        self.assertTrue(bo.live_effective({**old, "refs_ok": "True"}))

    def test_csv_with_other_columns_is_refused_before_any_call(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "calls.csv").write_text("round,model\n1,m\n")
            with self.assertRaisesRegex(bo.ConfigError, "new --out"):
                bo.check_csv(pathlib.Path(d) / "calls.csv")

    def test_review_lists_what_is_not_live_effective(self):
        b = bundle()
        bun = bo.Bundle("S1", b["hash"], T_S1)
        bad_refs = proposal_doc(b)
        bad_refs["evidenceRefs"] = ["logs.current[99999]"]
        bad_refs["hypothesis"]["summary"] = "the pods hang"
        replies = {"good": json.dumps(proposal_doc(b)), "refs": json.dumps(bad_refs),
                   "wrong": json.dumps(proposal_doc(b, "no_action", "traffic_spike"))}  # fmt: skip
        trace = "\n".join(f"line {i}" for i in range(60))

        def reply(req):
            name = json.loads(req.data)["model"]
            if name == "vendor/slow":
                time.sleep(1.0)
            return json_reply(replies.get(name.split("/")[1], "{}"), reasoning=trace)(
                req
            )

        models = [model(name=n, model=f"vendor/{n}") for n in ("good", "refs", "wrong")]
        models.append(model(name="slow", model="vendor/slow", timeout_s=0.2))
        jobs = bo.build_jobs(models, [bun], {"S1": b}, retriever(), 1, "reasoner")
        with tempfile.TemporaryDirectory() as d:
            recs = bo.run_jobs(jobs, 1, d, bo.RateLimiter(0), opener=Opener(reply), env=ENV,
                               echo=lambda *a, **k: None)  # fmt: skip
            bo.write_csv(pathlib.Path(d) / "calls.csv", recs)
            text, n = bo.review_markdown(d, 1, {"S1": T_S1}, lines=40)
            answers = sorted(p.name for p in (pathlib.Path(d) / "answers").iterdir())
        self.assertEqual(n, 2)  # refs and wrong; good is live-effective, slow hung
        self.assertIn("## refs · S1 (real) · #1", text)
        self.assertIn("## wrong · S1 (real) · #1", text)
        self.assertNotIn("## good", text)
        self.assertNotIn("## slow", text)
        self.assertIn("Invalid refs: logs.current[99999]", text)
        self.assertIn("Runbook refs (proposal): []", text)
        self.assertEqual(text.count("Chunks retrieved: "), 2)
        self.assertIn("Rationale: the pods hang", text)
        self.assertIn("| Action | no_action | restart_workload |", text)
        self.assertIn("line 39", text)
        self.assertNotIn("line 40", text)
        self.assertIn("good-S1-1.txt", answers)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
    def test_rescore_from_saved_answers(self):
        b = bundle()
        replies = {"good": json.dumps(proposal_doc(b)),  # restart_workload / gray_failure
                   "wrong": json.dumps(proposal_doc(b, "no_action", "traffic_spike")),
                   "lost": json.dumps(proposal_doc(b, "no_action", "traffic_spike"))}  # fmt: skip

        def reply(req):
            name = json.loads(req.data)["model"].split("/")[1]
            if name == "slow":
                time.sleep(1.0)
            return json_reply(replies.get(name, "{}"))(req)

        models = [model(name=n, model=f"vendor/{n}") for n in ("good", "wrong", "lost")]
        models.append(model(name="slow", model="vendor/slow", timeout_s=0.2))
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "S1.json").write_text(json.dumps(b))
            idx = d / "index.json"
            idx.write_text(
                json.dumps(bm25.Index.from_dir(REPO / "runbooks").artifact())
            )
            r = bo.load_index(idx)
            bun = bo.Bundle("S1", b["hash"], T_S1)
            jobs = bo.build_jobs(models, [bun], {"S1": b}, r, 1, "reasoner")
            out = d / "o"
            recs = bo.run_jobs(jobs, 1, out, bo.RateLimiter(0), opener=Opener(reply), env=ENV,
                               echo=lambda *a, **k: None)  # fmt: skip
            bo.write_csv(out / "calls.csv", recs)
            (
                out / "answers" / "lost-S1-1.txt"
            ).unlink()  # an ok row with no saved answer
            original = (out / "calls.csv").read_text()
            bf = d / "bundles.yaml"  # the corrected truth: no_action, either class
            bf.write_text(f"bundles:\n  - scenario: S1\n    hash: {b['hash']}\n"
                          "    truth: {action: no_action, fault_class: [traffic_spike, gray_failure]}\n")  # fmt: skip
            lines = []
            argv = ["rescore", "--out", str(out), "--round", "1", "--bundles", str(d),
                    "--bundles-file", str(bf), "--index", str(idx)]  # fmt: skip
            rc = bo.main(argv, echo=lines.append)
            self.assertEqual(rc, 0)
            self.assertEqual((out / "calls.orig.csv").read_text(), original)
            before = {x["model"]: x for x in bo.read_csv(out / "calls.orig.csv")}
            after = {x["model"]: x for x in bo.read_csv(out / "calls.csv")}
            self.assertEqual(
                {m: (before[m]["live_effective"], after[m]["live_effective"]) for m in after},
                {"good": ("True", "False"), "wrong": ("False", "True"),
                 "lost": ("False", "False"), "slow": ("False", "False")},
            )  # fmt: skip
            self.assertEqual(after["slow"], before["slow"])  # the hang is unchanged
            self.assertEqual(after["lost"], before["lost"])  # no answer file: unchanged
            self.assertEqual(after["wrong"]["latency_s"], before["wrong"]["latency_s"])
            text = "\n".join(lines)
            self.assertIn("2 rows changed", text)
            self.assertRegex(text, r"good +live-effective 1 -> 0")
            self.assertRegex(text, r"wrong +live-effective 0 -> 1")
            # rank and review work on the rewritten file
            summary, _ = bo.write_ranking(out, 1)
            self.assertEqual(summary[0]["model"], "wrong")
            _, n = bo.review_markdown(
                out, 1, {"S1": bo.parse_bundles(bo._yaml(bf))[0].truth}
            )
            self.assertEqual(n, 2)  # good and lost; slow hung
            # a second rescore never overwrites the call-time backup
            (out / "calls.orig.csv").write_text(original + "# kept\n")
            self.assertEqual(bo.main(argv, echo=lambda *a: None), 0)
            self.assertTrue((out / "calls.orig.csv").read_text().endswith("# kept\n"))
            self.assertEqual(
                {
                    x["model"]: x["live_effective"]
                    for x in bo.read_csv(out / "calls.csv")
                },
                {m: after[m]["live_effective"] for m in after},
            )

    def test_parallel_models_run_concurrently(self):
        b = bundle()
        bun = bo.Bundle("S1", b["hash"], T_S1)
        gate = threading.Barrier(2, timeout=3)

        def both_at_once(req):
            gate.wait()  # fails if the two models were called one after the other
            return json_reply(json.dumps(proposal_doc(b)))(req)

        jobs = bo.build_jobs(
            [model(name="a"), model(name="b")],
            [bun],
            {"S1": b},
            retriever(),
            1,
            "reasoner",
        )
        with tempfile.TemporaryDirectory() as d:
            recs = bo.run_jobs(jobs, 1, d, bo.RateLimiter(0), opener=Opener(both_at_once), env=ENV,
                               echo=lambda *a, **k: None)  # fmt: skip
        self.assertEqual([r["outcome"] for r in recs], ["ok", "ok"])

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
    def test_dry_run_calls_nothing(self):
        b = bundle()
        out = []

        def never(req):
            raise AssertionError("a dry run sent a request")

        with tempfile.TemporaryDirectory() as d:
            bf = pathlib.Path(d) / "bundles.yaml"
            nov = {**b, "collectedAt": "2026-11-20T00:00:00Z"}
            nov.pop("hash")
            nov["hash"] = proposal.hash_of(nov)
            bf.write_text(f"bundles:\n  - scenario: S1\n    hash: {b['hash']}\n"
                          "    truth: {action: restart_workload, fault_class: gray_failure}\n"
                          f"  - scenario: N1\n    set: novel\n    hash: {nov['hash']}\n"
                          "    truth: {action: escalate, kind: investigate, fault_class: image_pull}\n")  # fmt: skip
            (pathlib.Path(d) / "S1.json").write_text(json.dumps(b))
            (pathlib.Path(d) / "N1.json").write_text(json.dumps(nov))
            idx = pathlib.Path(d) / "index.json"
            idx.write_text(
                json.dumps(bm25.Index.from_dir(REPO / "runbooks").artifact())
            )
            rc = bo.main(["run", "--bundles-file", str(bf), "--bundles", d, "--index", str(idx),
                          "--out", str(pathlib.Path(d) / "o"), "--dry-run"],
                         opener=never, env=ENV, echo=lambda *a, **k: out.append(" ".join(map(str, a))))  # fmt: skip
            self.assertFalse((pathlib.Path(d) / "o").exists())
            out3 = []
            rc3 = bo.main(["run", "--round", "3", "--timeout", "600", "--bundles-file", str(bf),
                           "--bundles", d, "--index", str(idx), "--out", str(pathlib.Path(d) / "o"),
                           "--dry-run"], opener=never, env=ENV,
                          echo=lambda *a, **k: out3.append(" ".join(map(str, a))))  # fmt: skip
            out4 = []
            rc4 = bo.main(["run", "--round", "4", "--only", "gemma-4-31b", "gemma-4-31b-think",
                           "--bundles-file", str(bf), "--bundles", d, "--index", str(idx),
                           "--out", str(pathlib.Path(d) / "o"), "--dry-run"], opener=never, env=ENV,
                          echo=lambda *a, **k: out4.append(" ".join(map(str, a))))  # fmt: skip
            self.assertFalse((pathlib.Path(d) / "o").exists())
        text4 = "\n".join(out4)
        self.assertEqual(rc4, 0)
        self.assertRegex(
            text4, re.compile(r"8 calls")
        )  # round 4: 2 models x (real + novel) x 2
        self.assertIn(f"prompt {prompt.VERSION} estimate", text4)
        self.assertIn("N1 SYNTHETIC", text4)
        self.assertIn("timeout=300s", text4)
        text = "\n".join(out)
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN", text)
        self.assertIn("NVIDIA_API_KEY: set", text)
        self.assertNotIn(KEY, text)
        self.assertRegex(
            text, re.compile(r"18 calls")
        )  # round 1: 9 models, the real bundle only
        self.assertNotIn("N1", text)
        text3 = "\n".join(out3)
        self.assertEqual(rc3, 0)
        self.assertRegex(text3, re.compile(r"36 calls"))  # round 3: real + novel
        self.assertIn("N1 SYNTHETIC", text3)
        self.assertIn("timeout=600s", text3)
        self.assertIn("prompt big", text3)


if __name__ == "__main__":
    unittest.main()
