"""Unattended batch driver for the M4 runs (re-plan "Unattended runs"; spec §20, §25). Stdlib only.

  python3 -m experiments.harness.batch init   --state DIR --approval-ref REF --cells FILE [--max-pause-gap S]
  python3 -m experiments.harness.batch status --state DIR
  python3 -m experiments.harness.batch clear-halt --state DIR      (the owner's acknowledgement)
  python3 -m experiments.harness.batch run    --state DIR --approval-ref REF --context C \\
          --locust URL --git-dir DIR [--execute]    (from the repository root)

What it adds to runner.py (which plans and executes single operations):

* A queue on disk: DIR/queue.json, rewritten atomically (temp file, fsync, rename) after every
  state change, so a kill at any point leaves the previous or the next state, never half of one.
* Tiers. Each cell carries its tier, valid-run target and attempt cap from the cells file; G16 sets
  them (Tier 1 = 5 valid runs), there are no defaults. Tier 1 runs before Tier 2; inside a tier the
  open cell with the fewest valid runs goes next (ties: declaration order), so stopping at any point
  leaves equal repetitions everywhere (within one run per tier). A cell stops at its target (done) or
  its cap (exhausted, reported); every attempt counts, valid, void or discarded.
* Resume after a reboot: `run` starts by recovering. A run still `running` in the file was cut off:
  it is voided (`boot_id_changed` when the boot_id differs from the one it started under, else
  `interrupted`) and its cell requeues it. A systemd unit (systemd/nexus-batch.service) starts `run`
  again at logon; installing it is the owner's step.
* The timed-run rule (ADR-026/028): boot_id and the pause gap (realtime delta minus monotonic delta)
  are read at the start of every run and checked between its steps and at its end; a change voids
  the run, whatever else it measured, and the cell requeues it.
* Pre-checks (precheck.py): the Prometheus clean-baseline check on baseline_stddev15m, the node-load
  check, and no non-terminal Incident, all inside the 20 min cap. The window records the alerts and
  Incidents that fired with nothing injected, in the run record (`no_fault`). Incidents are never
  deleted: one still open at the cap stops the batch (`halt`, reason recorded) until `clear-halt`.
* Locust recovery: when the pre-check finds Locust stopped or off baseline and precheck.locust has
  spawn_rate and classes, the driver POSTs the exact baseline (users, spawn rate, classes) to the master,
  waits recovery_wait_s (default cap_s), and re-runs every pre-check; at most recovery_attempts (default 2)
  in a row (a successful run resets the count), each logged and kept on the run record (`locust_recovery`). Past the cap the batch halts
  (`locust_recovery_cap_reached`).
* End-of-run Locust check: before a run counts as valid, precheck.locust_failures is re-run; a Locust that
  is not running at the baseline (S2/S4/S6: at the cell's users) voids the run (`locust_end_check`) and the
  cell requeues it.
* The operator's advisoryChecks and breakerEpoch are written at every reset (plan_operator_config);
  "off" only for a phase D cell.
* fallbackUsed (spec §17) is on every run record.

Mutations: nothing mutates without --execute AND an approval reference. The reference is the one
the pre-registration (G16) names for the whole batch; `init` stores it, `run` must be given the same
string, and the placeholder in the unit file is refused. Without --execute `run` prints what it would
do next and writes nothing. A STOP file in DIR halts the loop between runs.

Exit codes: 0 queue finished or STOP; 2 refused (bad arguments, approval, halted); 3 stopped by an
error or a halt (the run is recorded `discarded`, the batch does not carry on unattended).

Live behaviour: live check pending (no cluster in the session that wrote this).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import precheck
from . import runner as runner_mod
from .operator_lib import model

QUEUE_VERSION = 2
MAX_PAUSE_GAP_S = (
    1.0  # the M2-1 void rule V2; the pre-registration fixes the batch's value
)
PLACEHOLDER_REF = "REPLACE_WITH_G16_APPROVAL_REF"
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@-]{2,127}$")
STATUSES = ("running", "valid", "void", "discarded")


class Refused(Exception):
    """The batch will not run: bad queue, bad approval, bad arguments."""


class StopBatch(Exception):
    """Raised from inside a run: the batch must not go on (e.g. an Incident outlived the pre-check cap).
    The run is recorded `discarded` with this reason and the queue is marked halted."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Voided(Exception):
    """Raised from inside a run (guard.check) when the timed-run rule is broken."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- clock and the timed-run rule


def read_clock() -> dict:
    """One sample: boot_id and the two clocks. A WSL2 VM pause moves realtime, not monotonic."""
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
        boot = f.read().strip()
    return {
        "boot_id": boot,
        "realtime": time.clock_gettime(time.CLOCK_REALTIME),
        "monotonic": time.clock_gettime(time.CLOCK_MONOTONIC),
    }


def pause_gap(a: dict, b: dict) -> float:
    return (b["realtime"] - a["realtime"]) - (b["monotonic"] - a["monotonic"])


def void_reason(start: dict, now: dict, max_gap: float) -> str | None:
    if now["boot_id"] != start["boot_id"]:
        return "boot_id_changed"
    if abs(pause_gap(start, now)) > max_gap:
        return "pause_gap"
    return None


class Guard:
    """The timed-run rule for one run: start() at its beginning, check() between steps."""

    def __init__(self, clock=read_clock, max_gap: float = MAX_PAUSE_GAP_S):
        self.clock, self.max_gap, self.start = clock, max_gap, clock()

    def check(self) -> dict:
        now = self.clock()
        why = void_reason(self.start, now, self.max_gap)
        if why:
            raise Voided(why)
        return now


# ---------------------------------------------------------------- the queue on disk


def _utc(t: float | None = None) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(
        time.time() if t is None else t, datetime.timezone.utc
    ).isoformat()


def validate_cells(cells) -> list[dict]:
    """Every cell names its scenario, arm, level, tier, valid-run target and attempt cap."""
    if not isinstance(cells, list) or not cells:
        raise Refused("cells must be a non-empty list")
    ids = [c.get("cell") if isinstance(c, dict) else None for c in cells]
    if None in ids or len(ids) != len(set(ids)):
        raise Refused("every cell needs a unique 'cell' id")
    for c in cells:
        for k in (
            "scenario",
            "reasoner",
            "level",
            "tier",
            "valid_target",
            "max_attempts",
        ):
            if k not in c:
                raise Refused(f"cell {c['cell']!r} lacks {k!r}")
        for k in ("tier", "valid_target", "max_attempts"):
            if not isinstance(c[k], int) or isinstance(c[k], bool) or c[k] < 1:
                raise Refused(f"cell {c['cell']!r}: {k} must be an integer >= 1")
        if c["max_attempts"] < c["valid_target"]:
            raise Refused(f"cell {c['cell']!r}: max_attempts is below valid_target")
        adv = c.get("advisory_checks", "on")
        if adv not in ("on", "off"):
            raise Refused(f"cell {c['cell']!r}: advisory_checks must be on or off")
        if adv == "off" and c.get("phase") != "D":
            raise Refused(
                f"cell {c['cell']!r}: advisory_checks off is for phase D only"
            )
    return cells


class Queue:
    """DIR/queue.json: cells in order, and one record per attempt."""

    def __init__(self, path: Path, doc: dict):
        self.path, self.doc = Path(path), doc

    @classmethod
    def create(
        cls,
        state_dir,
        approval_ref,
        cells,
        max_pause_gap=MAX_PAUSE_GAP_S,
        precheck_cfg=None,
    ) -> Queue:
        """Targets, caps and tiers come from `cells` (G16 sets them); there are no defaults."""
        check_ref(approval_ref)
        cells = validate_cells(cells)
        try:
            pre = precheck.normalise(precheck_cfg)
        except precheck.ConfigError as e:
            raise Refused(str(e)) from None
        path = Path(state_dir) / "queue.json"
        if path.exists():
            raise Refused(f"{path} exists: a queue is never overwritten")
        q = cls(
            path,
            {
                "version": QUEUE_VERSION,
                "approval_ref": approval_ref,
                "created": _utc(),
                "max_pause_gap_s": max_pause_gap,
                "precheck": pre,
                "halt": None,
                "cells": cells,
                "runs": [],
            },
        )
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        q.save()
        return q

    @classmethod
    def load(cls, state_dir) -> Queue:
        path = Path(state_dir) / "queue.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Refused(f"cannot read {path}: {e}") from None
        if doc.get("version") != QUEUE_VERSION:
            raise Refused(f"queue version {doc.get('version')!r} != {QUEUE_VERSION}")
        return cls(path, doc)

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.doc, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)  # atomic on POSIX
        dfd = os.open(self.path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    # -- reading
    @property
    def runs(self) -> list[dict]:
        return self.doc["runs"]

    def cell(self, cell: str) -> dict:
        return next(c for c in self.doc["cells"] if c["cell"] == cell)

    def cell_runs(self, cell: str) -> list[dict]:
        return [r for r in self.runs if r["cell"] == cell]

    def cell_state(self, cell: str) -> dict:
        rs = self.cell_runs(cell)
        valid = sum(r["status"] == "valid" for r in rs)
        n = len(rs)
        c = self.cell(cell)
        if valid >= c["valid_target"]:
            status = "done"
        elif n >= c["max_attempts"]:
            status = "exhausted"
        else:
            status = "open"
        return {
            "cell": cell,
            "tier": c["tier"],
            "target": c["valid_target"],
            "cap": c["max_attempts"],
            "status": status,
            "valid": valid,
            "attempts": n,
            "void": sum(r["status"] == "void" for r in rs),
            "discarded": sum(r["status"] == "discarded" for r in rs),
            "running": sum(r["status"] == "running" for r in rs),
        }

    def export_runs(self) -> list[dict]:
        """Attempt records shaped for metrics: reasoner and scenario from the cell, `discarded`
        true for everything that is not a valid run (void and discarded attempts are reported
        per cell by metrics.discards, never hidden)."""
        by = {c["cell"]: c for c in self.doc["cells"]}
        return [
            {
                **r,
                "reasoner": by[r["cell"]]["reasoner"],
                "scenario": by[r["cell"]]["scenario"],
                "discarded": r["status"] != "valid",
                "discard_reason": r["void_reason"] or r["discard_reason"],
            }
            for r in self.runs
            if r["status"] != "running"
        ]

    def next_cell(self) -> dict | None:
        """Tier 1 before Tier 2 (and so on); within a tier, round-robin: the open cell with the
        fewest valid runs, then the order given (so a void or discarded run is requeued at once). Stopping at any point
        therefore leaves every cell of a tier within one valid run of the others. A tier is open
        until all its cells are done or exhausted; a cell that is exhausted is reported, not waited on."""
        states = [(c, self.cell_state(c["cell"])) for c in self.doc["cells"]]
        for tier in sorted({c["tier"] for c in self.doc["cells"]}):
            open_ = [
                (i, c, st)
                for i, (c, st) in enumerate(states)
                if c["tier"] == tier and st["status"] == "open"
            ]
            if any(st["running"] for _, _, st in open_):
                return None  # a run is in flight (recover() clears it); never start a second one
            if open_:
                return min(open_, key=lambda t: (t[2]["valid"], t[0]))[1]
        return None

    def clear_halt(self) -> dict | None:
        """The owner's acknowledgement of a halt. The old halt stays in `halts`."""
        h = self.doc.get("halt")
        if h:
            self.doc.setdefault("halts", []).append({**h, "cleared_at": _utc()})
            self.doc["halt"] = None
            self.save()
        return h

    # -- writing (each one is saved)
    def begin(self, cell: dict, clock: dict) -> dict:
        n = len(self.cell_runs(cell["cell"])) + 1
        rec = {
            "run_id": f"{cell['cell']}#{n}",
            "cell": cell["cell"],
            "attempt": n,
            "status": "running",
            "approval_ref": self.doc["approval_ref"],
            "started_at": _utc(),
            "ended_at": None,
            "boot_id_start": clock["boot_id"],
            "boot_id_end": None,
            "pause_gap_s": None,
            "void_reason": None,
            "discard_reason": None,
            "fallbackUsed": False,
            "terminal": None,
        }
        self.runs.append(rec)
        self.save()
        return rec

    def finish(self, rec: dict, status: str, **fields) -> None:
        assert status in STATUSES and status != "running"
        rec.update(status=status, ended_at=_utc(), **fields)
        self.save()

    def recover(self, now: dict) -> list[dict]:
        """Void every run left `running` by a stop or a reboot. They stay in the file and count as
        attempts; the cell requeues by being open again."""
        out = []
        for r in self.runs:
            if r["status"] == "running":
                why = (
                    "boot_id_changed"
                    if now["boot_id"] != r["boot_id_start"]
                    else "interrupted"
                )
                r.update(
                    status="void",
                    void_reason=why,
                    ended_at=_utc(),
                    boot_id_end=now["boot_id"],
                )
                out.append(r)
        if out:
            self.save()
        return out


def check_ref(ref: str | None) -> None:
    if not ref or ref == PLACEHOLDER_REF or not REF_RE.match(ref):
        raise Refused(
            f"approval reference {ref!r} is empty, the placeholder, or malformed"
        )


# ---------------------------------------------------------------- the loop


@dataclass
class Outcome:
    """What one run reports. valid=False needs a discard_reason (e.g. precheck_timeout)."""

    valid: bool
    terminal: str | None = None
    fallback_used: bool = False
    discard_reason: str | None = None
    notes: dict = field(default_factory=dict)


def drive(
    queue: Queue,
    run_one,
    clock=read_clock,
    execute=False,
    approval_ref=None,
    stop_file: Path | None = None,
    max_runs: int | None = None,
) -> dict:
    """Run cells until the queue is finished, a STOP file appears, or a run fails or halts the batch.
    run_one(cell, rec, guard) -> Outcome; it may raise Voided via guard.check()."""
    if not execute:
        raise Refused("--execute is required to run")
    check_ref(approval_ref)
    if approval_ref != queue.doc["approval_ref"]:
        raise Refused(
            "approval reference differs from the one this queue was created under"
        )
    if queue.doc.get("halt"):
        raise Refused(
            f"the batch is halted ({queue.doc['halt']['reason']}); clear it with clear-halt"
        )
    summary = {"recovered": [], "ran": 0, "stopped": None}
    summary["recovered"] = [r["run_id"] for r in queue.recover(clock())]
    max_gap = queue.doc["max_pause_gap_s"]
    while True:
        if stop_file is not None and stop_file.exists():
            summary["stopped"] = "STOP"
            return summary
        if max_runs is not None and summary["ran"] >= max_runs:
            summary["stopped"] = "max_runs"
            return summary
        cell = queue.next_cell()
        if cell is None:
            summary["stopped"] = "finished"
            return summary
        guard = Guard(clock, max_gap)
        rec = queue.begin(cell, guard.start)
        summary["ran"] += 1
        try:
            out = run_one(cell, rec, guard)
            end = guard.check()  # the end check decides, whatever the run measured
        except Voided as v:
            end = clock()
            queue.finish(
                rec,
                "void",
                void_reason=v.reason,
                boot_id_end=end["boot_id"],
                pause_gap_s=pause_gap(guard.start, end),
            )
            continue
        except StopBatch as s:
            end = clock()
            queue.finish(rec, "discarded", discard_reason=s.reason, boot_id_end=end["boot_id"],
                         pause_gap_s=pause_gap(guard.start, end))  # fmt: skip
            queue.doc["halt"] = {
                "reason": s.reason,
                "run_id": rec["run_id"],
                "at": _utc(),
            }
            queue.save()
            summary["stopped"] = f"halt:{s.reason}"
            return summary
        except Exception as e:  # noqa: BLE001 - unattended: record whatever it was, then stop
            end = clock()
            queue.finish(
                rec,
                "discarded",
                discard_reason=f"runner_error:{type(e).__name__}",
                boot_id_end=end["boot_id"],
            )
            summary["stopped"] = f"error:{type(e).__name__}: {e}"
            return summary
        gap = pause_gap(guard.start, end)
        common = {
            "boot_id_end": end["boot_id"],
            "pause_gap_s": gap,
            "terminal": out.terminal,
            "fallbackUsed": bool(out.fallback_used),
        }
        if out.valid:
            queue.finish(rec, "valid", **common)
        else:
            queue.finish(
                rec,
                "discarded",
                discard_reason=out.discard_reason or "unspecified",
                **common,
            )


# ---------------------------------------------------------------- the live run


class LiveRun:
    """One run against the cluster, through runner.Runner: reset (with the operator's advisoryChecks
    and breakerEpoch), wait for Applications, pre-checks, inject, wait for the Incident to end, read
    fallbackUsed. Every mutation is one the pre-registration approves (S1-S6 injection, the §20
    reset, the advisoryChecks toggle), and `drive` runs nothing without the approval reference.
    Incidents are NEVER deleted. Live check pending; the tests run it against a fake exec."""

    def __init__(
        self,
        runner: runner_mod.Runner,
        precheck_cfg: dict,
        wait_s: int = 1200,
        poll_s: int = 10,
        sleep=time.sleep,
        monotonic=time.monotonic,
        wall=time.time,
        stat=precheck._file_mtime,
    ):
        self.stat = stat
        self.r, self.cfg = runner, precheck.normalise(precheck_cfg)
        self.wait_s, self.poll_s = wait_s, poll_s
        self.sleep, self.monotonic, self.wall = sleep, monotonic, wall
        self.recoveries = 0  # consecutive Locust restarts; a successful run resets it

    def incidents(self) -> list[dict]:
        rc, out = self.r.exec(
            [runner_mod.CLI, "--context", self.r.context, "-n", model.NAMESPACE]
            + ["get", model.PLURAL, "-o", "json"]
        )
        if rc != 0:
            raise RuntimeError("cannot list Incidents")
        return json.loads(out).get("items", [])

    # ---- Locust recovery: restart the baseline swarm when the pre-check finds Locust stopped or off baseline
    def _recovery_conf(self) -> dict | None:
        """The baseline to restart with, from the config (precheck.locust); None when the config does
        not give spawn_rate and classes (then there is no recovery and a Locust failure discards as before)."""
        loc = self.cfg["locust"]
        if "spawn_rate" not in loc or "classes" not in loc:
            return None
        return {
            "users": int(loc["baseline_users"]),
            "spawn_rate": loc["spawn_rate"],
            "classes": list(loc["classes"]),
            "max": int(loc.get("recovery_attempts", 2)),
            "wait_s": loc.get("recovery_wait_s", self.cfg["cap_s"]),
        }

    def _locust_baseline(self) -> dict | None:
        """The swarm the reset leaves Locust at (users, spawn_rate, classes), or None without the config."""
        conf = self._recovery_conf()
        return (
            {k: conf[k] for k in ("users", "spawn_rate", "classes")} if conf else None
        )

    @staticmethod
    def _locust_down(failures: list[str]) -> list[str]:
        """Failures that mean Locust is stopped or off baseline (not 'no answer' or 'no url')."""
        return [
            f
            for f in failures
            if f.startswith(("locust:state=", "locust:users="))
            or (f.startswith("locust:nexus-") and ":rps=" in f)
        ]

    def _recover_locust(self, rec: dict, why: list[str]) -> None:
        """One recovery attempt: POST the baseline to the master, then wait the clean-baseline window.
        Raises StopBatch when the per-batch cap is reached."""
        conf = self._recovery_conf()
        done = rec.setdefault("locust_recovery", [])
        if conf is None or self.recoveries >= conf["max"]:
            raise StopBatch(
                "locust_recovery_cap_reached"
                if conf
                else "locust_recovery_unconfigured"
            )
        self.recoveries += 1
        entry = {
            "attempt": self.recoveries,
            "of": conf["max"],
            "why": why,
            "at": _utc(self.wall()),
        }
        try:
            self.r.http_post(
                f"{self.r.locust.rstrip('/')}/swarm",
                {
                    "user_count": [str(conf["users"])],
                    "spawn_rate": [str(conf["spawn_rate"])],
                    "user_classes": conf["classes"],
                },
            )
            entry["posted"] = True
        except OSError as e:
            entry["posted"] = False
            entry["error"] = f"{type(e).__name__}"
        done.append(entry)
        print(
            f"batch: locust recovery {entry['attempt']}/{conf['max']}: {why}; posted={entry['posted']}",
            file=sys.stderr,
        )
        self.sleep(conf["wait_s"])

    def _prechecks(self, rec: dict, before: set, cell: dict, guard: Guard):
        """run_prechecks, restarting Locust first when it is already down and again when the pre-check
        fails on it. Every pre-check is re-run after each restart."""
        force = None
        while True:
            down = force or (
                self._recovery_conf() is not None
                and self._locust_down(
                    precheck.locust_failures(self.r, precheck.Prom(self.r), self.cfg)
                )
            )
            if down:
                self._recover_locust(rec, down)
                guard.check()
            force = None
            pc = precheck.run_prechecks(
                precheck.Prom(self.r),
                self.cfg,
                self.incidents,
                before,
                model.TERMINAL_PHASES,
                int(cell["level"]),
                monotonic=self.monotonic,
                wall=self.wall,
                sleep=self.sleep,
                stat=self.stat,
            )
            if pc.ok or pc.stop_reason or self._recovery_conf() is None:
                return pc
            force = self._locust_down(pc.failures) or None
            if not force:
                return pc

    def locust_end_failures(self, cell: dict) -> list[str]:
        """precheck.locust_failures at the end of a run. S2/S4/S6 drive their own load, so for them
        the target is the cell's users (no per-namespace rate: the fault load is not the baseline);
        every other scenario is held to the pre-registered baseline users and rate."""
        cfg = self.cfg
        if cell["scenario"].upper() in ("S2", "S4", "S6"):
            loc = {
                **cfg["locust"],
                "baseline_users": cell.get("users"),
                "namespaces": [],
            }
            cfg = {**cfg, "locust": loc}
        return precheck.locust_failures(self.r, precheck.Prom(self.r), cfg)

    def __call__(self, cell: dict, rec: dict, guard: Guard) -> Outcome:
        r = self.r
        advisory = cell.get("advisory_checks", "on")
        epoch = int(self.wall())
        rec["advisory_checks"], rec["breaker_epoch"] = advisory, epoch
        before = {i["metadata"]["uid"] for i in self.incidents()}
        r.execute(
            r.plan_reset(
                int(cell["level"]),
                cell.get("baseline_ref"),
                advisory,
                epoch,
                locust_baseline=self._locust_baseline(),
            )
        )
        guard.check()
        if not r.wait_synced():
            return Outcome(False, discard_reason="applications_not_synced")
        pc = self._prechecks(rec, before, cell, guard)
        # the no-fault record and the pre-check outcome go on the run record, kept whatever follows
        rec["no_fault"] = pc.window
        rec["precheck"] = {
            "ok": pc.ok,
            "polls": pc.polls,
            "waited_s": pc.waited_s,
            "failures": pc.failures,
        }
        if pc.stop_reason:
            raise StopBatch(pc.stop_reason)
        if not pc.ok:
            return Outcome(False, discard_reason="precheck_timeout")
        guard.check()
        before = {
            i["metadata"]["uid"] for i in self.incidents()
        }  # false alarms are not this run's
        r.execute(
            r.plan_inject(cell["scenario"], cell.get("s3_patch"), cell.get("users"))
        )
        deadline = self.monotonic() + self.wait_s
        while self.monotonic() < deadline:
            guard.check()
            new = [i for i in self.incidents() if i["metadata"]["uid"] not in before]
            ended = [
                i
                for i in new
                if (i.get("status") or {}).get("phase") in model.TERMINAL_PHASES
            ]
            if ended:
                # the end check: Locust must still be running at the load the run was measured at
                why = self.locust_end_failures(cell)
                rec["locust_end"] = {"ok": not why, "failures": why}
                if why:
                    raise Voided("locust_end_check")
                self.recoveries = 0  # the cap counts consecutive recoveries: a successful run clears it
                st = ended[0]["status"]
                return Outcome(
                    True,
                    st["phase"],
                    bool((st.get("reasoner") or {}).get("fallbackUsed")),
                    notes={"incident": ended[0]["metadata"]["uid"]},
                )
            self.sleep(self.poll_s)
        return Outcome(False, discard_reason="no_terminal_incident")


# ---------------------------------------------------------------- command line


def _status(queue: Queue) -> dict:
    return {
        "approval_ref": queue.doc["approval_ref"],
        "halt": queue.doc.get("halt"),
        "cells": [queue.cell_state(c["cell"]) for c in queue.doc["cells"]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("init", "status", "run", "clear-halt"))
    ap.add_argument("--state", required=True)
    ap.add_argument("--approval-ref")
    ap.add_argument(
        "--cells", help='JSON file (init): {"precheck": {...}, "cells": [...]}'
    )
    ap.add_argument("--max-pause-gap", type=float, default=MAX_PAUSE_GAP_S)
    ap.add_argument("--context")
    ap.add_argument("--locust")
    ap.add_argument("--git-dir")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--max-runs", type=int)
    a = ap.parse_args(argv)
    try:
        if a.mode == "init":
            conf = json.loads(Path(a.cells).read_text(encoding="utf-8"))
            if not isinstance(conf, dict):
                raise Refused(
                    'the cells file must be {"precheck": {...}, "cells": [...]}'
                )
            Queue.create(
                a.state,
                a.approval_ref,
                conf.get("cells"),
                a.max_pause_gap,
                conf.get("precheck"),
            )
            print(f"queue created in {a.state}")
            return 0
        queue = Queue.load(a.state)
        if a.mode == "clear-halt":
            print(json.dumps({"cleared": queue.clear_halt()}))
            return 0
        if a.mode == "status":
            print(json.dumps(_status(queue), indent=1))
            return 0
        if not a.context:
            raise Refused("--context is required")
        if not a.execute:  # dry: say what would run, change nothing
            cell = queue.next_cell()
            print(
                json.dumps(
                    {"dry_run": True, "next_cell": cell, "status": _status(queue)},
                    indent=1,
                )
            )
            return 0
        live = LiveRun(
            runner_mod.Runner(a.context, locust=a.locust, git_dir=a.git_dir),
            queue.doc["precheck"],
        )
        out = drive(
            queue,
            live,
            execute=True,
            approval_ref=a.approval_ref,
            stop_file=Path(a.state) / "STOP",
            max_runs=a.max_runs,
        )
        print(json.dumps(out))
        return 3 if str(out["stopped"]).startswith(("error:", "halt:")) else 0
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
