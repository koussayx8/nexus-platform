"""Offline tests for reasoner_client.py. The redacted bundle is built exactly as the operator's
act.py builds it (redact_bundle, then counts, then hash_of), so the client is tested against the
real Redactor and the real proposal/v1 validator, not copies of them."""

import io
import json
import pathlib
import unittest
import urllib.error
from typing import ClassVar
from unittest import mock

from .. import reasoner_client as rc
from ..operator_lib import proposal, redact

RAW = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "evidence-v1.1-raw.json").read_text()
)


def operator_redacted(raw: dict) -> dict:
    """What act.py does between the Evidence Collector and the Reasoner Client."""
    redacted, counts = redact.redact_bundle(raw)
    bundle = redacted | {"redaction": {"counts": counts}}
    bundle["hash"] = proposal.hash_of(bundle)
    return bundle


GOOD = {
    "schema": "proposal/v1",
    "hypothesis": {"faultClass": "gray_failure", "summary": "x"},
    "evidenceRefs": ["metrics.zscores.latencyP95"],
    "runbookRefs": ["rb-gray-failure#2"],
    "action": {"id": "restart_workload", "target": {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"}, "parameters": {}},
    "expectedEffect": "e", "confidence": 0.8, "escalation": None, "abstain": False,
}  # fmt: skip
CTX = {"incidentUid": "u-1", "level": "L3", "mode": "llm", "model": "m"}


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


class CheckRedactedTest(unittest.TestCase):
    def test_operator_bundle_passes_and_secrets_are_gone(self):
        b = operator_redacted(RAW)
        self.assertIsNone(rc.check_redacted(b))
        text = json.dumps(b)
        for leak in ("10.42.0.17", "ops@example.com", "hunter2go"):
            self.assertNotIn(leak, text)
        self.assertGreaterEqual(b["redaction"]["counts"]["ip"], 1)

    def test_refuses_what_the_operator_did_not_redact(self):
        self.assertIn("redaction.counts", rc.check_redacted(RAW))
        b = operator_redacted(RAW)
        tampered = json.loads(json.dumps(b))
        tampered["logs"]["current"][0] = "still has 10.9.9.9"
        self.assertIn("hash", rc.check_redacted(tampered))
        self.assertIn("evidence/v1", rc.check_redacted({"schema": "other"}))

    def test_v1_and_v1_1_bundles_pass_and_v2_does_not(self):
        """evidence/v1.1 only adds metrics.zscores.inflight (ADR-039)."""
        b = operator_redacted(RAW)
        self.assertEqual(b["schema"], "evidence/v1.1")
        self.assertIn("inflight", b["metrics"]["zscores"])
        for schema, ok in (
            ("evidence/v1", True),
            ("evidence/v1.1", True),
            ("evidence/v2", False),
        ):
            raw = {k: v for k, v in b.items() if k != "hash"} | {"schema": schema}
            raw["hash"] = rc.proposal.hash_of(raw)
            self.assertEqual(rc.check_redacted(raw) is None, ok, schema)


class ProposeTest(unittest.TestCase):
    BUNDLE: ClassVar[dict] = operator_redacted(RAW)

    def test_ok_nested_and_flat_one_call_30s(self):
        for doc in ({"proposal": GOOD, "metadata": {"indexHash": "h"}}, GOOD):
            op = Opener(json.dumps(doc))
            r = rc.propose("http://reasoner:8080/", self.BUNDLE, CTX, opener=op)
            self.assertTrue(r.ok, r)
            self.assertEqual(r.proposal["action"]["id"], "restart_workload")
            req, timeout = op.calls[0]
            self.assertEqual(
                (len(op.calls), req.full_url, timeout),
                (1, "http://reasoner:8080/v1/propose", 30),
            )
            self.assertEqual(
                json.loads(req.data)["bundle"], self.BUNDLE
            )  # sent as received, never re-scrubbed or altered

    def test_unredacted_bundle_is_never_sent(self):
        op = Opener(json.dumps(GOOD))
        r = rc.propose("http://x", RAW, CTX, opener=op)
        self.assertEqual((r.ok, r.reason, op.calls), (False, "not_redacted", []))

    def test_failures_one_attempt_each(self):
        cases = {
            "timeout": TimeoutError(),
            "transport": urllib.error.URLError("refused"),
            "http_422": urllib.error.HTTPError("u", 422, "x", {}, None),
            "http_503": urllib.error.HTTPError("u", 503, "x", {}, None),
        }
        for reason, exc in cases.items():
            op = Opener(exc)
            r = rc.propose("http://x", self.BUNDLE, CTX, opener=op)
            self.assertEqual(
                (r.ok, r.reason, len(op.calls)), (False, reason, 1), reason
            )
        r = rc.propose(
            "http://x",
            self.BUNDLE,
            CTX,
            opener=Opener(urllib.error.URLError(TimeoutError())),
        )
        self.assertEqual(r.reason, "timeout")

    def test_422_body_keeps_raw_output_and_reason(self):
        body = {
            "error": "invalid_proposal",
            "detail": "action.id must be a string",
            "raw": '{"schema": "proposal/v1", "oops": 1}',
            "metadata": {"model": "openai/gpt-oss-20b", "corpusHash": "c" * 64},
        }
        err = urllib.error.HTTPError(
            "u", 422, "x", {}, io.BytesIO(json.dumps(body).encode())
        )
        r = rc.propose("http://x", self.BUNDLE, CTX, opener=Opener(err))
        self.assertEqual((r.ok, r.reason), (False, "http_422"))
        self.assertEqual(r.raw, body["raw"])
        self.assertEqual(r.errors, [body["detail"]])
        self.assertEqual(r.meta["rejection"], "invalid_proposal")
        self.assertEqual(r.meta["model"], "openai/gpt-oss-20b")

    def test_422_without_a_body_is_plain(self):
        err = urllib.error.HTTPError("u", 422, "x", {}, io.BytesIO(b"<html>"))
        r = rc.propose("http://x", self.BUNDLE, CTX, opener=Opener(err))
        self.assertEqual((r.reason, r.raw), ("http_422", None))

    def test_untrusted_output_rejected_raw_kept(self):
        r = rc.propose("http://x", self.BUNDLE, CTX, opener=Opener("not json"))
        self.assertEqual((r.reason, r.raw), ("unparseable", "not json"))
        for bad, reason in (
            ({**GOOD, "schema": "proposal/v2"}, "schema_invalid"),
            (
                {**GOOD, "extra": 1},
                "schema_invalid",
            ),  # the operator's validator is strict
            ({**GOOD, "confidence": "high"}, "schema_invalid"),
            (
                {**GOOD, "action": {**GOOD["action"], "id": "delete_deployment"}},
                "not_in_catalogue",
            ),
        ):
            raw = json.dumps(bad)
            r = rc.propose("http://x", self.BUNDLE, CTX, opener=Opener(raw))
            self.assertEqual((r.ok, r.reason, r.raw), (False, reason, raw))

    def test_fallback_to_r1_once_then_blocked(self):
        op = Opener(TimeoutError())
        r = rc.propose_with_fallback("http://x", self.BUNDLE, CTX, opener=op)
        self.assertEqual(len(op.calls), 1)  # the reasoner is not retried
        self.assertTrue(r.ok and r.fallback_used)
        self.assertEqual(r.meta["primary_failure"], "timeout")
        self.assertEqual(proposal.validate(r.proposal), [])
        self.assertEqual(
            r.proposal["action"]["id"], "restart_workload"
        )  # R1 rule 4: p95 Z 5.1, CPU Z 0.4, all Ready
        # R1 itself producing invalid output is Blocked, not retried (R1 is always valid today)
        with mock.patch.object(rc.rules, "propose", return_value={"schema": "nope"}):
            r = rc.propose_with_fallback(
                "http://x", self.BUNDLE, CTX, opener=Opener(TimeoutError())
            )
        self.assertTrue(r.blocked and not r.ok and r.fallback_used)

    def test_not_redacted_does_not_fall_back(self):
        r = rc.propose_with_fallback(
            "http://x", RAW, CTX, opener=Opener(json.dumps(GOOD))
        )
        self.assertEqual((r.reason, r.fallback_used), ("not_redacted", False))


if __name__ == "__main__":
    unittest.main()
