import json
import os
import threading
import unittest
import urllib.request

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

    def test_refusals(self):
        rs = reasoner(groq(good_proposal(self.b)))
        self.rejected(rs, 422, "unknown_model", model="evil/model")
        self.rejected(rs, 501, "mode_not_available", mode="adversarial")
        self.rejected(rs, 422, "invalid_request", mode="agentic")
        self.rejected(rs, 422, "invalid_request", level="L4")
        self.b["logs"]["current"].append("tampered")
        with self.assertRaises(service.Rejected) as cm:
            self.call(rs)
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
