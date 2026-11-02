"""Offline tests for reasoner_client.py: redaction, validation, timeout, no retry."""

import io
import json
import unittest
import urllib.error
from typing import ClassVar

from .. import reasoner_client as rc

GOOD = {
    "schema": "proposal/v1",
    "hypothesis": {"faultClass": "gray_failure", "summary": "x"},
    "evidenceRefs": ["metrics.zscores.latencyP95"], "runbookRefs": ["rb-gray-failure#2"],
    "action": {"id": "restart_workload", "target": {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"}, "parameters": {}},
    "expectedEffect": "e", "confidence": 0.8, "escalation": None, "abstain": False,
}  # fmt: skip


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return FakeResp(self.result.encode())


class RedactTest(unittest.TestCase):
    def test_scrubs_and_counts(self):
        b = {"logs": {"current": ["auth Bearer abcdefghijklmnop1234 from 10.1.2.3", "mail bob@example.com",
                                  "password=hunter2 key ghp_" + "a" * 24]}}  # fmt: skip
        r = rc.redact(b)
        text = json.dumps(r)
        for leak in (
            "abcdefghijklmnop1234",
            "10.1.2.3",
            "bob@example.com",
            "hunter2",
            "ghp_aaaa",
        ):
            self.assertNotIn(leak, text)
        c = r["redaction"]["counts"]
        self.assertEqual((c["email"], c["ip"], c["credential"]), (1, 1, 1))
        self.assertGreaterEqual(c["token"], 2)
        self.assertTrue(r["hash"].startswith("sha256:"))

    def test_idempotent_and_input_untouched(self):
        b = {"logs": {"current": ["mail bob@example.com"]}}
        once = rc.redact(b)
        self.assertEqual(rc.redact(once)["hash"], once["hash"])
        self.assertIn("bob@example.com", b["logs"]["current"][0])

    def test_private_key_block(self):
        r = rc.redact(
            {
                "x": "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----"
            }
        )
        self.assertNotIn("MIIB", json.dumps(r))
        self.assertEqual(r["redaction"]["counts"]["key"], 1)


class ValidateTest(unittest.TestCase):
    def test_good(self):
        self.assertEqual(rc.validate_proposal(GOOD), [])

    def test_each_rule(self):
        def bad(**kw):
            return rc.validate_proposal({**GOOD, **kw})

        self.assertTrue(bad(schema="proposal/v2"))
        self.assertTrue(bad(hypothesis={"faultClass": "ghosts"}))
        self.assertTrue(bad(action={"id": "delete_deployment"}))  # not representable
        self.assertTrue(
            bad(action={"id": "scale_deployment", "target": {"namespace": "n"}})
        )
        self.assertTrue(bad(abstain="no"))
        self.assertTrue(bad(confidence=1.5))
        self.assertTrue(bad(confidence=True))
        self.assertTrue(bad(runbookRefs="rb-x#1"))
        self.assertTrue(bad(action={"id": "escalate"}, escalation=None))
        self.assertEqual(bad(action={"id": "no_action"}), [])
        ok = bad(
            action={"id": "escalate"},
            escalation={
                "reason": "r",
                "recommendation": {"kind": "git_revert", "detail": "d"},
            },
        )
        self.assertEqual(ok, [])
        self.assertEqual(rc.validate_proposal([]), ["not an object"])


class ProposeTest(unittest.TestCase):
    CTX: ClassVar[dict] = {
        "incidentUid": "u",
        "level": "L3",
        "mode": "llm",
        "model": "m",
    }

    def test_ok_nested_and_flat(self):
        for doc in ({"proposal": GOOD, "metadata": {"indexHash": "h"}}, GOOD):
            op = Opener(json.dumps(doc))
            r = rc.propose(
                "http://reasoner:8080/",
                {"logs": {"current": ["a@b.co"]}},
                self.CTX,
                opener=op,
            )
            self.assertTrue(r.ok, r)
            self.assertEqual(r.proposal["action"]["id"], "restart_workload")
            self.assertEqual(len(op.calls), 1)
            req, timeout = op.calls[0]
            self.assertEqual(
                (req.full_url, timeout), ("http://reasoner:8080/v1/propose", 30)
            )
            self.assertNotIn("a@b.co", req.data.decode())  # redacted before it leaves

    def test_failures_one_attempt_each(self):
        cases = {
            "timeout": TimeoutError(),
            "transport": urllib.error.URLError("refused"),
            "http_422": urllib.error.HTTPError("u", 422, "x", {}, None),
            "http_503": urllib.error.HTTPError("u", 503, "x", {}, None),
        }
        for reason, exc in cases.items():
            op = Opener(exc)
            r = rc.propose("http://x", {}, self.CTX, opener=op)
            self.assertEqual(
                (r.ok, r.reason, len(op.calls)), (False, reason, 1), reason
            )

    def test_url_timeout_reason(self):
        r = rc.propose(
            "http://x",
            {},
            self.CTX,
            opener=Opener(urllib.error.URLError(TimeoutError())),
        )
        self.assertEqual(r.reason, "timeout")

    def test_unparseable_and_schema_invalid_keep_raw(self):
        r = rc.propose("http://x", {}, self.CTX, opener=Opener("not json"))
        self.assertEqual((r.reason, r.raw), ("unparseable", "not json"))
        raw = json.dumps({**GOOD, "action": {"id": "delete_deployment"}})
        r = rc.propose("http://x", {}, self.CTX, opener=Opener(raw))
        self.assertEqual((r.ok, r.reason, r.raw), (False, "schema_invalid", raw))
        self.assertTrue(r.errors)


if __name__ == "__main__":
    unittest.main()
