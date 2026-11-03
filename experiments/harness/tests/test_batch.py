"""Offline tests for batch.py: persistence, resume after a reboot, void-and-requeue, the attempt
cap, the approval gate, the systemd unit, and the live run against a fake exec."""

import contextlib
import io
import json
import pathlib
import re
import tempfile
import unittest

from .. import batch, metrics

REF = "G16/pre-registration#approval-1"
CELLS = [
    {"cell": "C1/S1/L3", "scenario": "S1", "reasoner": "R1", "level": 3},
    {"cell": "C1/S4/L3", "scenario": "S4", "reasoner": "R1", "level": 3},
]
UNIT = pathlib.Path(__file__).resolve().parents[1] / "systemd" / "nexus-batch.service"


class FakeClock:
    """boot_id, realtime and monotonic, moved by hand."""

    def __init__(self, boot="boot-A"):
        self.boot, self.real, self.mono = boot, 1000.0, 50.0

    def __call__(self):
        return {"boot_id": self.boot, "realtime": self.real, "monotonic": self.mono}

    def tick(self, s=1.0):
        self.real += s
        self.mono += s

    def pause(self, s):  # a VM pause: realtime jumps, monotonic does not
        self.real += s

    def reboot(self):
        self.boot = "boot-B"
        self.mono = 3.0


def ok(valid=True, terminal="Resolved", fallback=False, why=None):
    return batch.Outcome(valid, terminal, fallback, why)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.clock = FakeClock()
        self.q = batch.Queue.create(self.dir, REF, CELLS)

    def drive(self, run_one, queue=None, **kw):
        return batch.drive(
            queue or self.q,
            run_one,
            clock=self.clock,
            execute=True,
            approval_ref=REF,
            **kw,
        )

    def reload(self):
        return batch.Queue.load(self.dir)


class PersistenceTest(Base):
    def test_state_is_on_disk_after_every_change_and_reloads_identically(self):
        seen = []

        def run_one(cell, rec, guard):
            seen.append(
                json.loads((self.dir / "queue.json").read_text())
            )  # what a crash now would leave
            self.clock.tick()
            return ok()

        self.drive(run_one, max_runs=2)
        self.assertEqual(
            seen[0]["runs"][0]["status"], "running"
        )  # written before the run began
        self.assertEqual(self.reload().doc, self.q.doc)
        self.assertEqual([r["status"] for r in self.reload().runs], ["valid", "valid"])

    def test_atomic_write_leaves_no_temp_file_and_never_overwrites_a_queue(self):
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["queue.json"])
        with self.assertRaises(batch.Refused):
            batch.Queue.create(self.dir, REF, CELLS)

    def test_a_torn_temp_file_does_not_damage_the_queue(self):
        (self.dir / "queue.json.tmp").write_text(
            "{ half a write"
        )  # a kill between write and rename
        self.assertEqual(self.reload().doc, self.q.doc)
        self.q.save()  # the next save replaces the temp file
        self.assertFalse((self.dir / "queue.json.tmp").exists())

    def test_bad_queue_files_and_cells_are_refused(self):
        (self.dir / "queue.json").write_text("not json")
        with self.assertRaises(batch.Refused):
            self.reload()
        with tempfile.TemporaryDirectory() as d:
            for cells in ([], [CELLS[0], CELLS[0]], [{"cell": "x"}]):
                with self.assertRaises(batch.Refused):
                    batch.Queue.create(d, REF, cells)

    def test_cells_run_in_order_and_finish_before_the_next(self):
        order = []

        def run_one(cell, rec, guard):
            order.append(cell["cell"])
            return ok()

        self.drive(run_one)
        self.assertEqual(order, ["C1/S1/L3"] * 10 + ["C1/S4/L3"] * 10)
        self.assertEqual(
            [c["status"] for c in batch._status(self.q)["cells"]], ["done", "done"]
        )


class ResumeTest(Base):
    def test_reboot_voids_the_cut_off_run_and_requeues_it(self):
        # a run is `running` in the file, as a reboot would leave it
        rec = self.q.begin(CELLS[0], self.clock())
        self.assertEqual(self.reload().runs[0]["status"], "running")
        self.clock.reboot()
        calls = []

        def run_one(cell, rec, guard):
            calls.append(rec["run_id"])
            return ok()

        out = self.drive(run_one, queue=self.reload(), max_runs=1)
        self.assertEqual(out["recovered"], [rec["run_id"]])
        q = self.reload()
        first = q.runs[0]
        self.assertEqual(
            (first["status"], first["void_reason"]), ("void", "boot_id_changed")
        )
        self.assertEqual(first["boot_id_end"], "boot-B")
        self.assertEqual(
            calls, ["C1/S1/L3#2"]
        )  # requeued: a new attempt on the same cell
        self.assertEqual(
            q.cell_state("C1/S1/L3")["attempts"], 2
        )  # the void run still counts

    def test_restart_on_the_same_boot_is_interrupted_not_boot_id_changed(self):
        self.q.begin(CELLS[0], self.clock())
        out = self.drive(lambda *a: ok(), queue=self.reload(), max_runs=0)
        self.assertEqual(out["stopped"], "max_runs")
        self.assertEqual(self.reload().runs[0]["void_reason"], "interrupted")

    def test_recovery_is_idempotent(self):
        self.q.begin(CELLS[0], self.clock())
        self.clock.reboot()
        q = self.reload()
        self.assertEqual(len(q.recover(self.clock())), 1)
        self.assertEqual(q.recover(self.clock()), [])

    def test_a_fresh_process_continues_where_the_last_one_stopped(self):
        self.drive(lambda *a: ok(), max_runs=4)
        q = self.reload()  # "the machine restarted": a new process reads the file
        self.assertEqual(q.cell_state("C1/S1/L3")["valid"], 4)
        order = []
        self.drive(
            lambda c, r, g: order.append(r["run_id"]) or ok(), queue=q, max_runs=1
        )
        self.assertEqual(order, ["C1/S1/L3#5"])

    def test_stop_file_halts_between_runs(self):
        stop = self.dir / "STOP"

        def run_one(cell, rec, guard):
            stop.write_text("")
            return ok()

        out = self.drive(run_one, stop_file=stop)
        self.assertEqual((out["ran"], out["stopped"]), (1, "STOP"))
        self.assertEqual(self.reload().runs[0]["status"], "valid")


class VoidAndRequeueTest(Base):
    def test_boot_id_change_during_a_run_voids_it_whatever_the_run_measured(self):
        calls = iter([self._reboot_during, lambda c, r, g: ok()])

        def run_one(cell, rec, guard):
            return next(calls)(cell, rec, guard)

        self.drive(run_one, max_runs=2)
        r = self.reload().runs
        self.assertEqual(
            (r[0]["status"], r[0]["void_reason"]), ("void", "boot_id_changed")
        )
        self.assertEqual((r[1]["status"], r[1]["attempt"]), ("valid", 2))

    def _reboot_during(self, cell, rec, guard):
        self.clock.reboot()
        guard.check()  # the run's own step check notices
        return ok()  # never reached

    def test_end_check_voids_a_run_that_finished_after_a_pause(self):
        def run_one(cell, rec, guard):
            self.clock.pause(5.0)  # the VM paused, nobody checked until the end
            return ok()

        self.drive(run_one, max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual((r["status"], r["void_reason"]), ("void", "pause_gap"))
        self.assertAlmostEqual(r["pause_gap_s"], 5.0)

    def test_pause_threshold_comes_from_the_queue_and_small_gaps_pass(self):
        def run_one(cell, rec, guard):
            self.clock.pause(0.5)
            return ok()

        self.drive(run_one, max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(r["status"], "valid")
        self.assertAlmostEqual(r["pause_gap_s"], 0.5)
        with tempfile.TemporaryDirectory() as d:
            q = batch.Queue.create(d, REF, CELLS, max_pause_gap=0.1)
            batch.drive(
                q, run_one, clock=self.clock, execute=True, approval_ref=REF, max_runs=1
            )
            self.assertEqual(q.runs[0]["void_reason"], "pause_gap")

    def test_void_rule_arithmetic(self):
        a = {"boot_id": "x", "realtime": 10.0, "monotonic": 5.0}
        self.assertIsNone(
            batch.void_reason(a, {**a, "realtime": 20.0, "monotonic": 15.0}, 1.0)
        )
        self.assertEqual(
            batch.void_reason(a, {**a, "realtime": 20.0, "monotonic": 12.0}, 1.0),
            "pause_gap",
        )
        self.assertEqual(
            batch.void_reason(a, {**a, "boot_id": "y"}, 1.0), "boot_id_changed"
        )

    def test_discards_are_attempts_and_a_runner_error_stops_the_batch(self):
        def run_one(cell, rec, guard):
            raise RuntimeError("kubectl exploded")

        out = self.drive(run_one)
        self.assertTrue(out["stopped"].startswith("error:RuntimeError"))
        r = self.reload().runs
        self.assertEqual(
            (len(r), r[0]["status"], r[0]["discard_reason"]),
            (1, "discarded", "runner_error:RuntimeError"),
        )

    def test_a_discard_counts_and_does_not_stop(self):
        outs = iter([ok(False, why="precheck_timeout")] + [ok()] * 30)
        self.drive(lambda *a: next(outs), max_runs=3)
        st = self.reload().cell_state("C1/S1/L3")
        self.assertEqual((st["valid"], st["attempts"], st["discarded"]), (2, 3, 1))


class AttemptCapTest(Base):
    def test_ten_valid_runs_close_a_cell(self):
        self.drive(lambda *a: ok(), max_runs=10)
        self.assertEqual(self.reload().cell_state("C1/S1/L3")["status"], "done")

    def test_fifteen_attempts_exhaust_a_cell_that_never_reaches_ten(self):
        # every other attempt is void: 7 or 8 valid in 15 attempts
        flip = iter(range(1000))

        def run_one(cell, rec, guard):
            if cell["cell"] == "C1/S1/L3" and next(flip) % 2:
                return ok(False, why="precheck_timeout")
            return ok()

        self.drive(run_one)
        q = self.reload()
        s1 = q.cell_state("C1/S1/L3")
        self.assertEqual((s1["status"], s1["attempts"]), ("exhausted", 15))
        self.assertLess(s1["valid"], 10)
        self.assertEqual(len(q.cell_runs("C1/S1/L3")), 15)  # never a 16th
        self.assertEqual(
            q.cell_state("C1/S4/L3")["status"], "done"
        )  # the batch moved on

    def test_void_runs_use_attempts_like_any_other(self):
        def run_one(cell, rec, guard):
            self.clock.boot = f"boot-{rec['attempt']}"  # a reboot during every run
            guard.check()
            return ok()

        self.drive(run_one, max_runs=15)
        st = self.reload().cell_state("C1/S1/L3")
        self.assertEqual(
            (st["void"], st["attempts"], st["status"]), (15, 15, "exhausted")
        )
        out = self.drive(lambda *a: ok(), queue=self.reload(), max_runs=0)
        self.assertEqual(out["stopped"], "max_runs")


class FallbackRecordTest(Base):
    def test_every_record_carries_fallbackUsed_and_it_reaches_the_metric(self):
        outs = iter([ok(fallback=True), ok(), ok(fallback=True), ok()])
        self.drive(lambda *a: next(outs), max_runs=4)
        runs = self.reload().runs
        self.assertTrue(all("fallbackUsed" in r for r in runs))
        self.assertEqual([r["fallbackUsed"] for r in runs], [True, False, True, False])
        f = metrics.fallback_rate(self.reload().export_runs())
        self.assertEqual((f["R1"]["k"], f["R1"]["n"]), (2, 4))

    def test_void_runs_are_excluded_from_the_rate_but_reported_per_cell(self):
        def run_one(cell, rec, guard):
            if rec["attempt"] == 1:
                self.clock.pause(9)
            return ok(fallback=True)

        self.drive(run_one, max_runs=2)
        rows = self.reload().export_runs()
        self.assertEqual(metrics.fallback_rate(rows)["R1"]["n"], 1)
        d = metrics.discards(rows)["R1/S1"]
        self.assertEqual((d["valid"], d["discarded"]), (1, ["pause_gap"]))


class ApprovalGateTest(Base):
    def test_run_needs_execute_and_the_queues_own_approval_reference(self):
        run = lambda **kw: batch.drive(self.q, lambda *a: ok(), clock=self.clock, **kw)
        for kw in (
            {"execute": False, "approval_ref": REF},
            {"execute": True, "approval_ref": None},
            {"execute": True, "approval_ref": batch.PLACEHOLDER_REF},
            {"execute": True, "approval_ref": "G16/someone-elses-ref"},
            {"execute": True, "approval_ref": "x y"},
        ):
            with self.assertRaises(batch.Refused, msg=kw):
                run(**kw)
        self.assertEqual(self.reload().runs, [])  # nothing started

    def test_init_refuses_a_missing_or_placeholder_reference(self):
        with tempfile.TemporaryDirectory() as d:
            for ref in (None, "", batch.PLACEHOLDER_REF, "a b"):
                with self.assertRaises(batch.Refused):
                    batch.Queue.create(d, ref, CELLS)

    def test_command_line_dry_run_prints_and_writes_nothing(self):
        before = (self.dir / "queue.json").read_text()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = batch.main(
                [
                    "run",
                    "--state",
                    str(self.dir),
                    "--context",
                    "ctx",
                    "--approval-ref",
                    REF,
                ]
            )
        out = json.loads(buf.getvalue())
        self.assertEqual(
            (rc, out["dry_run"], out["next_cell"]["cell"]), (0, True, "C1/S1/L3")
        )
        self.assertEqual((self.dir / "queue.json").read_text(), before)

    def test_command_line_refusals_exit_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = batch.main(
                ["run", "--state", str(self.dir), "--context", "ctx", "--execute"]
            )
            self.assertEqual(rc, 2)  # --execute without --approval-ref
            self.assertEqual(
                batch.main(["status", "--state", str(self.dir / "nope")]), 2
            )
        self.assertIn("REFUSED", err.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(batch.main(["status", "--state", str(self.dir)]), 0)
        self.assertEqual(json.loads(out.getvalue())["approval_ref"], REF)


class SystemdUnitTest(unittest.TestCase):
    text = UNIT.read_text()

    def test_unit_ships_with_the_placeholder_and_the_exit_code_contract(self):
        self.assertIn(
            batch.PLACEHOLDER_REF, self.text
        )  # refused by batch until the owner edits it
        self.assertIn("-m experiments.harness.batch run", self.text)
        self.assertIn("--execute", self.text)
        self.assertRegex(self.text, r"Restart=on-failure")
        self.assertRegex(
            self.text, r"RestartPreventExitStatus=2 3"
        )  # refused / error: no restart loop
        self.assertRegex(self.text, r"WantedBy=default\.target")

    def test_unit_names_no_secret_and_is_installed_by_the_owner(self):
        self.assertIn("owner's step", self.text)
        self.assertNotRegex(self.text, r"(?i)(password|token|api[_-]?key|secret)\s*=")
        self.assertFalse(re.search(r"/home/\w+", self.text))  # %h, not a user's path

    def test_unit_with_the_placeholder_is_refused_by_the_command(self):
        with self.assertRaises(batch.Refused):
            batch.check_ref(batch.PLACEHOLDER_REF)


# ---------------------------------------------------------------- the live run, on a fake exec


class FakeCluster:
    """Answers the runner's kubectl calls; Incidents appear after the inject step."""

    def __init__(
        self, existing=(), phases=("Resolved",), fallback=False, synced="Synced/Healthy"
    ):
        self.calls, self.injected, self.synced = [], False, synced
        self.existing = list(existing)
        self.phases, self.fallback = list(phases), fallback

    def __call__(self, argv, cwd=None):
        self.calls.append(argv)
        if "incidents" in argv:
            items = list(self.existing)
            if self.injected:
                phase = self.phases.pop(0) if len(self.phases) > 1 else self.phases[0]
                items.append({"metadata": {"uid": "new"},
                              "status": {"phase": phase, "reasoner": {"fallbackUsed": self.fallback}}})  # fmt: skip
            return 0, json.dumps({"items": items})
        if any("sync.status" in a for a in argv):
            return 0, self.synced
        if "jsonpath={.items[*].metadata.name}" in argv:
            return 0, "sample-api-a sample-api-b"
        if any("/fault/" in a for a in argv):  # S1's hang: the fault goes in
            self.injected = True
        return 0, ""


class LiveRunTest(Base):
    def live(self, cluster, **kw):
        from .. import runner

        r = runner.Runner(
            "ctx",
            exec_=cluster,
            sleep=lambda s: None,
            locust="http://l",
            http_post=lambda u, f: None,
        )
        return batch.LiveRun(r, wait_s=5, poll_s=0, sleep=lambda s: None, **kw)

    def test_happy_path_reads_terminal_phase_and_fallback(self):
        c = FakeCluster(phases=("Diagnosing", "Resolved"), fallback=True)
        out = self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(
            (r["status"], r["terminal"], r["fallbackUsed"]), ("valid", "Resolved", True)
        )
        joined = [" ".join(a) for a in c.calls]
        self.assertTrue(
            all("--context ctx" in j for j in joined if j.startswith("kubectl"))
        )
        self.assertFalse(
            any(" delete " in j for j in joined)
        )  # Incidents are not deleted by the runner
        self.assertEqual(out["ran"], 1)

    def test_non_terminal_incident_discards_with_a_reason(self):
        c = FakeCluster(
            existing=[{"metadata": {"uid": "old"}, "status": {"phase": "Executing"}}]
        )
        self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(
            (r["status"], r["discard_reason"]), ("discarded", "non_terminal_incident")
        )
        self.assertFalse(c.injected)  # no fault was injected over a live incident

    def test_unsynced_applications_discard_before_any_injection(self):
        c = FakeCluster(synced="OutOfSync/Healthy")
        live = self.live(c)
        live.r.wait_synced = lambda *a, **k: False
        self.drive(live, max_runs=1)
        self.assertEqual(
            self.reload().runs[0]["discard_reason"], "applications_not_synced"
        )
        self.assertFalse(c.injected)

    def test_incident_that_never_ends_is_discarded(self):
        c = FakeCluster(phases=("Executing",))
        live = self.live(c)
        live.wait_s = 0
        self.drive(live, max_runs=1)
        self.assertEqual(
            self.reload().runs[0]["discard_reason"], "no_terminal_incident"
        )


if __name__ == "__main__":
    unittest.main()
