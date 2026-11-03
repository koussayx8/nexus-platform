"""Unattended batch driver for the M4 runs (re-plan "Unattended runs"; spec §20, §25). Stdlib only.

  python3 -m experiments.harness.batch init   --state DIR --approval-ref REF --cells FILE [--max-pause-gap S]
  python3 -m experiments.harness.batch status --state DIR
  python3 -m experiments.harness.batch run    --state DIR --approval-ref REF --context C \\
          --locust URL --git-dir DIR [--execute]    (from the repository root)

What it adds to runner.py (which plans and executes single operations):

* A queue on disk: DIR/queue.json, rewritten atomically (temp file, fsync, rename) after every
  state change, so a kill at any point leaves the previous or the next state, never half of one.
* Resume after a reboot: `run` starts by recovering. A run still `running` in the file was cut off:
  it is voided (`boot_id_changed` when the boot_id differs from the one it started under, else
  `interrupted`) and the cell requeues it. A systemd unit (systemd/nexus-batch.service) starts `run`
  again at logon; installing it is the owner's step.
* The timed-run rule (ADR-026/028): boot_id and the pause gap (realtime delta minus monotonic delta)
  are read at the start of every run and checked between its steps and at its end; a change voids
  the run, whatever else it measured, and the cell requeues it.
* Per cell: stop at 10 valid runs or 15 attempts. Every attempt counts, valid, void or discarded;
  nothing is retried for free. A cell that reaches 15 attempts is `exhausted`, and reported as such.
* fallbackUsed (spec §17) is on every run record.

Mutations: nothing mutates without --execute AND an approval reference. The reference is the one
the pre-registration (G16) names for the whole batch; `init` stores it, `run` must be given the same
string, and the placeholder in the unit file is refused. Without --execute `run` prints what it would
do next and writes nothing. A STOP file in DIR halts the loop between runs.

Exit codes: 0 queue finished or STOP; 2 refused (bad arguments, approval); 3 stopped by an error
(the run is recorded `discarded`, the batch does not carry on unattended).

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

from . import runner as runner_mod
from .operator_lib import model

QUEUE_VERSION = 1
VALID_TARGET = 10
MAX_ATTEMPTS = 15
MAX_PAUSE_GAP_S = (
    1.0  # the M2-1 void rule V2; the pre-registration fixes the batch's value
)
PLACEHOLDER_REF = "REPLACE_WITH_G16_APPROVAL_REF"
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@-]{2,127}$")
STATUSES = ("running", "valid", "void", "discarded")


class Refused(Exception):
    """The batch will not run: bad queue, bad approval, bad arguments."""


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


class Queue:
    """DIR/queue.json: cells in order, and one record per attempt."""

    def __init__(self, path: Path, doc: dict):
        self.path, self.doc = Path(path), doc

    @classmethod
    def create(
        cls, state_dir, approval_ref, cells, max_pause_gap=MAX_PAUSE_GAP_S
    ) -> Queue:
        check_ref(approval_ref)
        ids = [c["cell"] for c in cells]
        if not cells or len(ids) != len(set(ids)):
            raise Refused("cells must be a non-empty list with unique 'cell' ids")
        for c in cells:
            for k in ("cell", "scenario", "reasoner", "level"):
                if k not in c:
                    raise Refused(f"cell {c.get('cell')!r} lacks {k!r}")
        path = Path(state_dir) / "queue.json"
        if path.exists():
            raise Refused(f"{path} exists: a queue is never overwritten")
        q = cls(
            path,
            {
                "version": QUEUE_VERSION,
                "approval_ref": approval_ref,
                "created": _utc(),
                "valid_target": VALID_TARGET,
                "max_attempts": MAX_ATTEMPTS,
                "max_pause_gap_s": max_pause_gap,
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

    def cell_runs(self, cell: str) -> list[dict]:
        return [r for r in self.runs if r["cell"] == cell]

    def cell_state(self, cell: str) -> dict:
        rs = self.cell_runs(cell)
        valid = sum(r["status"] == "valid" for r in rs)
        n = len(rs)
        if valid >= self.doc["valid_target"]:
            status = "done"
        elif n >= self.doc["max_attempts"]:
            status = "exhausted"
        else:
            status = "open"
        return {
            "cell": cell,
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
        """The first open cell, in the order given: a cell is finished before the next starts."""
        for c in self.doc["cells"]:
            st = self.cell_state(c["cell"])
            if st["status"] == "open" and not st["running"]:
                return c
        return None

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
    """Run cells until the queue is finished, a STOP file appears, or a run fails.
    run_one(cell, rec, guard) -> Outcome; it may raise Voided via guard.check()."""
    if not execute:
        raise Refused("--execute is required to run")
    check_ref(approval_ref)
    if approval_ref != queue.doc["approval_ref"]:
        raise Refused(
            "approval reference differs from the one this queue was created under"
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
    """One run against the cluster, through runner.Runner: pre-check, reset, inject, wait for the
    Incident to end, read fallbackUsed. Every step is a mutation the pre-registration approves
    (S1-S6 injection, the §20 reset). Deleting Incidents is NOT done here (not in the G16 list).
    Live check pending; the tests run it against a fake exec."""

    def __init__(
        self,
        runner: runner_mod.Runner,
        wait_s: int = 1200,
        poll_s: int = 10,
        sleep=time.sleep,
    ):
        self.r, self.wait_s, self.poll_s, self.sleep = runner, wait_s, poll_s, sleep

    def incidents(self) -> list[dict]:
        rc, out = self.r.exec(
            [
                runner_mod.CLI,
                "--context",
                self.r.context,
                "-n",
                model.NAMESPACE,
                "get",
                model.PLURAL,
                "-o",
                "json",
            ]
        )
        if rc != 0:
            raise RuntimeError("cannot list Incidents")
        return json.loads(out).get("items", [])

    def precheck(self) -> str | None:
        """None when the cheap pre-checks hold, else why not. The Z-score and node-load checks of
        spec §20 need Prometheus and are not here (live check pending, next PR)."""
        if any(
            (i.get("status") or {}).get("phase") not in model.TERMINAL_PHASES
            for i in self.incidents()
        ):
            return "non_terminal_incident"
        return None

    def __call__(self, cell: dict, rec: dict, guard: Guard) -> Outcome:
        r = self.r
        before = {i["metadata"]["uid"] for i in self.incidents()}
        r.execute(r.plan_reset(int(cell["level"]), cell.get("baseline_ref")))
        guard.check()
        if not r.wait_synced():
            return Outcome(False, discard_reason="applications_not_synced")
        why = self.precheck()
        if why:
            return Outcome(False, discard_reason=why)
        guard.check()
        r.execute(
            r.plan_inject(cell["scenario"], cell.get("s3_patch"), cell.get("users"))
        )
        deadline = time.monotonic() + self.wait_s
        while time.monotonic() < deadline:
            guard.check()
            new = [i for i in self.incidents() if i["metadata"]["uid"] not in before]
            ended = [
                i
                for i in new
                if (i.get("status") or {}).get("phase") in model.TERMINAL_PHASES
            ]
            if ended:
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
        "cells": [queue.cell_state(c["cell"]) for c in queue.doc["cells"]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("init", "status", "run"))
    ap.add_argument("--state", required=True)
    ap.add_argument("--approval-ref")
    ap.add_argument("--cells", help="JSON list of cells (init)")
    ap.add_argument("--max-pause-gap", type=float, default=MAX_PAUSE_GAP_S)
    ap.add_argument("--context")
    ap.add_argument("--locust")
    ap.add_argument("--git-dir")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--max-runs", type=int)
    a = ap.parse_args(argv)
    try:
        if a.mode == "init":
            cells = json.loads(Path(a.cells).read_text(encoding="utf-8"))
            Queue.create(a.state, a.approval_ref, cells, a.max_pause_gap)
            print(f"queue created in {a.state}")
            return 0
        queue = Queue.load(a.state)
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
        live = LiveRun(runner_mod.Runner(a.context, locust=a.locust, git_dir=a.git_dir))
        out = drive(
            queue,
            live,
            execute=True,
            approval_ref=a.approval_ref,
            stop_file=Path(a.state) / "STOP",
            max_runs=a.max_runs,
        )
        print(json.dumps(out))
        return 3 if str(out["stopped"]).startswith("error:") else 0
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
