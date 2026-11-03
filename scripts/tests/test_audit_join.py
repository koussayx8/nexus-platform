"""Tests for scripts/audit-join.py (G5). Synthetic audit events shaped by the k8s audit.k8s.io/v1
Event and rows shaped by the `actions` table (operator/k8s/flight-recorder-db/05-schema.sql);
no cluster. Live run against a real audit log: live check pending."""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("aj", ROOT / "scripts/audit-join.py")
aj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aj)

OP = aj.OPERATOR


def event(aid, *, sub="scale", res="deployments", verb="patch", code=200, user=OP,
          uri="/apis/apps/v1/namespaces/nexus-dev/deployments/sample-api/scale",
          when="2026-11-20T10:00:05.123456Z", stage="ResponseComplete"):
    return {"auditID": aid, "stage": stage, "verb": verb, "user": {"username": user},
            "objectRef": {"resource": res, "subresource": sub, "namespace": "nexus-dev", "name": "sample-api"},
            "responseStatus": {"code": code}, "requestURI": uri, "stageTimestamp": when}


def row(key, aid, status=200):
    return {"idempotency_key": key, "audit_id": aid, "http_status": status, "intent_at": "2026-11-20T10:00:04Z"}


class Join(unittest.TestCase):
    def verdicts(self, actions, events, **kw):
        r = aj.join(actions, events, **kw)
        return r, [(v["verdict"]) for v in r["verdicts"]]

    def test_exact_match_is_complete(self):
        r, v = self.verdicts([row("k1", "a1")], [event("a1")])
        self.assertEqual((v, r["completeness"], r["ok"]), (["matched"], 1.0, True))

    def test_unexplained_request_invalidates_the_run(self):
        r, v = self.verdicts([row("k1", "a1")], [event("a1"), event("a2")])
        self.assertEqual(sorted(v), ["matched", "unexplained"])
        self.assertEqual((r["completeness"], r["ok"]), (0.5, False))

    def test_status_mismatch(self):
        r, v = self.verdicts([row("k1", "a1", 200)], [event("a1", code=403)])
        self.assertEqual((v, r["ok"]), (["status_mismatch"], False))

    def test_row_without_event_is_missing_event(self):
        _, v = self.verdicts([row("k1", "a1")], [])
        self.assertEqual(v, ["missing_event"])

    def test_row_without_audit_id_is_pending_not_unexplained(self):
        r, v = self.verdicts([row("k1", None)], [])
        self.assertEqual((v, r["ok"]), (["pending"], False))

    def test_dry_run_is_counted_and_not_joined(self):
        dry = event("d1", uri="/apis/apps/v1/namespaces/nexus-dev/deployments/sample-api/scale?dryRun=All")
        r, v = self.verdicts([row("k1", "a1")], [event("a1"), dry])
        self.assertEqual((v, r["dry_run_events_ignored"], r["ok"]), (["matched"], 1, True))

    def test_other_identities_resources_and_stages_are_ignored(self):
        events = [event("a1"),
                  event("x1", user="system:admin"),
                  event("x2", res="incidents", sub="status"),
                  event("x3", verb="get"),
                  event("x4", stage="ResponseStarted")]
        r, v = self.verdicts([row("k1", "a1")], events)
        self.assertEqual((v, r["operator_mutation_events"]), (["matched"], 1))

    def test_eviction_is_an_operator_mutation(self):
        e = event("e1", res="pods", sub="eviction", verb="create", code=201)
        _, v = self.verdicts([row("k1", "e1", 201)], [e])
        self.assertEqual(v, ["matched"])

    def test_window_excludes_events_outside_it(self):
        since = aj.ts("2026-11-20T10:00:00Z")
        until = aj.ts("2026-11-20T10:01:00Z")
        old = event("o1", when="2026-11-20T09:00:00Z")
        r, v = self.verdicts([row("k1", "a1")], [event("a1"), old], since=since, until=until)
        self.assertEqual((v, r["ok"]), (["matched"], True))

    def test_no_events_and_no_rows_is_ok(self):
        r, _ = self.verdicts([], [])
        self.assertTrue(r["ok"])


class Cli(unittest.TestCase):
    def run_cli(self, actions, events, *extra):
        with tempfile.TemporaryDirectory() as d:
            a, e = pathlib.Path(d, "a.jsonl"), pathlib.Path(d, "e.log")
            a.write_text("\n".join(json.dumps(x) for x in actions) + "\n")
            e.write_text("\n".join(json.dumps(x) for x in events) + "\n")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = aj.main(["--actions", str(a), "--audit", str(e), *extra])
            return code, out.getvalue(), err.getvalue(), d

    def test_exit_codes(self):
        self.assertEqual(self.run_cli([row("k1", "a1")], [event("a1")])[0], 0)
        self.assertEqual(self.run_cli([row("k1", "a1")], [event("a1"), event("a2")])[0], 1)

    def test_unreadable_input_is_exit_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(aj.main(["--actions", "/nonexistent", "--audit", "/nonexistent"]), 2)

    def test_links_file_carries_audit_links_columns(self):
        with tempfile.TemporaryDirectory() as d:
            links = str(pathlib.Path(d, "links.jsonl"))
            code, *_ = self.run_cli([row("k1", "a1")], [event("a1"), event("a2")], "--links", links)
            self.assertEqual(code, 1)
            rows = [json.loads(x) for x in pathlib.Path(links).read_text().splitlines()]
        self.assertEqual({(r["audit_id"], r["matched_action"], r["verdict"]) for r in rows},
                         {("a1", "k1", "matched"), ("a2", None, "unexplained")})


if __name__ == "__main__":
    unittest.main()
