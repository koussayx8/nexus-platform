"""A rig for the act path: a fake cluster, a recorder (fake or real), clocks and a tick helper.
All ticks run on one event loop, so a real asyncpg pool can be used across them."""

import asyncio

from fakes import T0
from fakes_act import (
    ACTIVE,
    BASELINE,
    Clock,
    FakeCluster,
    FakeMetrics,
    FakeRecorder,
)
from nexus_operator.act import ActPath
from nexus_operator.evidence import EvidenceCollector
from nexus_operator.executor import Executor
from nexus_operator.gate import Gate
from nexus_operator.model import iso
from nexus_operator.verifier import Verifier

TERMINAL = {"Resolved", "Escalated", "Blocked", "Recommended", "Dismissed"}


class Rig:
    def __init__(
        self,
        *,
        replicas=2,
        level="3",
        advisory=True,
        killswitch=ACTIVE,
        metrics=None,
        recorder=None,
        collector=False,
    ):
        self.loop = asyncio.new_event_loop()
        self.api = FakeCluster(replicas, level, killswitch)
        self.rec = recorder if recorder is not None else FakeRecorder()
        self.api.events = []
        if isinstance(self.rec, FakeRecorder):
            self.rec.order = self.api.events
        self.clock = Clock()
        self.metrics = metrics or FakeMetrics()
        gate = Gate(self.api, self.api, self.rec, advisory=advisory, clock=self.clock)
        self.act = ActPath(
            self.api,
            self.api,  # the fake cluster also takes the Scale and Eviction requests
            self.rec,
            gate,
            Executor(self.api, self.rec, self.clock),
            Verifier(self.api, self.metrics),
            self.clock,
            EvidenceCollector(self.api, self.metrics, self.clock)
            if collector
            else None,
        )

    def call(self, coro):
        return self.loop.run_until_complete(coro)

    def proposed(self, doc, level=3, baseline=BASELINE, name="inc"):
        status = {
            "phase": "Proposed",
            "autonomyLevel": level,
            "timestamps": {"proposed": iso(self.clock())},
            "evidence": {"baseline": baseline},
            "proposal": {"hash": f"sha256:{name}"},
        }
        self.api.put(name, created=T0, status=status)
        digest = f"sha256:{name}"
        if isinstance(self.rec, FakeRecorder):
            self.rec.proposals[digest] = doc
        else:  # a real Writer: the incident row first, then the proposal under its hash
            uid = f"uid-{name}"
            self.call(
                self.rec.open_incident(uid, "nexus-dev/sample-api", "detector", T0)
            )
            self.call(
                self.rec.record_proposal(
                    digest, uid, "rules", True, parsed_proposal=doc
                )
            )
        return name

    def inc(self, name="inc") -> dict:
        return self.api.incidents[name]

    def status(self, name="inc") -> dict:
        return self.inc(name)["status"]

    def tick(self, name="inc", seconds=0.0):
        self.clock.advance(seconds)
        self.call(self.act.step(self.inc(name)))

    def run(self, name="inc", ticks=40, seconds=5.0):
        """Tick until the Incident is terminal; returns the number of ticks used."""
        for i in range(ticks):
            if self.status(name)["phase"] in TERMINAL:
                return i
            self.tick(name, seconds)
        return ticks


def ended(rig, name="inc"):
    s = rig.status(name)
    return s["phase"], s.get("reason")


def verify(rig, *, seconds=130, step=10):
    for _ in range(seconds // step):
        if rig.status()["phase"] != "Verifying":
            break
        rig.tick(seconds=step)
