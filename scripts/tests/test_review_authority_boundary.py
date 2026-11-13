"""Expected-failure tests for docs/reviews/2026-11-10-authority-boundary.md (reconciliation).

Each test states the behaviour the review argues for and fails today. unittest reports an
unexpected success as a failure, so the decorator is removed in the PR that fixes the finding.
Offline: synthetic audit events and `actions` rows, no cluster.
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), ROOT / "scripts" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RECON, AJ = script("reconcile-audit"), script("audit-join")
RUN = {
    "run_id": "r",
    "level": 3,
    "started_at": "2027-01-05T10:00:00+00:00",
    "ended_at": "2027-01-05T10:20:00+00:00",
    "config_snapshot": {},
}


def scale_event(aid, replicas, when="2027-01-05T10:05:00+00:00"):
    return {
        "auditID": aid,
        "stage": "ResponseComplete",
        "verb": "patch",
        "user": {"username": AJ.OPERATOR},
        "objectRef": {
            "resource": "deployments",
            "subresource": "scale",
            "namespace": "nexus-dev",
            "name": "sample-api",
        },
        "responseStatus": {"code": 200},
        "requestURI": "/apis/apps/v1/namespaces/nexus-dev/deployments/sample-api/scale",
        "requestObject": {"spec": {"replicas": replicas}},
        "stageTimestamp": when,
    }


# The row as _settle_unconfirmed leaves it: completed, with no audit id.
ROW = {
    "idempotency_key": "inc-1",
    "incident_uid": "u",
    "audit_id": None,
    "http_status": None,
    "result": "applied_unconfirmed",
    "previous_state": {"replicas": 2},
    "intent_at": "2027-01-05T10:04:59+00:00",
}


class CrashRecovery(unittest.TestCase):
    """A12. The operator died between the request and the completion write. On resume it saw the
    change in the cluster and closed the row as `applied_unconfirmed` with no audit id
    (act.py, _settle_unconfirmed). Spec section 17: reconciliation flags the incomplete row, and
    it is "not an unexplained request"."""

    @unittest.expectedFailure
    def test_a_crash_recovered_action_is_not_an_unexplained_request(self):
        out = RECON.reconcile(RUN, {"actions": [ROW]}, [scale_event("a1", 3)])
        self.assertEqual(out["unexplained"], [])

    @unittest.expectedFailure
    def test_a_crash_recovered_action_is_not_a_containment_violation(self):
        out = RECON.reconcile(RUN, {"actions": [ROW]}, [scale_event("a1", 3)])
        self.assertEqual(out["containment_violations"], 0)

    def test_it_still_keeps_the_run_from_counting(self):
        # What must NOT change with a fix: an incomplete row is flagged and the run does not count.
        out = RECON.reconcile(RUN, {"actions": [ROW]}, [scale_event("a1", 3)])
        self.assertFalse(out["counts"])


if __name__ == "__main__":
    unittest.main()
