import json
import os
import threading
import unittest
import urllib.request

from nexus_operator import proposal
from nexus_reasoner import service

from .helpers import FakeOpener, bundle, good_proposal, reasoner


def ctx(model="openai/gpt-oss-120b", mode="llm", **kw):
    return {
        "incidentUid": "u-1",
        "level": "L1",
        "catalogueVersion": "1",
        "mode": mode,
        "model": model,
        **kw,
    }


def groq(p):
    return FakeOpener(
        lambda url, body: {
            "choices": [
                {"message": {"content": p if isinstance(p, str) else json.dumps(p)}}
            ],
            "usage": {},
        }
    )


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["T_GROQ_KEY"], os.environ["T_ANT_KEY"] = "k1", "k2"
        self.b = bundle()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def call(self, rs, **kw):
        return rs.propose({"bundle": self.b, "context": ctx(**kw)})

    def rejected(self, rs, status, error, **kw):
        with self.assertRaises(service.Rejected) as cm:
            self.call(rs, **kw)
        self.assertEqual((cm.exception.status, cm.exception.error), (status, error))
        return cm.exception

    def test_healthz_carries_the_index_hash(self):
        rs = reasoner()
        self.assertEqual(rs.health()["corpusHash"], rs.retriever.index.corpus_hash)
        self.assertEqual(rs.health()["status"], "ready")

    def test_rules_mode_is_r1(self):
        out = self.call(reasoner(), mode="rules", model=None)
        self.assertEqual(out["proposal"]["schema"], "proposal/v1")
        self.assertIsNone(out["metadata"]["model"])
        self.assertEqual(
            out["metadata"]["corpusHash"], reasoner().health()["corpusHash"]
        )

    def test_llm_mode_records_model_chunks_and_hash(self):
        rs = reasoner(groq(good_proposal(self.b)))
        out = self.call(rs)
        m = out["metadata"]
        self.assertEqual(
            (m["arm"], m["model"], m["provider"]), ("R3", "openai/gpt-oss-120b", "groq")
        )
        self.assertEqual(m["promptVersion"], "prompt/v1")
        self.assertEqual(m["corpusHash"], rs.retriever.corpus_hash)
        self.assertTrue(m["retrievedChunks"])
        self.assertFalse(m["noRunbook"])

    def test_a_bundle_with_null_zscores_is_answered(self):
        # A real bundle carries null for a signal with no data (p95 once a hang lets no request
        # complete). The BM25 query used to raise on it, which the operator saw as a failed arm.
        z = self.b["metrics"]["zscores"]
        z["latencyP95"], z["errorRatio"] = None, None
        self.b.pop("hash")
        self.b["hash"] = proposal.hash_of(self.b)
        out = self.call(
            reasoner(groq(good_proposal(self.b, refs=("metrics.zscores.cpu",))))
        )
        self.assertEqual(out["proposal"]["action"]["id"], "restart_workload")
        self.assertEqual(
            self.call(reasoner(), mode="rules", model=None)["proposal"]["schema"],
            "proposal/v1",
        )

    def test_no_runbook_when_floor_is_high(self):
        rs = reasoner(groq(good_proposal(self.b)), floor=1e9)
        out = self.call(rs)
        self.assertTrue(out["metadata"]["noRunbook"])
        self.assertIn(
            "no_runbook", rs.opener.calls[0]["body"]["messages"][1]["content"]
        )

    def test_identical_prompt_across_arms_and_providers(self):
        sent = {}
        for model in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
            op = groq(good_proposal(self.b))
            self.call(reasoner(op), model=model)
            m = op.calls[0]["body"]["messages"]
            sent[model] = (m[0]["content"], m[1]["content"])
        op = FakeOpener(
            lambda u, b: {
                "content": [
                    {"type": "text", "text": json.dumps(good_proposal(self.b))}
                ],
                "usage": {},
            }
        )
        self.call(reasoner(op), model="claude-haiku-5-5")
        b = op.calls[0]["body"]
        sent["h1"] = (b["system"], b["messages"][0]["content"])
        op = FakeOpener(
            lambda u, b: {"message": {"content": json.dumps(good_proposal(self.b))}}
        )
        self.call(reasoner(op), model="llama-demo")
        m = op.calls[0]["body"]["messages"]
        sent["demo"] = (m[0]["content"], m[1]["content"])
        self.assertEqual(len(set(sent.values())), 1, "the prompt differs between arms")

    def test_422_after_the_model_answered_carries_raw_reason_and_metadata(self):
        """(a) The Flight Recorder stores the model's message as received and why it was rejected."""
        bad_doc = good_proposal(self.b)
        bad_doc["action"]["id"] = (
            "delete_deployment"  # shape is fine; the bad key below is not
        )
        bad_doc["unexpected"] = 1
        cases = {
            "unparseable": "Sure! here is my answer: {oops",
            "invalid_proposal": json.dumps(
                bad_doc, indent=2
            ),  # whitespace must survive verbatim
            "invalid_refs": json.dumps(
                good_proposal(self.b, refs=["logs.current[99]"])
            ),
        }
        for error, text in cases.items():
            with self.assertRaises(service.Rejected) as cm:
                self.call(reasoner(groq(text)))
            r = cm.exception
            self.assertEqual((r.status, r.error), (422, error))
            self.assertEqual(
                r.extra["raw"], text, error
            )  # not truncated, not re-serialised
            self.assertTrue(r.detail)
            m = r.extra["metadata"]
            self.assertEqual((m["arm"], m["model"]), ("R3", "openai/gpt-oss-120b"))
            self.assertIn("usage", m)
            self.assertTrue(m["retrievedChunks"])

    def test_422_body_over_http_and_through_the_harness_client(self):
        from experiments.harness import reasoner_client as rc

        text = json.dumps({**good_proposal(self.b), "abstain": "maybe"})
        rs = reasoner(groq(text))
        object.__setattr__(rs.cfg, "host", "127.0.0.1")
        object.__setattr__(rs.cfg, "port", 0)
        srv = service.serve(rs)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = rc.propose(f"http://127.0.0.1:{srv.server_address[1]}", self.b, ctx())
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual((r.ok, r.reason), (False, "http_422"))
        self.assertEqual(r.raw, text)
        self.assertIn("abstain", r.errors[0])
        self.assertEqual(r.meta["rejection"], "invalid_proposal")
        self.assertEqual(r.meta["model"], "openai/gpt-oss-120b")

    def test_a_422_before_the_model_has_no_raw(self):
        e = self.rejected(reasoner(groq({})), 422, "unknown_model", model="evil/model")
        self.assertNotIn("raw", e.extra)

    def test_refusals(self):
        rs = reasoner(groq(good_proposal(self.b)))
        self.rejected(rs, 422, "unknown_model", model="evil/model")
        self.rejected(rs, 422, "invalid_request", mode="agentic")
        self.rejected(rs, 422, "invalid_request", level="L4")
        self.b["logs"]["current"].append("tampered")
        with self.assertRaises(service.Rejected) as cm:
            self.call(rs)
        self.assertEqual(cm.exception.error, "invalid_bundle")

    def test_bundle_schemas(self):
        """evidence/v1.1 (the fixture) and v1 pass; anything else is invalid_bundle (ADR-039)."""
        self.assertEqual(self.b["schema"], "evidence/v1.1")
        for schema, ok in (
            ("evidence/v1", True),
            ("evidence/v1.1", True),
            ("evidence/v2", False),
        ):
            b = {k: v for k, v in self.b.items() if k != "hash"} | {"schema": schema}
            b["hash"] = proposal.hash_of(b)
            if ok:
                service.check_bundle(b)
                continue
            with self.assertRaises(service.Rejected) as cm:
                service.check_bundle(b)
            self.assertEqual(cm.exception.error, "invalid_bundle")

    def test_model_output_checks(self):
        self.rejected(reasoner(groq("not json")), 422, "unparseable")
        bad = good_proposal(self.b)
        bad["action"]["id"] = "delete_deployment"
        bad["unexpected"] = 1
        self.rejected(reasoner(groq(bad)), 422, "invalid_proposal")
        self.rejected(
            reasoner(groq(good_proposal(self.b, refs=["logs.current[99]"]))),
            422,
            "invalid_refs",
        )
        self.rejected(
            reasoner(
                groq(good_proposal(self.b, runbooks=["rb-gray-failure#2"])), floor=1e9
            ),
            422,
            "invalid_refs",
        )

    def test_provider_failures_map_to_5xx(self):
        os.environ.pop("T_GROQ_KEY")
        self.rejected(reasoner(groq({})), 503, "model_unavailable")
        os.environ["T_GROQ_KEY"] = "k"
        self.rejected(
            reasoner(FakeOpener(exc=TimeoutError())), 504, "model_unavailable"
        )
        self.rejected(reasoner(FakeOpener(exc=OSError("x"))), 502, "model_unavailable")
        self.rejected(reasoner(groq({})), 503, "model_unavailable", model="no-url")

    def test_resolves(self):
        self.assertTrue(service.resolves(self.b, "metrics.zscores.latencyP95"))
        self.assertTrue(service.resolves(self.b, "logs.current[0]"))
        self.assertFalse(service.resolves(self.b, "logs.current[9]"))
        self.assertFalse(service.resolves(self.b, "metrics.zscores.nope"))
        self.assertFalse(service.resolves(self.b, "../etc"))

    def test_http_endpoints(self):
        rs = reasoner(groq(good_proposal(self.b)))
        object.__setattr__(rs.cfg, "host", "127.0.0.1")
        object.__setattr__(rs.cfg, "port", 0)
        srv = service.serve(rs)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            self.assertEqual(
                json.load(urllib.request.urlopen(base + "/healthz"))["corpusHash"],
                rs.retriever.corpus_hash,
            )
            req = urllib.request.Request(
                base + "/v1/propose",
                data=json.dumps({"bundle": self.b, "context": ctx()}).encode(),
                method="POST",
            )
            self.assertEqual(
                json.load(urllib.request.urlopen(req))["proposal"]["action"]["id"],
                "restart_workload",
            )
            bad = urllib.request.Request(base + "/v1/propose", data=b"{", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(bad)
            self.assertEqual(cm.exception.code, 422)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/nope")
            self.assertEqual(cm.exception.code, 404)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
