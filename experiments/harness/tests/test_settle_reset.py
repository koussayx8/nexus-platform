"""The settle before the next reset (protocol #234 §5, 2026-11-21). Offline: the fakes of
test_batch.py, no cluster."""

import json

from .. import batch
from . import test_batch as tb


class LaterIncident(tb.FakeCluster):
    """After the injection a second Incident appears on nexus-dev (30 s after the run's alert). It
    is Verifying until the fake clock reaches `verify_until` (None: for ever), then Resolved. Every
    call's fake time is kept, to see when the next run's reset started."""

    def __init__(self, ft, verify_until=None, **kw):
        super().__init__(ft, **kw)
        self.verify_until, self.times = verify_until, []

    def __call__(self, argv, cwd=None):
        self.times.append((self.ft.t, argv))
        rc, out = super().__call__(argv, cwd)
        if "incidents" in argv and self.injected:
            doc = json.loads(out)
            later = batch.model.parse_time(self.detected_at).timestamp() + 30
            done = self.verify_until is not None and self.ft.t >= self.verify_until
            doc["items"].append({
                "metadata": {"uid": "later", "name": "inc-later", "creationTimestamp": batch._utc(later)},
                "spec": {"target": {"namespace": "nexus-dev", "name": "sample-api"},
                         "detectedAt": batch._utc(later)},
                "status": {"phase": "Resolved" if done else "Verifying"},
            })  # fmt: skip
            out = json.dumps(doc)
        return rc, out

    def resets(self):
        """Fake times of the reset's nexus-operator-config patch: one per reset."""
        return [
            t for t, a in self.times if "nexus-operator-config" in a and "patch" in a
        ]

    def injections(self):
        return [t for t, a in self.times if any("/fault/" in x for x in a)]


class SettleTest(tb.Base):
    def setUp(self):
        super().setUp()
        self.ft = tb.FakeTime(self.clock)

    def live(self, cluster, **kw):
        return batch.LiveRun(tb.runner_for(cluster, self.ft), tb.PRE, wait_s=50, poll_s=5,
                             sleep=self.ft.sleep, monotonic=self.ft.monotonic, wall=self.ft.wall,
                             stat=tb.audit_fresh(self.ft), **kw)  # fmt: skip

    def test_a_later_incident_still_verifying_holds_the_next_reset(self):
        # the later Incident ends 400 s into the fake time, well after the run's own Incident
        c = LaterIncident(self.ft, verify_until=400.0)
        cells = [dict(tb.CELLS[0]), dict(tb.CELLS[0], cell="C1/S1/L3/b")]
        self.q = batch.Queue.create(
            self.dir / "two", tb.REF, cells, precheck_cfg=tb.PRE
        )
        out = self.drive(self.live(c), max_runs=2)
        self.assertEqual(out["ran"], 2)
        first, second = self.q.runs[0], self.q.runs[1]
        self.assertEqual((first["status"], first["terminal"]), ("valid", "Resolved"))
        self.assertTrue(first["settle"]["settled"])
        self.assertEqual(first["settle"]["open"], [])
        self.assertEqual([(i["uid"], i["phase"]) for i in first["settle"]["incidents"]],
                         [("later", "Resolved"), ("new", "Resolved")])  # fmt: skip
        # the second run's reset started only once the later Incident had been terminal for 60 s
        resets = c.resets()
        self.assertEqual(len(resets), 2)
        self.assertGreaterEqual(resets[1], 400.0 + batch.SETTLE_S)
        # and while it was Verifying, no reset ran
        self.assertFalse([t for t in resets if c.injections()[0] < t < 400.0])
        # the next cell's run started after the hold (the fake reuses the Incident uid, so what
        # that run then counts as is not this test's concern)
        self.assertEqual(second["cell"], "C1/S1/L3/b")

    def test_all_terminal_for_60_s_releases_the_reset(self):
        c = LaterIncident(
            self.ft, verify_until=0.0
        )  # the later Incident is Resolved at once
        self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Resolved"))
        s = r["settle"]
        self.assertTrue(s["settled"])
        # released on the first poll at or past 60 s of everything terminal, not before
        self.assertGreaterEqual(s["waited_s"], batch.SETTLE_S)
        self.assertLess(s["waited_s"], batch.SETTLE_S + 5 + 1)

    def test_a_new_incident_restarts_the_60_s(self):
        class Appears(LaterIncident):
            """The later Incident is already terminal, but only appears 30 s into the settle."""

            def __call__(self, argv, cwd=None):
                rc, out = super().__call__(argv, cwd)
                if "incidents" in argv and self.injected and self.appear_at is not None:
                    doc = json.loads(out)
                    if self.ft.t < self.appear_at:
                        doc["items"] = [
                            i for i in doc["items"] if i["metadata"]["uid"] != "later"
                        ]
                    out = json.dumps(doc)
                return rc, out

        c = Appears(self.ft, verify_until=0.0)
        c.appear_at = None
        live = self.live(c)
        settled_at = {}

        def run_one(cell, rec, guard):
            orig = live.settle

            def settle(rec_, before, mono):
                c.appear_at = self.ft.t + 30
                start = self.ft.t
                orig(rec_, before, mono)
                settled_at["after"] = self.ft.t - start

            live.settle = settle
            return live(cell, rec, guard)

        self.drive(run_one, max_runs=1)
        r = self.reload().runs[0]
        self.assertTrue(r["settle"]["settled"])
        self.assertGreaterEqual(settled_at["after"], 30 + batch.SETTLE_S)

    def test_the_cap_voids_the_run_halts_the_batch_and_no_reset_follows(self):
        c = LaterIncident(self.ft, verify_until=None)  # Verifying for ever
        live = self.live(c, settle_cap_s=300)
        out = self.drive(live, max_runs=3)
        r = self.reload().runs[0]
        self.assertEqual(
            (r["status"], r["void_reason"]), ("void", batch.SETTLE_CAP_REASON)
        )
        self.assertFalse(r["settle"]["settled"])
        self.assertEqual(r["settle"]["open"], ["inc-later"])
        self.assertEqual(out, {"recovered": [], "ran": 1, "stopped": f"halt:{batch.SETTLE_CAP_REASON}"})  # fmt: skip
        q = self.reload()
        self.assertEqual(q.doc["halt"]["reason"], batch.SETTLE_CAP_REASON)
        self.assertEqual(q.doc["halt"]["run_id"], r["run_id"])
        self.assertEqual(
            len(c.resets()), 1
        )  # only the run's own reset, before its injection
        calls = [a for _, a in c.times]
        patch = next(
            i
            for i, a in enumerate(calls)
            if "nexus-operator-config" in a and "patch" in a
        )
        self.assertLess(
            patch,
            next(i for i, a in enumerate(calls) if any("/fault/" in x for x in a)),
        )
        # the cap counts from the injection
        self.assertGreaterEqual(self.ft.t - c.injections()[0], 300)
        # a halted batch refuses to go on until the owner clears it
        with self.assertRaises(batch.Refused):
            self.drive(live)

    def test_a_run_voided_during_the_wait_still_holds_its_reset(self):
        class Pauses(LaterIncident):
            """A VM pause on the second Incident read after the injection."""

            reads_after = 0

            def __call__(self, argv, cwd=None):
                if "incidents" in argv and self.injected:
                    self.reads_after += 1
                    if self.reads_after == 2:
                        self.ft.clock.pause(5)
                return super().__call__(argv, cwd)

        # the run's Incident is still Diagnosing at the pause, so the wait loop sees it
        c = Pauses(
            self.ft, verify_until=200.0, phases=("Diagnosing",) * 5 + ("Resolved",)
        )
        self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual((r["status"], r["void_reason"]), ("void", "pause_gap"))
        self.assertEqual(r["void_before_settle"], "pause_gap")
        self.assertTrue(r["settle"]["settled"])
        self.assertGreaterEqual(self.ft.t, 200.0 + batch.SETTLE_S)

    def test_the_settle_cap_must_cover_the_wait_and_the_settle(self):
        with self.assertRaises(ValueError):
            self.live(tb.FakeCluster(self.ft), settle_cap_s=100)  # wait_s 50 + 60 > 100
