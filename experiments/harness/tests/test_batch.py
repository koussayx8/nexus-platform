"""Offline tests for batch.py: persistence, resume after a reboot, void-and-requeue, the attempt
cap, the approval gate, the systemd unit, and the live run against a fake exec."""

import contextlib
import io
import json
import pathlib
import re
import tempfile
import unittest
import urllib.parse

from .. import batch, metrics, precheck, runner

REF = "G16/pre-registration#approval-1"
# Targets, caps and tiers are G16 configuration; these are test values (Tier 1 = a small target).
CELLS = [
    {"cell": "C1/S1/L3", "scenario": "S1", "reasoner": "R1", "level": 3,
     "tier": 1, "valid_target": 4, "max_attempts": 6},
    {"cell": "C1/S4/L3", "scenario": "S4", "reasoner": "R1", "level": 3,
     "tier": 1, "valid_target": 4, "max_attempts": 6},
]  # fmt: skip
INDEX_HASH = (
    "ab" * 32
)  # a corpus hash is 64 lowercase hex digits (bm25.Index.corpus_hash)
PRE = {  # the pre-registration's clean baseline (a stddev per signal); the rest are spec defaults
    "clean_stddev": {"cpu": 0.05, "latencyP95": 0.02, "inflight": 2.0, "errorRatio": 0.005, "requests": 3.0},
    "cap_s": 100,
    "poll_s": 10,
    "locust": {"baseline_users": 160, "baseline_rps_per_namespace": 80},  # B = 80 per namespace (ADR-026)
    "reasoner": {"expected_index_hash": INDEX_HASH},
}  # fmt: skip
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
        self.q = batch.Queue.create(self.dir, REF, CELLS, precheck_cfg=PRE)

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
            batch.Queue.create(self.dir, REF, CELLS, precheck_cfg=PRE)

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

    def test_round_robin_then_every_cell_reaches_its_target(self):
        order = []

        def run_one(cell, rec, guard):
            order.append(cell["cell"])
            return ok()

        self.drive(run_one)
        self.assertEqual(order, ["C1/S1/L3", "C1/S4/L3"] * 4)
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
        self.assertEqual(q.cell_state("C1/S1/L3")["valid"], 2)
        order = []
        self.drive(
            lambda c, r, g: order.append(r["run_id"]) or ok(), queue=q, max_runs=1
        )
        self.assertEqual(order, ["C1/S1/L3#3"])

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
            q = batch.Queue.create(d, REF, CELLS, max_pause_gap=0.1, precheck_cfg=PRE)
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
        q = self.reload()
        st = q.cell_state(
            "C1/S1/L3"
        )  # the discarded run is requeued at once, then the other cell has its turn
        self.assertEqual((st["valid"], st["attempts"], st["discarded"]), (1, 2, 1))
        self.assertEqual(q.cell_state("C1/S4/L3")["valid"], 1)


class AttemptCapTest(Base):
    def test_the_target_closes_a_cell(self):
        self.drive(lambda *a: ok(), max_runs=8)
        self.assertEqual(
            [c["status"] for c in batch._status(self.reload())["cells"]],
            ["done", "done"],
        )

    def test_the_cap_exhausts_a_cell_that_never_reaches_its_target(self):
        flip = iter(range(1000))

        def run_one(cell, rec, guard):
            if cell["cell"] == "C1/S1/L3" and next(flip) % 2:
                return ok(False, why="precheck_timeout")
            return ok()

        self.drive(run_one)
        q = self.reload()
        s1 = q.cell_state("C1/S1/L3")
        self.assertEqual((s1["status"], s1["attempts"], s1["cap"]), ("exhausted", 6, 6))
        self.assertLess(s1["valid"], 4)
        self.assertEqual(len(q.cell_runs("C1/S1/L3")), 6)  # never a 7th
        self.assertEqual(
            q.cell_state("C1/S4/L3")["status"], "done"
        )  # the batch moved on

    def test_void_runs_use_attempts_like_any_other(self):
        def run_one(cell, rec, guard):
            self.clock.boot = (
                f"boot-{len(self.reload().runs)}"  # a reboot during every run
            )
            guard.check()
            return ok()

        out = self.drive(run_one)
        q = self.reload()
        for c in ("C1/S1/L3", "C1/S4/L3"):
            st = q.cell_state(c)
            self.assertEqual(
                (st["void"], st["attempts"], st["status"]), (6, 6, "exhausted")
            )
        self.assertEqual(out["stopped"], "finished")

    def test_caps_and_targets_are_per_cell_configuration(self):
        cells = [
            {**CELLS[0], "valid_target": 1, "max_attempts": 1},
            {**CELLS[1], "valid_target": 2, "max_attempts": 3},
        ]
        with tempfile.TemporaryDirectory() as d:
            q = batch.Queue.create(d, REF, cells, precheck_cfg=PRE)
            batch.drive(
                q,
                lambda *a: ok(False, why="x"),
                clock=self.clock,
                execute=True,
                approval_ref=REF,
            )
            self.assertEqual([len(q.cell_runs(c["cell"])) for c in cells], [1, 3])


class TierTest(Base):
    def make(self, cells):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return batch.Queue.create(d.name, REF, cells, precheck_cfg=PRE)

    def cells(self):
        t1 = {"tier": 1, "valid_target": 2, "max_attempts": 3}
        t2 = {"tier": 2, "valid_target": 4, "max_attempts": 5}
        mk = lambda n, **kw: {
            "cell": n,
            "scenario": "S1",
            "reasoner": "R1",
            "level": 3,
            **kw,
        }
        # declared with Tier 2 first: the tier, not the order, decides
        return [mk("t2-a", **t2), mk("t1-a", **t1), mk("t1-b", **t1), mk("t2-b", **t2)]

    def test_tier_one_runs_before_tier_two_and_round_robin_inside_a_tier(self):
        q = self.make(self.cells())
        order = []
        batch.drive(
            q,
            lambda c, r, g: order.append(c["cell"]) or ok(),
            clock=self.clock,
            execute=True,
            approval_ref=REF,
        )
        self.assertEqual(
            order[:4], ["t1-a", "t1-b", "t1-a", "t1-b"]
        )  # all of Tier 1 first
        self.assertEqual(order[4:12], ["t2-a", "t2-b"] * 4)
        self.assertEqual(len(order), 12)

    def test_stopping_anywhere_leaves_equal_repetitions(self):
        q = self.make(self.cells())
        for stop_after in range(1, 13):
            with tempfile.TemporaryDirectory() as d:
                q = batch.Queue.create(d, REF, self.cells(), precheck_cfg=PRE)
                batch.drive(
                    q,
                    lambda *a: ok(),
                    clock=self.clock,
                    execute=True,
                    approval_ref=REF,
                    max_runs=stop_after,
                )
                for tier in (1, 2):
                    v = [
                        q.cell_state(c["cell"])["valid"]
                        for c in q.doc["cells"]
                        if c["tier"] == tier
                    ]
                    self.assertLessEqual(max(v) - min(v), 1, (stop_after, tier, v))
                if any(
                    q.cell_state(c["cell"])["valid"]
                    for c in q.doc["cells"]
                    if c["tier"] == 2
                ):
                    self.assertTrue(  # Tier 2 only ever started once Tier 1 was finished
                        all(
                            q.cell_state(c["cell"])["status"] == "done"
                            for c in q.doc["cells"]
                            if c["tier"] == 1
                        )
                    )

    def test_an_exhausted_tier_one_cell_does_not_block_tier_two(self):
        cells = self.cells()
        q = self.make(cells)

        def run_one(cell, rec, guard):
            return ok(False, why="x") if cell["cell"] == "t1-a" else ok()

        batch.drive(q, run_one, clock=self.clock, execute=True, approval_ref=REF)
        self.assertEqual(q.cell_state("t1-a")["status"], "exhausted")
        self.assertEqual(
            [q.cell_state(c)["status"] for c in ("t1-b", "t2-a", "t2-b")], ["done"] * 3
        )

    def test_targets_caps_and_tiers_are_required_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            base = {"cell": "x", "scenario": "S1", "reasoner": "R1", "level": 3}
            bad = [
                base,  # no tier, target or cap
                {**base, "tier": 1, "valid_target": 5},  # no cap
                {**base, "tier": 0, "valid_target": 5, "max_attempts": 8},
                {**base, "tier": 1, "valid_target": 0, "max_attempts": 8},
                {
                    **base,
                    "tier": 1,
                    "valid_target": 5,
                    "max_attempts": 4,
                },  # cap below target
                {
                    **base,
                    "tier": 1,
                    "valid_target": 5,
                    "max_attempts": 8,
                    "advisory_checks": "maybe",
                },
                {
                    **base,
                    "tier": 1,
                    "valid_target": 5,
                    "max_attempts": 8,
                    "advisory_checks": "off",
                },  # not phase D
            ]
            for cell in bad:
                with self.assertRaises(batch.Refused, msg=cell):
                    batch.Queue.create(d, REF, [cell], precheck_cfg=PRE)
            good = {
                **base,
                "tier": 1,
                "valid_target": 5,
                "max_attempts": 8,
                "phase": "D",
                "advisory_checks": "off",
            }
            batch.Queue.create(d, REF, [good], precheck_cfg=PRE)

    def test_the_precheck_configuration_is_required_and_checked(self):
        for pre in (
            None,
            {},
            {"clean_stddev": {"cpu": 1}},
            {**PRE, "cap_s": 0},
            {**PRE, "clean_stddev": {**PRE["clean_stddev"], "cpu": -1}},
        ):
            with (
                tempfile.TemporaryDirectory() as d,
                self.assertRaises(batch.Refused, msg=pre),
            ):
                batch.Queue.create(d, REF, CELLS, precheck_cfg=pre)


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


# ---------------------------------------------------------------- pre-checks and the live run, on a fake exec

CLEAN = PRE["clean_stddev"]
RULE = {"cpu": "cpu", "latencyP95": "latency_p95", "inflight": "inflight", "errorRatio": "error_ratio", "requests": "requests"}  # fmt: skip


class FakeTime:
    """monotonic/wall/sleep in one: sleeping advances every clock, so no real waiting and no pause gap."""

    def __init__(self, clock):
        self.t, self.clock = 0.0, clock

    def monotonic(self):
        return self.t

    def wall(self):
        return 1_800_000_000.0 + self.t

    def sleep(self, s):
        self.t += s
        self.clock.tick(s)


def runner_for(cluster, ft, **kw):
    """A Runner whose exec, sleep and Locust GET are all the fakes."""
    return runner.Runner("ctx", exec_=cluster, sleep=ft.sleep, locust="http://l", http_post=lambda u, f: None,
                         http_get=lambda url: (200, json.dumps(cluster.locust_doc)), **kw)  # fmt: skip


def audit_fresh(ft):
    return lambda path: ft.wall() - 5.0  # written five seconds ago


class FakeCluster:
    """Answers the runner's kubectl calls, the Prometheus proxy reads and the Incident list.

    z / sd_factor / node_cpu / node_mem / alerts drive the pre-check answers; `prom` maps a substring
    of the PromQL to a replacement (None = the call fails). Incidents: `existing` are there from the
    start; `open_polls` > 0 makes a non-terminal one end after that many Incident reads; an injected
    fault creates the run's own Incident."""

    def __init__(
        self,
        ft,
        existing=(),
        phases=("Resolved",),
        fallback=False,
        open_polls=0,
        stuck=False,
    ):
        self.ft, self.calls, self.injected, self.synced = (
            ft,
            [],
            False,
            "Synced/Healthy",
        )
        self.existing, self.phases, self.fallback = (
            list(existing),
            list(phases),
            fallback,
        )
        self.reads, self.open_polls, self.stuck = 0, open_polls, stuck
        self.z, self.sd_factor, self.node_cpu, self.node_mem, self.alerts, self.prom = (
            0.2,
            1.0,
            0.3,
            0.5,
            [],
            {},
        )
        self.sample = (2, 2, 2)  # desired, ready, updated
        self.killswitch, self.level_label, self.backstage_pods = "active", "3", 0
        self.reasoner = {  # the shape reasoner/nexus_reasoner/service.py returns; None: the call fails
            "status": "ready",
            "corpusHash": INDEX_HASH,
            "promptVersion": "prompt/v1",
            "scoreFloor": 2.9584,
        }
        self.locust_doc = {"state": "running", "user_count": 160}
        self.rps = 80.0
        self.recorder_up = 1.0  # nexus_operator_recorder_up: a number, a list of series, or None (no answer)
        self.false_alarm_after = None  # an Incident appears (and ends) this many reads in, before any injection

    def vector(self, value):
        return json.dumps(
            {
                "status": "success",
                "data": {"result": [{"metric": {}, "value": [0, str(value)]}]},
            }
        )

    def promql(self, q):
        for key, val in self.prom.items():
            if key in q:
                return val
        if q == "nexus_operator_recorder_up":
            return self.recorder_up
        if ":rate2m" in q:
            return self.rps
        if ":zscore15m" in q:
            return self.z
        if ":baseline_stddev15m" in q:
            rule = q.split("nexus_sample_api_")[1].split(":")[0]
            return CLEAN[next(k for k, v in RULE.items() if v == rule)] * self.sd_factor
        if "node_cpu_seconds_total" in q:
            return self.node_cpu
        if "MemAvailable" in q:
            return self.node_mem
        if "ALERTS" in q:
            return [{"metric": m} for m in self.alerts]
        raise AssertionError(f"unexpected PromQL {q}")

    def __call__(self, argv, cwd=None):
        self.calls.append(argv)
        line = " ".join(argv)
        if "--raw" in argv and "/proxy/healthz" in argv[-1]:
            return (
                (0, json.dumps(self.reasoner)) if self.reasoner is not None else (1, "")
            )
        if line.endswith("-n nexus-dev get deployment sample-api -o json"):
            d, r_, u = self.sample
            return 0, json.dumps(
                {
                    "spec": {"replicas": d},
                    "status": {"readyReplicas": r_, "updatedReplicas": u},
                }
            )
        if "get configmap nexus-killswitch" in line:
            return (
                (0, json.dumps({"data": {"state": self.killswitch}}))
                if self.killswitch is not None
                else (1, "")
            )
        if line.endswith("get namespace nexus-dev -o json"):
            labels = (
                {"nexus.io/autonomy-level": self.level_label}
                if self.level_label is not None
                else {}
            )
            return 0, json.dumps({"metadata": {"labels": labels}})
        if line.endswith("-n backstage get pods -o json"):
            return 0, json.dumps({"items": [{}] * self.backstage_pods})
        if "--raw" in argv:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(argv[-1]).query)["query"][0]
            ans = self.promql(q)
            if ans is None:
                return 1, ""
            if isinstance(ans, list):
                return 0, json.dumps({"status": "success", "data": {"result": ans}})
            return 0, self.vector(ans)
        if "incidents" in argv:
            self.reads += 1
            items = []
            for i in self.existing:
                st = dict(i["status"])
                if self.open_polls and self.reads > self.open_polls and not self.stuck:
                    st["phase"] = "Resolved"
                items.append({**i, "status": st})
            if (
                self.false_alarm_after is not None
                and self.reads > self.false_alarm_after
                and not self.injected
            ):
                items.append(
                    {
                        "metadata": {"uid": "false-alarm"},
                        "status": {"phase": "Escalated"},
                    }
                )
            if self.injected:
                if self.false_alarm_after is not None:
                    items.append(
                        {
                            "metadata": {"uid": "false-alarm"},
                            "status": {"phase": "Escalated"},
                        }
                    )
                phase = self.phases.pop(0) if len(self.phases) > 1 else self.phases[0]
                items.append({"metadata": {"uid": "new"}, "status": {"phase": phase, "reasoner": {"fallbackUsed": self.fallback}}})  # fmt: skip
            return 0, json.dumps({"items": items})
        if any("sync.status" in a for a in argv):
            return 0, self.synced
        if "jsonpath={.items[*].metadata.name}" in argv:
            return 0, "sample-api-a sample-api-b"
        if any("/fault/" in a for a in argv):  # S1's hang: the fault goes in
            self.injected = True
        return 0, ""


def firing(name, state="firing", ns="nexus-dev"):
    return {"alertname": name, "alertstate": state, "namespace": ns}


class PrecheckTest(unittest.TestCase):
    """precheck.run_prechecks against the fake cluster; no queue involved."""

    def setUp(self):
        self.clock = FakeClock()
        self.ft = FakeTime(self.clock)
        self.cfg = precheck.normalise(PRE)

    def run_pc(self, cluster, before=frozenset(), level=3, stat=None):
        r = runner_for(cluster, self.ft)
        live = batch.LiveRun(
            r, PRE, monotonic=self.ft.monotonic, wall=self.ft.wall, sleep=self.ft.sleep
        )
        return precheck.run_prechecks(precheck.Prom(r), self.cfg, live.incidents, set(before),
                                      batch.model.TERMINAL_PHASES, level, self.ft.monotonic, self.ft.wall,
                                      self.ft.sleep, stat or audit_fresh(self.ft))  # fmt: skip

    def test_clean_baseline_and_quiet_node_pass_on_the_first_poll(self):
        pc = self.run_pc(FakeCluster(self.ft))
        self.assertEqual(
            (pc.ok, pc.polls, pc.failures, pc.stop_reason), (True, 1, [], None)
        )

    def test_the_queries_use_baseline_stddev15m_for_every_signal_in_the_namespace(self):
        c = FakeCluster(self.ft)
        self.run_pc(c)
        qs = [urllib.parse.unquote(a[-1]) for a in c.calls if "--raw" in a]
        for rule in RULE.values():
            self.assertTrue(
                any(
                    f"nexus_sample_api_{rule}:baseline_stddev15m" in q
                    and 'namespace="nexus-dev"' in q
                    for q in qs
                ),
                rule,
            )
            self.assertTrue(
                any(f"nexus_sample_api_{rule}:zscore15m" in q for q in qs), rule
            )
        raw = next(a for a in c.calls if "--raw" in a)
        self.assertEqual(
            raw[:6], ["kubectl", "--context", "ctx", "-n", "monitoring", "get"]
        )  # context and namespace named

    def test_a_masking_zscore_fails_until_the_cap_and_discards(self):
        c = FakeCluster(self.ft)
        c.z = 1.5  # the limit is "below 1.5"
        pc = self.run_pc(c)
        self.assertFalse(pc.ok)
        self.assertIsNone(pc.stop_reason)  # a discard, not a stop
        self.assertGreaterEqual(pc.waited_s, 100)  # waited the whole cap
        self.assertEqual(pc.polls, 11)
        self.assertIn("baseline:cpu:zscore=1.50", pc.failures)

    def test_negative_z_counts_by_absolute_value(self):
        c = FakeCluster(self.ft)
        c.z = -2.0
        self.assertFalse(self.run_pc(c).ok)

    def test_stddev_within_one_and_a_half_times_the_clean_baseline(self):
        c = FakeCluster(self.ft)
        c.sd_factor = 1.5
        self.assertTrue(self.run_pc(c).ok)  # exactly 1.5x is within
        c = FakeCluster(self.ft)
        c.sd_factor = 1.51
        pc = self.run_pc(c)
        self.assertFalse(pc.ok)
        self.assertTrue(any(f.startswith("baseline:cpu:stddev=") for f in pc.failures))

    def test_a_series_with_no_answer_is_not_clean(self):
        for key, want in (
            (":zscore15m", "no_zscore"),
            (":baseline_stddev15m", "no_stddev"),
        ):
            c = FakeCluster(self.ft)
            c.prom = {key: []}  # an empty vector: the rule needs 27 of 30 samples
            pc = self.run_pc(c)
            self.assertFalse(pc.ok)
            self.assertTrue(any(want in f for f in pc.failures), (key, pc.failures))
        c = FakeCluster(self.ft)
        c.prom = {":zscore15m": None}  # a failed call
        self.assertFalse(self.run_pc(c).ok)

    def test_baseline_recovers_while_waiting(self):
        c = FakeCluster(self.ft)
        orig = c.promql
        c.promql = lambda q: (self.ft.t < 40 and ":zscore15m" in q) and 4.0 or orig(q)
        pc = self.run_pc(c)
        self.assertTrue(pc.ok)
        self.assertEqual(pc.polls, 5)

    def test_node_load_limits_cpu_60_and_memory_80_percent(self):
        for attr, value in (("node_cpu", 0.60), ("node_mem", 0.80)):
            c = FakeCluster(self.ft)
            setattr(c, attr, value)
            pc = self.run_pc(c)
            self.assertFalse(pc.ok, attr)
            self.assertTrue(any(f.startswith("node:") for f in pc.failures))
        c = FakeCluster(self.ft)
        c.node_cpu, c.node_mem = 0.59, 0.79
        self.assertTrue(self.run_pc(c).ok)
        c = FakeCluster(self.ft)
        c.prom = {"node_cpu_seconds_total": None}
        self.assertFalse(self.run_pc(c).ok)  # no answer is not "quiet"

    def test_an_open_incident_is_waited_for_inside_the_cap(self):
        open_ = {"metadata": {"uid": "old"}, "status": {"phase": "Executing"}}
        c = FakeCluster(self.ft, existing=[open_], open_polls=3)
        pc = self.run_pc(c, before={"old"})
        self.assertTrue(pc.ok)
        self.assertGreater(pc.polls, 1)
        self.assertFalse(any(" delete " in " ".join(a) for a in c.calls))

    def test_an_incident_still_open_at_the_cap_stops_the_batch_and_is_never_deleted(
        self,
    ):
        open_ = {"metadata": {"uid": "old"}, "status": {"phase": "Executing"}}
        c = FakeCluster(self.ft, existing=[open_], stuck=True)
        pc = self.run_pc(c, before={"old"})
        self.assertEqual((pc.ok, pc.stop_reason), (False, "non_terminal_incident"))
        self.assertGreaterEqual(pc.waited_s, 100)
        self.assertIn("non_terminal_incident:old", pc.failures)
        self.assertFalse(
            any("delete" in a or "patch" in a for call in c.calls for a in call)
        )

    def test_the_window_records_alerts_and_incidents_that_fired_with_nothing_injected(
        self,
    ):
        c = FakeCluster(self.ft)
        c.alerts = [
            firing("NexusTrafficAnomaly"),
            firing("NexusLatencyAnomaly", "pending"),
        ]
        c.false_alarm_after = 0
        pc = self.run_pc(c, before={"pre-existing"})
        w = pc.window
        self.assertEqual([(a["alertname"], a["state"]) for a in w["alerts"]],
                         [("NexusLatencyAnomaly", "pending"), ("NexusTrafficAnomaly", "firing")])  # fmt: skip
        self.assertEqual(w["incidents"], [{"uid": "false-alarm", "phase": "Escalated"}])
        self.assertGreaterEqual(w["polls"], 1)
        raw = [
            urllib.parse.unquote(a[-1])
            for a in c.calls
            if "--raw" in a and "ALERTS" in a[-1]
        ]
        self.assertEqual(len(raw), 1)
        self.assertIn(
            "max_over_time(ALERTS", raw[0]
        )  # the whole window, not only the polls

    def test_a_quiet_window_is_recorded_as_empty_and_an_unanswered_one_as_null(self):
        w = self.run_pc(FakeCluster(self.ft)).window
        self.assertEqual((w["alerts"], w["incidents"]), ([], []))
        c = FakeCluster(self.ft)
        c.prom = {"ALERTS": None}
        self.assertIsNone(self.run_pc(c).window["alerts"])  # unknown, never "none"

    def test_window_is_kept_when_the_precheck_fails(self):
        c = FakeCluster(self.ft)
        c.z = 9
        c.alerts = [firing("NexusCpuAnomaly")]
        pc = self.run_pc(c)
        self.assertFalse(pc.ok)
        self.assertEqual(pc.window["alerts"][0]["alertname"], "NexusCpuAnomaly")


class LiveRunTest(Base):
    def setUp(self):
        super().setUp()
        self.ft = FakeTime(self.clock)

    def live(self, cluster):
        return batch.LiveRun(runner_for(cluster, self.ft), PRE, wait_s=50, poll_s=5, sleep=self.ft.sleep,
                             monotonic=self.ft.monotonic, wall=self.ft.wall, stat=audit_fresh(self.ft))  # fmt: skip

    def joined(self, c):
        return [" ".join(a) for a in c.calls]

    def test_happy_path_reads_terminal_phase_fallback_and_the_no_fault_record(self):
        c = FakeCluster(self.ft, phases=("Diagnosing", "Resolved"), fallback=True)
        c.alerts = [firing("NexusTrafficAnomaly")]
        out = self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(
            (r["status"], r["terminal"], r["fallbackUsed"]), ("valid", "Resolved", True)
        )
        self.assertEqual(r["no_fault"]["alerts"][0]["alertname"], "NexusTrafficAnomaly")
        self.assertEqual((r["precheck"]["ok"], r["precheck"]["polls"]), (True, 1))
        j = self.joined(c)
        self.assertTrue(all("--context ctx" in x for x in j if x.startswith("kubectl")))
        self.assertFalse(any(" delete " in x for x in j))  # Incidents are never deleted
        self.assertEqual(out["ran"], 1)

    def test_order_reset_then_config_then_sync_then_prechecks_then_injection(self):
        c = FakeCluster(self.ft)
        self.drive(self.live(c), max_runs=1)
        j = self.joined(c)
        idx = lambda key: next(i for i, x in enumerate(j) if key in x)
        order = [idx("rollout restart deployment/sample-api"), idx("patch configmap nexus-operator-config"),
                 idx("rollout restart deployment/nexus-operator"), idx("sync.status"), idx("zscore15m"), idx("/fault/hang")]  # fmt: skip
        self.assertEqual(order, sorted(order))

    # ---- Locust recovery (fake Locust: the master's /swarm POST starts it when `heals`)
    def recovery_live(self, c, heals=True, **loc):
        cfg = {**PRE, "locust": {**PRE["locust"], "spawn_rate": 4, "classes": ["DevUser", "ProdUser"],
                                 "recovery_wait_s": 30, **loc}}  # fmt: skip
        posts = []

        def post(url, fields):
            if url.endswith(
                "/stop"
            ):  # the reset's idle step; this fake keeps Locust as the test set it
                return
            posts.append((url, fields))
            if heals:
                c.locust_doc = {"state": "running", "user_count": 160}

        r = runner_for(c, self.ft)
        r.http_post = post
        live = batch.LiveRun(r, cfg, wait_s=50, poll_s=5, sleep=self.ft.sleep, monotonic=self.ft.monotonic,
                             wall=self.ft.wall, stat=audit_fresh(self.ft))  # fmt: skip
        return live, posts

    def test_stopped_locust_is_restarted_with_the_exact_baseline_then_the_run_goes_on(
        self,
    ):
        c = FakeCluster(self.ft)
        c.locust_doc = {"state": "stopped", "user_count": 0}
        live, posts = self.recovery_live(c)
        self.drive(live, max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(r["status"], "valid")
        self.assertEqual(
            posts,
            [
                (
                    "http://l/swarm",
                    {
                        "user_count": ["160"],
                        "spawn_rate": ["4"],
                        "user_classes": ["DevUser", "ProdUser"],
                    },
                )
            ],
        )
        (att,) = r["locust_recovery"]
        self.assertEqual((att["attempt"], att["of"], att["posted"]), (1, 2, True))
        self.assertTrue(att["why"])
        self.assertTrue(r["precheck"]["ok"])  # every pre-check re-ran after the restart

    def test_locust_off_baseline_users_is_restarted(self):
        c = FakeCluster(self.ft)
        c.locust_doc = {"state": "running", "user_count": 40}
        live, posts = self.recovery_live(c)
        self.drive(live, max_runs=1)
        self.assertEqual(len(posts), 1)
        self.assertEqual(self.reload().runs[0]["status"], "valid")

    def test_recovery_cap_stops_the_batch_with_a_clear_reason(self):
        for cap, want in ((None, 2), (1, 1), (0, 0)):
            c = FakeCluster(self.ft)
            c.locust_doc = {"state": "stopped", "user_count": 0}
            live, posts = self.recovery_live(
                c, heals=False, **({} if cap is None else {"recovery_attempts": cap})
            )
            self.setUp()
            out = self.drive(live)
            self.assertEqual(len(posts), want, cap)
            self.assertEqual(out["stopped"], "halt:locust_recovery_cap_reached")
            q = self.reload()
            self.assertEqual(q.doc["halt"]["reason"], "locust_recovery_cap_reached")
            self.assertEqual(len(q.runs[0]["locust_recovery"]), want)
            self.assertFalse(c.injected)

    def test_healthy_locust_is_never_touched_and_no_config_means_no_recovery(self):
        c = FakeCluster(self.ft)
        live, posts = self.recovery_live(c)
        self.drive(live, max_runs=1)
        self.assertEqual(posts, [])
        self.assertNotIn("locust_recovery", self.reload().runs[0])
        self.setUp()
        c = FakeCluster(self.ft)
        c.locust_doc = {"state": "stopped", "user_count": 0}
        posted = []
        r = runner_for(c, self.ft)
        r.http_post = lambda u, f: posted.append(u)
        live = batch.LiveRun(r, PRE, wait_s=50, poll_s=5, sleep=self.ft.sleep, monotonic=self.ft.monotonic,
                             wall=self.ft.wall, stat=audit_fresh(self.ft))  # fmt: skip
        self.drive(live, max_runs=1)
        self.assertEqual([u for u in posted if u.endswith("/swarm")], [])
        self.assertEqual(self.reload().runs[0]["discard_reason"], "precheck_timeout")

    def test_locust_off_baseline_at_the_end_voids_the_run_and_the_cell_requeues(self):
        class LocustDies(FakeCluster):
            def __call__(self, argv, cwd=None):
                out = super().__call__(argv, cwd)
                if self.injected:  # the fault is in; Locust stops before the run ends
                    self.locust_doc = {"state": "stopped", "user_count": 0}
                return out

        c = LocustDies(self.ft)
        self.drive(self.live(c), max_runs=1)
        q = self.reload()
        r = q.runs[0]
        self.assertEqual((r["status"], r["void_reason"]), ("void", "locust_end_check"))
        self.assertFalse(r["locust_end"]["ok"])
        self.assertIn("locust:state='stopped'", r["locust_end"]["failures"])
        self.assertEqual(q.cell_state(CELLS[0]["cell"])["valid"], 0)
        self.assertEqual(
            q.next_cell()["cell"], CELLS[0]["cell"]
        )  # open again: requeued

    def test_locust_at_baseline_at_the_end_keeps_the_run_valid(self):
        c = FakeCluster(self.ft)
        self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(r["status"], "valid")
        self.assertEqual(r["locust_end"], {"ok": True, "failures": []})

    def test_end_check_for_a_load_scenario_targets_the_cells_users(self):
        live = self.live(FakeCluster(self.ft))
        for doc, want in (
            ({"state": "running", "user_count": 300}, []),
            ({"state": "stopped", "user_count": 300}, ["locust:state='stopped'"]),
            ({"state": "running", "user_count": 160}, ["locust:users=160!~300"]),
        ):
            live.r.http_get = lambda url, d=doc: (200, json.dumps(d))
            self.assertEqual(
                live.locust_end_failures({"scenario": "S2", "users": 300}), want
            )

    def test_a_masked_baseline_discards_with_precheck_timeout_and_injects_nothing(self):
        c = FakeCluster(self.ft)
        c.z = 5
        self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(
            (r["status"], r["discard_reason"]), ("discarded", "precheck_timeout")
        )
        self.assertFalse(c.injected)
        self.assertTrue(r["precheck"]["failures"])
        self.assertIn("no_fault", r)  # the window is kept for a discarded run too

    def test_an_open_incident_at_the_cap_halts_the_batch_with_the_reason_recorded(self):
        c = FakeCluster(
            self.ft,
            existing=[{"metadata": {"uid": "old"}, "status": {"phase": "Executing"}}],
            stuck=True,
        )
        out = self.drive(self.live(c))
        self.assertEqual(out["stopped"], "halt:non_terminal_incident")
        q = self.reload()
        self.assertEqual(len(q.runs), 1)  # no second run was started
        self.assertEqual(
            (q.runs[0]["status"], q.runs[0]["discard_reason"]),
            ("discarded", "non_terminal_incident"),
        )
        self.assertEqual(q.doc["halt"]["reason"], "non_terminal_incident")
        self.assertEqual(q.doc["halt"]["run_id"], q.runs[0]["run_id"])
        self.assertFalse(c.injected)
        self.assertFalse(any(" delete " in x for x in self.joined(c)))
        # a restarted batch refuses to go on until the owner clears the halt
        with self.assertRaises(batch.Refused):
            self.drive(self.live(c), queue=q)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch.main(["clear-halt", "--state", str(self.dir)]), 0)
        self.assertIsNone(self.reload().doc["halt"])
        self.assertEqual(
            self.reload().doc["halts"][0]["reason"], "non_terminal_incident"
        )

    def test_an_open_incident_that_ends_inside_the_cap_lets_the_run_go_on(self):
        c = FakeCluster(
            self.ft,
            existing=[{"metadata": {"uid": "old"}, "status": {"phase": "Executing"}}],
            open_polls=3,
        )
        self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual((r["status"], r["precheck"]["ok"]), ("valid", True))
        self.assertGreater(r["precheck"]["polls"], 1)

    def test_a_false_alarm_in_the_window_is_recorded_and_not_taken_for_the_runs_incident(
        self,
    ):
        c = FakeCluster(self.ft, phases=("Executing", "Resolved"))
        c.false_alarm_after = (
            1  # appears after the run's first read, i.e. inside the pre-check window
        )
        self.drive(self.live(c), max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual(
            [i["uid"] for i in r["no_fault"]["incidents"]], ["false-alarm"]
        )
        self.assertEqual(
            (r["status"], r["terminal"]), ("valid", "Resolved")
        )  # the injected fault's own Incident

    def test_advisory_checks_off_only_in_a_phase_d_cell_and_restored_after(self):
        cells = [
            {
                **CELLS[0],
                "cell": "D/S1",
                "phase": "D",
                "advisory_checks": "off",
                "valid_target": 1,
            },
            {**CELLS[1], "cell": "A/S4", "valid_target": 1},
        ]
        with tempfile.TemporaryDirectory() as d:
            q = batch.Queue.create(d, REF, cells, precheck_cfg=PRE)
            c = FakeCluster(self.ft)
            batch.drive(
                q, self.live(c), clock=self.clock, execute=True, approval_ref=REF
            )
        patches = [
            json.loads(x.split(" -p ", 1)[1])["data"]
            for x in self.joined(c)
            if "patch configmap" in x
        ]
        self.assertEqual(
            [p["advisoryChecks"] for p in patches], ["off", "on"]
        )  # "on" again for the next cell
        self.assertEqual([r["advisory_checks"] for r in q.runs], ["off", "on"])
        self.assertTrue(all(int(p["breakerEpoch"]) > 1_700_000_000 for p in patches))

    def test_the_config_patch_waits_for_the_operator_restart(self):
        c = FakeCluster(self.ft)
        self.drive(self.live(c), max_runs=1)
        j = self.joined(c)
        self.assertTrue(
            any(
                x.endswith("rollout status deployment/nexus-operator --timeout=180s")
                for x in j
            )
        )
        self.assertTrue(
            any("-n nexus-system" in x and "patch configmap" in x for x in j)
        )

    def test_unsynced_applications_discard_before_any_injection(self):
        c = FakeCluster(self.ft)
        live = self.live(c)
        live.r.wait_synced = lambda *a, **k: False
        self.drive(live, max_runs=1)
        self.assertEqual(
            self.reload().runs[0]["discard_reason"], "applications_not_synced"
        )
        self.assertFalse(c.injected)

    def test_incident_that_never_ends_is_discarded(self):
        c = FakeCluster(self.ft, phases=("Executing",))
        live = self.live(c)
        live.wait_s = 0
        self.drive(live, max_runs=1)
        self.assertEqual(
            self.reload().runs[0]["discard_reason"], "no_terminal_incident"
        )


class ExampleConfigTest(unittest.TestCase):
    def test_the_example_cells_file_is_a_valid_queue(self):
        p = pathlib.Path(__file__).resolve().parents[1] / "batch-config.example.json"
        conf = json.loads(p.read_text())
        with tempfile.TemporaryDirectory() as d:
            q = batch.Queue.create(d, REF, conf["cells"], precheck_cfg=conf["precheck"])
            self.assertEqual([c["tier"] for c in q.doc["cells"]], [1, 1, 2, 2])
            self.assertEqual(
                q.doc["cells"][0]["valid_target"], 5
            )  # Tier 1 = 5 valid runs

    def test_init_through_the_command_line(self):
        p = pathlib.Path(__file__).resolve().parents[1] / "batch-config.example.json"
        with (
            tempfile.TemporaryDirectory() as d,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = batch.main(
                ["init", "--state", d + "/s", "--approval-ref", REF, "--cells", str(p)]
            )
            self.assertEqual(rc, 0)
            self.assertEqual(batch.main(["status", "--state", d + "/s"]), 0)


class NoFaultMetricTest(unittest.TestCase):
    def test_pre_check_windows_report_false_alarms_per_arm_and_unknown_is_not_clean(
        self,
    ):
        w = lambda alerts, incs: {"alerts": alerts, "incidents": incs}
        runs = [
            {"reasoner": "R1", "no_fault": w([], [])},
            {"reasoner": "R1", "no_fault": w([{"alertname": "NexusCpuAnomaly"}], [])},
            {"reasoner": "R1", "no_fault": w(None, [{"uid": "x"}])},
            {"reasoner": "R3", "no_fault": w([], [])},
            {"reasoner": "R3"},  # no window recorded: not counted
        ]
        m = metrics.pre_check_windows(runs)
        self.assertEqual(m["R1"]["windows"], 3)
        self.assertEqual(
            (m["R1"]["with_incident"]["k"], m["R1"]["with_incident"]["n"]), (1, 3)
        )
        self.assertEqual(
            (m["R1"]["with_alert"]["k"], m["R1"]["with_alert"]["n"]), (1, 2)
        )  # the unknown one is out
        self.assertEqual(m["R1"]["alerts_unknown"], 1)
        self.assertEqual(m["R3"]["with_alert"]["k"], 0)


if __name__ == "__main__":
    unittest.main()


class SevenPrechecksTest(unittest.TestCase):
    """The remaining spec section 20 pre-checks, each on its own and wired into run_prechecks."""

    def setUp(self):
        self.clock = FakeClock()
        self.ft = FakeTime(self.clock)
        self.cfg = precheck.normalise(PRE)

    def pc(self, cluster, level=3, stat=None, locust=True):
        r = runner_for(cluster, self.ft)
        if not locust:
            r.locust = None
        live = batch.LiveRun(
            r, PRE, monotonic=self.ft.monotonic, wall=self.ft.wall, sleep=self.ft.sleep
        )
        return precheck.run_prechecks(
            precheck.Prom(r), self.cfg, live.incidents, set(), batch.model.TERMINAL_PHASES, level,
            self.ft.monotonic, self.ft.wall, self.ft.sleep, stat or audit_fresh(self.ft),
        )  # fmt: skip

    def failing(self, mutate, **kw):
        c = FakeCluster(self.ft)
        mutate(c)
        pc = self.pc(c, **kw)
        self.assertFalse(pc.ok)
        self.assertIsNone(pc.stop_reason)  # a discard, not a halt
        return pc.failures

    def test_everything_clean_passes_on_the_first_poll(self):
        pc = self.pc(FakeCluster(self.ft))
        self.assertEqual((pc.ok, pc.polls, pc.failures), (True, 1, []))

    def test_every_check_is_read_only(self):
        c = FakeCluster(self.ft)
        self.pc(c)
        for argv in c.calls:
            if argv[0] != "kubectl":
                continue
            rest = argv[3:]
            if rest[0] == "-n":
                rest = rest[2:]
            self.assertEqual(
                rest[0], "get", argv
            )  # no patch, exec, label, delete, annotate, scale
        self.assertFalse(any("delete" in " ".join(a) for a in c.calls))

    def test_sample_api_needs_two_of_two_ready_and_updated(self):
        for sample in ((2, 1, 2), (2, 2, 1), (1, 1, 1), (3, 3, 3), (2, 0, 0)):
            f = self.failing(lambda c, s=sample: setattr(c, "sample", s))
            self.assertTrue(any(x.startswith("sample_api:") for x in f), (sample, f))

    def test_sample_api_with_no_answer_fails(self):
        c = FakeCluster(self.ft)
        r = runner_for(c, self.ft)
        r.exec = lambda argv, cwd=None: (
            (1, "") if "deployment" in argv else c(argv, cwd)
        )
        self.assertEqual(
            precheck.sample_api_failures(r, self.cfg), ["sample_api:no_answer"]
        )

    def test_killswitch_must_be_active(self):
        for state in ("halted", "", "ACTIVE", None):
            f = self.failing(lambda c, s=state: setattr(c, "killswitch", s))
            self.assertTrue(any(x.startswith("killswitch:") for x in f), (state, f))
        self.assertIn(
            "killswitch:missing", self.failing(lambda c: setattr(c, "killswitch", None))
        )  # absent = halted

    def test_namespace_level_is_the_cells_level(self):
        self.assertTrue(self.pc(FakeCluster(self.ft), level=3).ok)
        f = self.failing(lambda c: None, level=2)  # the label says 3, the cell wants 2
        self.assertIn("namespace_level:3!=2", f)

    def test_a_missing_level_label_is_l0(self):
        c = FakeCluster(self.ft)
        c.level_label = None
        self.assertTrue(self.pc(c, level=0).ok)
        self.assertIn(
            "namespace_level:0!=1",
            self.failing(lambda c: setattr(c, "level_label", None), level=1),
        )

    def test_backstage_must_have_no_pod(self):
        f = self.failing(lambda c: setattr(c, "backstage_pods", 1))
        self.assertIn("backstage:pods=1", f)

    def test_locust_must_be_running_at_the_baseline_within_ten_percent(self):
        for users, ok_ in (
            (160, True),
            (144, True),
            (176, True),
            (143, False),
            (177, False),
            (0, False),
        ):
            c = FakeCluster(self.ft)
            c.locust_doc = {"state": "running", "user_count": users}
            self.assertEqual(self.pc(c).ok, ok_, users)
        f = self.failing(
            lambda c: setattr(c, "locust_doc", {"state": "stopped", "user_count": 160})
        )
        self.assertIn("locust:state='stopped'", f)

    def test_locust_server_side_rate_is_checked_in_each_namespace(self):
        for rps, ok_ in ((72.0, True), (88.0, True), (71.9, False), (88.1, False)):
            c = FakeCluster(self.ft)
            c.rps = rps
            self.assertEqual(self.pc(c).ok, ok_, rps)
        f = self.failing(
            lambda c: c.prom.update({":rate2m": []})
        )  # no series: no answer is not at baseline
        self.assertIn("locust:nexus-dev:no_rate", f)
        self.assertIn("locust:nexus-prod:no_rate", f)

    def test_locust_without_a_url_or_an_answer_fails(self):
        self.assertIn(
            "locust:no_url", self.pc(FakeCluster(self.ft), locust=False).failures
        )
        c = FakeCluster(self.ft)
        r = runner_for(c, self.ft)
        r.http_get = lambda url: (0, "")
        live = batch.LiveRun(
            r, PRE, monotonic=self.ft.monotonic, wall=self.ft.wall, sleep=self.ft.sleep
        )
        pc = precheck.run_prechecks(precheck.Prom(r), self.cfg, live.incidents, set(), batch.model.TERMINAL_PHASES, 3,
                                    self.ft.monotonic, self.ft.wall, self.ft.sleep, audit_fresh(self.ft))  # fmt: skip
        self.assertIn("locust:no_answer", pc.failures)

    def test_reasoner_must_be_ready_with_the_expected_index_hash(self):
        ok = {"status": "ready", "corpusHash": INDEX_HASH}
        self.assertTrue(self.pc(self._with_reasoner(ok)).ok)
        other = "cd" * 32
        cases = {
            f"reasoner:index_hash={other}": {**ok, "corpusHash": other},
            "reasoner:not_ready": {**ok, "status": "loading"},
            "reasoner:no_index_hash": {"status": "ready"},
        }
        for want, doc in cases.items():
            self.assertIn(
                want, self.failing(lambda c, d=doc: setattr(c, "reasoner", d))
            )
        for bad in ("sha256:", "AB" * 32, "ab" * 31, 123, None):  # not a 64-hex hash
            f = self.failing(
                lambda c, b=bad: setattr(c, "reasoner", {**ok, "corpusHash": b})
            )
            self.assertIn("reasoner:no_index_hash", f, bad)
        self.assertIn(
            "reasoner:not_ready", self.failing(lambda c: setattr(c, "reasoner", None))
        )  # 5xx / unreachable
        # the old guesses are not the Reasoner's fields: `ready: true` or `indexHash` alone do not pass
        self.assertIn(
            "reasoner:not_ready",
            self.failing(
                lambda c: setattr(
                    c, "reasoner", {"ready": True, "corpusHash": INDEX_HASH}
                )
            ),
        )
        self.assertIn(
            "reasoner:no_index_hash",
            self.failing(
                lambda c: setattr(
                    c, "reasoner", {"status": "ready", "indexHash": INDEX_HASH}
                )
            ),
        )

    def _with_reasoner(self, doc):
        c = FakeCluster(self.ft)
        c.reasoner = doc
        return c

    def test_a_sha256_prefix_on_the_configured_hash_is_ignored(self):
        cfg = precheck.normalise(
            {**PRE, "reasoner": {"expected_index_hash": "sha256:" + INDEX_HASH}}
        )
        self.assertEqual(
            precheck.reasoner_failures(runner_for(FakeCluster(self.ft), self.ft), cfg),
            [],
        )

    def test_the_exact_response_of_the_reasoner_service_passes(self):
        """The shape comes from the producer itself: Reasoner.health() of reasoner/nexus_reasoner,
        built on the fixture runbooks, served through the fake proxy."""
        import sys

        root = pathlib.Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / "reasoner"))
        try:
            from nexus_reasoner import config as rcfg
            from nexus_reasoner.retrieval import Retriever
            from nexus_reasoner.service import Reasoner

            from .. import bm25
        finally:
            sys.path.remove(str(root / "reasoner"))
        index = bm25.Index.from_dir(
            pathlib.Path(__file__).parent / "fixtures" / "runbooks"
        )
        cfg = rcfg.parse({"indexPath": "x", "scoreFloor": 1.0, "models": {}})
        body = Reasoner(cfg, Retriever(index, 1.0), None).health()
        self.assertEqual(
            set(body), {"status", "corpusHash", "promptVersion", "scoreFloor"}
        )
        self.assertEqual(body["status"], "ready")
        self.assertRegex(
            body["corpusHash"], r"^[0-9a-f]{64}$"
        )  # bare hex, no sha256: prefix
        real = json.loads(json.dumps(body))  # what crosses the wire
        c = FakeCluster(self.ft)
        c.reasoner = real
        good = precheck.normalise(
            {**PRE, "reasoner": {"expected_index_hash": index.corpus_hash}}
        )
        self.assertEqual(precheck.reasoner_failures(runner_for(c, self.ft), good), [])
        bad = precheck.normalise(
            {**PRE, "reasoner": {"expected_index_hash": "00" * 32}}
        )
        self.assertEqual(
            precheck.reasoner_failures(runner_for(c, self.ft), bad),
            [f"reasoner:index_hash={index.corpus_hash}"],
        )

    def test_reasoner_is_read_through_the_service_proxy_get(self):
        c = FakeCluster(self.ft)
        self.pc(c)
        raw = [a for a in c.calls if "--raw" in a and "healthz" in a[-1]]
        self.assertEqual(
            raw[0][-1],
            "/api/v1/namespaces/nexus-reasoner/services/http:nexus-reasoner:8080/proxy/healthz",
        )
        self.assertEqual(raw[0][:3], ["kubectl", "--context", "ctx"])

    def test_audit_log_must_be_readable_and_recently_written(self):
        t = self.ft
        self.assertTrue(
            self.pc(FakeCluster(t), stat=lambda p: t.wall() - 60).ok
        )  # exactly at the limit
        f = self.failing(lambda c: None, stat=lambda p: t.wall() - 61)
        self.assertIn("audit_log:stale=61s", f)

        def unreadable(path):
            raise PermissionError(path)

        self.assertIn(
            "audit_log:unreadable", self.failing(lambda c: None, stat=unreadable)
        )

    def test_the_audit_log_check_reads_the_configured_path_and_sends_no_probe(self):
        seen = []
        c = FakeCluster(self.ft)
        self.pc(c, stat=lambda p: seen.append(p) or self.ft.wall())
        self.assertEqual(seen, ["/var/log/nexus-audit/audit.log"])
        self.assertFalse(
            any("dry-run" in " ".join(a) or "create" in a for a in c.calls)
        )

    def test_one_failing_check_blocks_the_run_and_nothing_is_injected(self):
        for mutate in (
            lambda c: setattr(c, "sample", (2, 1, 2)),
            lambda c: setattr(c, "killswitch", "halted"),
            lambda c: setattr(c, "backstage_pods", 2),
            lambda c: setattr(c, "reasoner", None),
        ):
            with tempfile.TemporaryDirectory() as d:
                q = batch.Queue.create(d, REF, CELLS, precheck_cfg=PRE)
                c = FakeCluster(self.ft)
                mutate(c)
                live = batch.LiveRun(runner_for(c, self.ft), PRE, sleep=self.ft.sleep, monotonic=self.ft.monotonic,
                                     wall=self.ft.wall, stat=audit_fresh(self.ft))  # fmt: skip
                batch.drive(
                    q,
                    live,
                    clock=self.clock,
                    execute=True,
                    approval_ref=REF,
                    max_runs=1,
                )
                r = q.runs[0]
                self.assertEqual(
                    (r["status"], r["discard_reason"]),
                    ("discarded", "precheck_timeout"),
                )
                self.assertFalse(c.injected)
                self.assertTrue(r["precheck"]["failures"])

    def test_configuration_needs_the_locust_baseline_and_the_index_hash(self):
        base = {k: v for k, v in PRE.items() if k not in ("locust", "reasoner")}
        for bad in (
            base,
            {**base, "locust": {"baseline_users": 160}, "reasoner": PRE["reasoner"]},
            {**base, "locust": PRE["locust"]},
            {**base, "locust": PRE["locust"], "reasoner": {"expected_index_hash": ""}},
            {
                **base,
                "locust": PRE["locust"],
                "reasoner": {"expected_index_hash": "sha256:index-1"},
            },  # not 64 hex
            {
                **base,
                "locust": PRE["locust"],
                "reasoner": {"expected_index_hash": "REPLACE_WITH_THE_G16_INDEX_HASH"},
            },
            {**PRE, "locust": {**PRE["locust"], "tolerance": 0}},
            {**PRE, "audit_log": {"max_age_s": -1}},
            {**PRE, "sample_api": {"replicas": 0}},
        ):
            with self.assertRaises(precheck.ConfigError, msg=bad):
                precheck.normalise(bad)
        n = precheck.normalise(PRE)  # the spec's names fill in
        self.assertEqual((n["sample_api"]["replicas"], n["killswitch"]["configmap"], n["backstage"]["namespace"]),
                         (2, "nexus-killswitch", "backstage"))  # fmt: skip
        self.assertEqual(n["audit_log"]["path"], "/var/log/nexus-audit/audit.log")
        self.assertEqual(n["locust"]["tolerance"], 0.10)
