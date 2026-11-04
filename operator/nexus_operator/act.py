"""The act path (spec §4, §6, §7): Proposed -> Gate -> Executing -> Verifying -> terminal.

One call to ActPath.step() moves one Incident by at most one step, level-triggered: it derives
the next step from the status and the observed cluster, never from an event. Phases it owns:

  Proposed      stages 1-3 for every proposal; abstain, escalate, no_action and the levels
                L1 (Recommended) end here; at L2 stages 4-6 run and a pass moves to
                AwaitingApproval; at L3 stages 4-7 run and a pass moves to Executing
  AwaitingApproval  the Approval Gate (§7, §9 stage 8): no decision waits, up to approvalTTL, then
                Expired; a rejection ends Rejected; an approval that does not pin the stored
                proposal ends Blocked; an unexpired one that does is re-validated (stages 1-7,
                the dry-run included, which K3 only admits now) and moves to Executing
  Executing     one request per call, write-ahead; a restart evicts one pod at a time and waits
                for the replacement to be Ready; a scale is one request
  Verifying     the catalogue's criteria against the raw baseline (verifier.py)
  Compensating  scale back, itself a gated request (kill switch, dry-run); restart has none

Detected and Diagnosing belong to the Evidence Collector and the Reasoner path, which are not part
of this change: the act path starts at a Proposed Incident whose proposal/v1 document the
recorder holds, found by status.proposal.hash (ActRecorder.get_proposal).

A completion write that failed while the Incident was still in an act phase is retried from
status.execution.completion on the next steps. One that fails on the step that ends the Incident
stays incomplete: a terminal Incident is never written again (§7), and reconciliation flags the
row without calling it an unexplained request (§17).

Crash safety (§4 idempotency): status.execution.pending carries the idempotency key BEFORE the
intent row and the request. On resume the real replica count or pod UID says whether the request
took effect; if it did, nothing is sent twice. Every status write after a request is retried on a
409, because the request cannot be unsent.

status.execution = {startedAt, plan, seq, idempotencyKey, auditIds, evicted, pending, result,
completion, compensation, error}; status.verification = the verifier's state.
"""

import asyncio
import dataclasses
import logging

from . import approval, proposal, rules
from .catalogue import CATALOGUE
from .evidence import CollectionError, EvidenceCollector
from .executor import Executed, Executor
from .gate import Gate, GateResult, live_pods, pod_ready, selector_of
from .killswitch import CONFIGMAP as KILLSWITCH
from .killswitch import state_of
from .kube import TRANSPORT_ERRORS, ApiError, Conflict, KubeApi
from .metrics import (
    ACTIONS,
    DIAGNOSE_COUNT,
    DIAGNOSE_SUM,
    GATE,
    INCIDENTS,
    METRICS,
    REASONER_CALLS,
    REASONER_FALLBACKS,
    REDACTIONS,
)
from .model import iso, parse_time
from .mutations import Mutations
from .reasoner_client import ReasonerClient
from .reasoner_client import Result as ReasonerResult
from .recorder import ActionIntent, ActRecorder, RecorderUnavailable
from .redact import redact_bundle
from .verifier import Verifier

logger = logging.getLogger("nexus.act")

ACT_PHASES = frozenset(
    {
        "Detected",
        "Diagnosing",
        "Proposed",
        "AwaitingApproval",
        "Executing",
        "Verifying",
        "Compensating",
    }
)
DIAGNOSING_TIMEOUT_S = 60

# D9 (§7), every edge a non-terminal phase may take. _move refuses any other, so the invariant
# "Verifying leads only to Resolved or Compensating" (I11) holds in code and not only in the
# spec: an illegal move is logged and not written, and the Incident stays where it is.
LEGAL = {
    "Detected": {"Recorded", "Diagnosing", "Escalated"},
    "Diagnosing": {"Proposed", "Escalated", "Blocked"},
    "Proposed": {
        "Recommended",
        "Dismissed",
        "Escalated",
        "Blocked",
        "AwaitingApproval",
        "Executing",
    },
    "AwaitingApproval": {"Executing", "Rejected", "Expired", "Blocked"},
    "Executing": {"Verifying", "Blocked", "Escalated"},
    "Verifying": {"Resolved", "Compensating"},
    "Compensating": {"Escalated"},
}
PROPOSED_TIMEOUT_S = 30
COMPENSATION_TIMEOUT_S = 120
WRITE_ATTEMPTS = 3

# §7 phase order; a phase appears at most once per Incident, so its index is the `transitions` seq.
PHASE_SEQ = {
    p: i
    for i, p in enumerate(
        [
            "Detected",
            "Diagnosing",
            "Proposed",
            "AwaitingApproval",
            "Executing",
            "Verifying",
            "Compensating",
        ]
    )
}
TERMINAL_SEQ = 7
OWNER = {
    "Detected": "Incident Reconciler",
    "Diagnosing": "Evidence Collector",
    "Proposed": "Safety Gate",
    "AwaitingApproval": "Approval Gate",
    "Executing": "Action Executor",
    "Verifying": "Verifier",
    "Compensating": "Compensator",
}


@dataclasses.dataclass
class Diagnosis:
    """One Incident's in-flight diagnosis: the redacted bundle and the Reasoner call. Kept in
    memory between ticks so a retry never asks the Reasoner twice; a restart starts it again."""

    bundle: dict
    counts: dict
    baseline: dict
    task: "asyncio.Task | None" = None
    counted: bool = False
    fell_back: bool = False


@dataclasses.dataclass
class Answer:
    doc: dict | None
    errors: list[str]
    mode: str
    meta: dict
    result: ReasonerResult | None
    fallback_used: bool
    latency_ms: int | None
    digest: str = ""

    @property
    def request_id(self) -> str | None:
        return self.result.request_id if self.result is not None else None


def _level(status: dict) -> int:
    v = status.get("autonomyLevel")
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 3 else 0


class ActPath:
    def __init__(
        self,
        api: KubeApi,
        mutations: Mutations,
        recorder: ActRecorder,
        gate: Gate,
        executor: Executor,
        verifier: Verifier,
        clock,
        collector: EvidenceCollector | None = None,
        reasoner: ReasonerClient | None = None,
        approval_ttl_s: int = 900,
    ) -> None:
        self.approval_ttl_s = approval_ttl_s
        self.api, self.mutations, self.recorder, self.gate = (
            api,
            mutations,
            recorder,
            gate,
        )
        self.executor, self.verifier, self.clock = executor, verifier, clock
        self.collector = collector  # None: an Incident stays Detected (M1b behaviour)
        self.reasoner = reasoner  # None: Diagnosing is R1 only
        self._diagnoses: dict[str, Diagnosis] = {}

    # ---- status writes -------------------------------------------------------------------
    async def _write(
        self, incident: dict, status: dict, *, must: bool = False
    ) -> dict | None:
        """Merge-patch status with the held resourceVersion. A 409 returns None (re-read next
        tick), or with must=True re-reads and retries: used after a request has been sent."""
        name = incident["metadata"]["name"]
        rv = incident["metadata"]["resourceVersion"]
        for _ in range(WRITE_ATTEMPTS if must else 1):
            try:
                return await self.api.patch_status(name, status, rv)
            except Conflict:
                if not must:
                    return None
                fresh = await self.api.get_incident(name)
                if fresh is None:
                    return None
                rv = fresh["metadata"]["resourceVersion"]
        raise Conflict(
            409, "PATCH", f"{name}/status", "gave up after repeated conflicts"
        )

    async def _move(
        self,
        incident: dict,
        phase: str,
        reason: str | None = None,
        *,
        must: bool = False,
        **fields,
    ) -> dict | None:
        """Phase change: one status patch, then one transition row."""
        now = self.clock()
        old = (incident.get("status") or {}).get("phase")
        if phase not in LEGAL.get(old, ()):
            logger.error(
                "refused illegal transition %s %s -> %s",
                incident["metadata"]["name"],
                old,
                phase,
            )
            return None
        terminal = phase not in PHASE_SEQ
        stamp = "terminal" if terminal else phase.lower()
        status = {"phase": phase, "timestamps": {stamp: iso(now)}, **fields}
        if reason is not None:
            status["reason"] = reason
        written = await self._write(incident, status, must=must)
        if written is None:
            return None
        if terminal:
            METRICS.inc(INCIDENTS, phase=phase, reason=reason or "")
        try:
            uid = incident["metadata"]["uid"]
            await self.recorder.record_transition(
                uid,
                TERMINAL_SEQ if terminal else PHASE_SEQ[phase],
                old,
                phase,
                now,
                OWNER.get(old, "Incident Reconciler"),
                reason,
            )
            if terminal:
                await self.recorder.close_incident(uid, phase, reason or phase)
        except RecorderUnavailable:
            logger.warning(
                "transition row for %s -> %s not recorded",
                incident["metadata"]["name"],
                phase,
            )
        logger.info(
            "act %s %s -> %s %s", incident["metadata"]["name"], old, phase, reason or ""
        )
        return written

    # ---- entry point ---------------------------------------------------------------------
    async def step(self, incident: dict) -> None:
        phase = (incident.get("status") or {}).get("phase")
        if ((incident.get("status") or {}).get("execution") or {}).get("completion"):
            incident = await self._retry_completion(incident)
            if incident is None:
                return
        handler = {
            "Detected": self._detected,
            "Diagnosing": self._diagnosing,
            "Proposed": self._proposed,
            "AwaitingApproval": self._awaiting,
            "Executing": self._executing,
            "Verifying": self._verifying,
            "Compensating": self._compensating,
        }.get(phase)
        if handler is not None:
            await handler(incident)

    # ---- Detected and Diagnosing ---------------------------------------------------------
    async def _detected(self, incident: dict) -> None:
        """L1-L3 with the intake done: open the recorder row and enter Diagnosing."""
        status = incident["status"]
        if (
            self.collector is None
            or "autonomyLevel" not in status
            or _level(status) == 0
        ):
            return  # L0 ends Recorded in the reconciler; no collector means no Diagnosing
        if any(((status.get("kopf") or {}).get("progress") or {}).values()):
            return  # the intake handler is still pending
        try:
            await self._open(incident)
            await self.recorder.record_transition(
                incident["metadata"]["uid"],
                PHASE_SEQ["Detected"],
                None,
                "Detected",
                parse_time(incident["metadata"]["creationTimestamp"]),
                OWNER["Detected"],
                None,
            )
        except RecorderUnavailable:
            return  # retried next tick; the reconciler's 20 s Detected timeout bounds the wait
        await self._move(incident, "Diagnosing")

    async def _diagnosing(self, incident: dict) -> None:
        """Collect, redact, store, ask the Reasoner (if configured), and move to Proposed.

        Order matters (Q1): the raw bundle exists only inside the collection step; the redacted
        copy is what is hashed, stored, sent to the Reasoner and given to R1. The Reasoner call
        (30 s, one attempt) runs as a task, so a slow Reasoner never stalls the reconcile loop or
        its liveness probe; a later tick collects the answer. On a timeout, a provider error, a
        422 or an invalid proposal, R1 answers once with fallbackUsed = true, and an invalid R1
        proposal ends the Incident Blocked (§6 transitions 4-6). Failures before the answer retry
        each tick until the 60 s Diagnosing timeout: an unreadable target ends Escalated /
        evidence_error, an unreachable recorder ends Blocked / recorder_unavailable.
        """
        status = incident["status"]
        uid = incident["metadata"]["uid"]
        now = self.clock()
        started = parse_time(
            (status.get("timestamps") or {}).get("diagnosing") or iso(now)
        )
        timed_out = (now - started).total_seconds() > DIAGNOSING_TIMEOUT_S
        work = self._diagnoses.get(uid)
        if work is None:
            try:
                await self._open(incident)
                raw, baseline = await self.collector.collect(incident)
            except RecorderUnavailable:
                if timed_out:
                    await self._move(incident, "Blocked", "recorder_unavailable")
                return
            except (CollectionError, ApiError, *TRANSPORT_ERRORS) as e:
                logger.warning("evidence for %s: %s", incident["metadata"]["name"], e)
                if timed_out:
                    await self._move(incident, "Escalated", "evidence_error")
                return
            redacted, counts = redact_bundle(raw)
            bundle = redacted | {"redaction": {"counts": counts}}
            bundle["hash"] = proposal.hash_of(bundle)
            work = Diagnosis(bundle, counts, baseline)
            if self.reasoner is not None:
                work.task = asyncio.create_task(
                    self.reasoner.propose(
                        bundle, incident_uid=uid, level=_level(status)
                    )
                )
            self._diagnoses[uid] = work
            if work.task is not None:
                return  # the answer is collected on a later tick
        if work.task is not None and not work.task.done():
            if (
                timed_out
            ):  # the client's own 30 s timeout should have fired long before this
                work.task.cancel()
                self._diagnoses.pop(uid, None)
                await self._move(incident, "Escalated", "reasoner_unavailable")
            return
        answer = self._answer(work)
        if answer.result is not None and answer.result.reason == "not_redacted":
            self._diagnoses.pop(
                uid, None
            )  # nothing was sent: redaction failed (§6 transition 3)
            await self._move(incident, "Escalated", "evidence_error")
            return
        try:
            await self._store(uid, work, answer, now)
        except RecorderUnavailable:
            if timed_out:
                self._diagnoses.pop(uid, None)
                await self._move(incident, "Blocked", "recorder_unavailable")
            return  # the answer is kept: the next tick stores it, and asks nobody again
        self._diagnoses.pop(uid, None)
        if answer.errors:
            await self._move(incident, "Blocked", "schema_invalid")
            return
        for cls, n in work.counts.items():
            if n:
                METRICS.inc(REDACTIONS, n, **{"class": cls})
        doc = answer.doc
        action = doc["action"]
        written = await self._move(
            incident,
            "Proposed",
            evidence={
                "bundleHash": work.bundle["hash"],
                "redactions": sum(work.counts.values()),
                "baseline": work.baseline,
            },
            reasoner=self._reasoner_status(answer),
            proposal={
                "hash": answer.digest,
                "actionId": action["id"],
                "parameters": action["parameters"],
                "faultClass": doc["hypothesis"]["faultClass"],
                "confidence": doc["confidence"],
                "abstain": doc["abstain"],
            },
        )
        if written is not None:
            METRICS.inc(DIAGNOSE_SUM, (self.clock() - started).total_seconds())
            METRICS.inc(DIAGNOSE_COUNT)

    @staticmethod
    def _reasoner_status(answer: "Answer") -> dict:
        """status.reasoner (§8). Without a Reasoner it is exactly what R1-only Diagnosing wrote."""
        if answer.result is None:
            return {"mode": "rules", "fallbackUsed": False}
        meta = answer.meta
        status = {
            "mode": answer.mode,
            "modelId": meta.get("model"),
            "promptVersion": meta.get("promptVersion"),
            "indexHash": meta.get("corpusHash"),
            "chunks": meta.get("retrievedChunks"),
            "latencyMs": answer.latency_ms,
            "requestId": answer.request_id,
            "fallbackUsed": answer.fallback_used,
        }
        return {k: v for k, v in status.items() if v is not None}

    def _answer(self, work: "Diagnosis") -> "Answer":
        """The proposal this diagnosis ends with: the Reasoner's if it passed, else R1's once."""
        result = None
        if work.task is not None:
            if work.task.cancelled() or work.task.exception() is not None:
                result = ReasonerResult(False, "none", "transport")
            else:
                result = work.task.result()
        if result is None:  # no Reasoner configured: today's R1-only behaviour
            doc = rules.propose(work.bundle)
            return Answer(doc, proposal.validate(doc), "rules", {}, None, False, None)
        if result.ok:
            # The client has validated the answer; the operator checks it again here, so nothing
            # reaches the Gate that proposal.py has not accepted, whatever the client says.
            errors = proposal.validate(result.doc)
            if errors:
                result = dataclasses.replace(
                    result, ok=False, reason="schema_invalid", errors=errors
                )
        if not work.counted:
            METRICS.inc(REASONER_CALLS, result="ok" if result.ok else result.reason)
            work.counted = True
        if result.ok:
            return Answer(
                result.doc,
                [],
                str(result.meta.get("mode") or self.reasoner.mode),
                result.meta,
                result,
                False,
                result.latency_ms,
            )
        if result.reason == "not_redacted":
            return Answer(None, [], "rules", {}, result, False, None)
        if not work.fell_back:
            METRICS.inc(REASONER_FALLBACKS)
            work.fell_back = True
        doc = rules.propose(work.bundle)  # R1 once; fallbackUsed = true
        return Answer(
            doc, proposal.validate(doc), "rules", {}, result, True, result.latency_ms
        )

    async def _store(self, uid: str, work: "Diagnosis", answer: "Answer", now) -> None:
        """Every row of this diagnosis, idempotently: the bundle, a failed Reasoner attempt if
        there was one, and the proposal that is used (or the invalid one that ended it)."""
        bundle = work.bundle
        await self.recorder.record_bundle(
            bundle["hash"], uid, "evidence/v1", bundle, work.counts, now
        )
        result = answer.result
        if result is not None and not result.ok:
            reason = result.reason + (f": {result.errors[0]}" if result.errors else "")
            await self.recorder.record_proposal(
                proposal.hash_of({"requestId": result.request_id, "failure": reason}),
                uid,
                self.reasoner.mode,
                False,
                model_id=self.reasoner.model or None,
                raw_output=result.raw,
                parsed_proposal=result.doc,
                latency_ms=result.latency_ms,
                fallback_used=False,
                request_id=result.request_id,
                reject_reason=reason[:500],
            )
        meta = answer.meta
        answer.digest = proposal.hash_of(answer.doc)
        await self.recorder.record_proposal(
            answer.digest,
            uid,
            answer.mode,
            not answer.errors,
            model_id=meta.get("model"),
            prompt_version=meta.get("promptVersion"),
            index_hash=meta.get("corpusHash"),
            retrieved_chunk_ids=meta.get("retrievedChunks") or (),
            raw_output=result.raw if result is not None and result.ok else None,
            parsed_proposal=answer.doc,
            latency_ms=answer.latency_ms,
            fallback_used=answer.fallback_used,
            request_id=answer.request_id,
            reject_reason="; ".join(answer.errors)[:500] or None,
        )

    # ---- Proposed ------------------------------------------------------------------------
    async def _proposed(self, incident: dict) -> None:
        status = incident["status"]
        uid = incident["metadata"]["uid"]
        since = parse_time(
            (status.get("timestamps") or {}).get("proposed") or iso(self.clock())
        )
        if (self.clock() - since).total_seconds() > PROPOSED_TIMEOUT_S:
            await self._move(incident, "Escalated", "gate_timeout")
            return
        digest = (status.get("proposal") or {}).get("hash")
        if not digest:
            return  # no proposal recorded yet; the 30 s timeout bounds the wait
        try:
            await self._open(incident)
            doc = await self.recorder.get_proposal(digest)
        except RecorderUnavailable:
            return  # retried next tick, until the 30 s timeout
        if doc is None:
            return
        level = _level(status)
        if not proposal.validate(doc) and doc["abstain"]:
            await self._move(incident, "Escalated", "abstain")
            return
        # L3 acts; L2 runs stages 4-6 and leaves the dry-run (K3 refuses it unapproved) to stage 8
        upto = {3: 7, 2: 6}.get(level, 3)
        result = await self.gate.run(incident, doc, upto=upto, level=level)
        await self._record_gate(uid, result)
        gate = result.status()
        if not result.passed:
            await self._move(incident, result.phase, result.reason, gate=gate)
            return
        action = doc["action"]
        entry = (
            CATALOGUE[action["id"]]
            if self.gate.advisory
            else CATALOGUE.get(action["id"])
        )
        if entry is None:
            await self._move(incident, "Blocked", "not_in_catalogue", gate=gate)
            return
        if action["id"] == "escalate":
            rec = (doc.get("escalation") or {}).get("recommendation")
            await self._move(
                incident,
                "Escalated",
                "proposed_escalation",
                gate=gate,
                proposal={"escalation": doc["escalation"], "recommendation": rec},
            )
        elif action["id"] == "no_action":
            if level == 1:
                await self._move(incident, "Recommended", "level_recommend", gate=gate)
            else:
                await self._move(incident, "Dismissed", "no_action_required", gate=gate)
        elif level == 1:
            await self._move(incident, "Recommended", "level_recommend", gate=gate)
        elif level == 2:
            await self._move(
                incident,
                "AwaitingApproval",
                gate=gate | {"verdict": "requires_approval", "dryRun": "pending"},
            )
        elif level != 3:
            await self._move(incident, "Blocked", "autonomy_denied", gate=gate)
        else:
            await self._begin(incident, doc, gate)

    async def _awaiting(self, incident: dict) -> None:
        """Stage 8: wait for spec.approval, judge it, re-validate, then execute (§7, §9)."""
        status = incident["status"]
        at = self.clock()
        if approval.waited_out(incident, at, self.approval_ttl_s):
            await self._move(
                incident, "Expired", "approval_expired", gate=self._gate_note(status)
            )
            return
        digest = (status.get("proposal") or {}).get("hash")
        if not digest or not (incident.get("spec") or {}).get("approval"):
            return
        try:
            await self._open(incident)
            doc = await self.recorder.get_proposal(digest)
        except RecorderUnavailable:
            return  # retried next tick; approvalTTL and the approval's own expiry still bound it
        if not isinstance(doc, dict) or proposal.validate(doc):
            await self._move(incident, "Blocked", "schema_invalid")
            return
        verdict = approval.check(incident, doc, at, self.approval_ttl_s)
        if verdict.state == "pending":
            return
        if verdict.phase is not None:
            logger.info("approval %s: %s", verdict.state, verdict.detail)
            await self._move(
                incident,
                verdict.phase,
                verdict.reason,
                gate=self._gate_note(status, verdict.detail),
            )
            return
        result = await self.gate.run(incident, doc, upto=7, level=2)
        await self._record_gate(incident["metadata"]["uid"], result)
        if not result.passed:
            await self._move(
                incident, result.phase, result.reason, gate=result.status()
            )
            return
        await self._begin(incident, doc, result.status() | {"verdict": "approved"})

    @staticmethod
    def _gate_note(status: dict, detail: str = "") -> dict:
        gate = dict(status.get("gate") or {})
        return gate | ({"detail": detail} if detail else {})

    async def _open(self, incident: dict) -> None:
        """The `incidents` row every other row refers to; idempotent."""
        spec = incident["spec"]
        target = spec["target"]
        await self.recorder.open_incident(
            incident["metadata"]["uid"],
            f"{target['namespace']}/{target['name']}",
            spec["source"]["type"],
            parse_time(spec["detectedAt"]),
            (incident["metadata"].get("labels") or {}).get("nexus.io/run-id"),
        )

    async def _record_gate(self, uid: str, result: GateResult) -> None:
        for d in result.decisions:
            if d.verdict == "skipped":
                continue  # the table holds pass and fail only
            METRICS.inc(GATE, stage=str(d.stage), verdict=d.verdict)
            try:
                await self.recorder.record_gate_decision(
                    uid, d.stage, d.verdict, {"detail": d.detail}, self.clock()
                )
            except RecorderUnavailable:
                logger.warning("gate decision for stage %d not recorded", d.stage)

    async def _begin(self, incident: dict, doc: dict, gate: dict) -> None:
        """Gate passed at L3: record the plan and enter Executing. No request is sent here."""
        target = incident["spec"]["target"]
        ns, name = target["namespace"], target["name"]
        action = doc["action"]
        plan: dict = {
            "actionId": action["id"],
            "proposalHash": incident["status"]["proposal"]["hash"],
        }
        if action["id"] == "scale_deployment":
            scale = await self.api.get_scale(ns, name)
            if scale is None:
                await self._move(incident, "Blocked", "precondition_failed", gate=gate)
                return
            plan |= {"replicas": action["parameters"]["replicas"]}
            plan["previous"] = (scale.get("spec") or {}).get("replicas", 1)
        else:
            deployment = await self.api.get_deployment(ns, name)
            pods = live_pods(
                await self.api.list_pods(ns, selector_of(deployment or {}))
            )
            plan["pods"] = sorted(
                (
                    {"name": p["metadata"]["name"], "uid": p["metadata"]["uid"]}
                    for p in pods
                ),
                key=lambda p: p["name"],
            )
        await self._move(
            incident,
            "Executing",
            gate=gate,
            execution={
                "startedAt": iso(self.clock()),
                "plan": plan,
                "seq": 0,
                "auditIds": [],
            },
        )

    # ---- Executing -----------------------------------------------------------------------
    async def _executing(self, incident: dict) -> None:
        exe = incident["status"]["execution"]
        plan = exe["plan"]
        entry = CATALOGUE[plan["actionId"]]
        target = incident["spec"]["target"]
        ns, name = target["namespace"], target["name"]
        pending = exe.get("pending")
        if pending:
            if await self._applied(ns, name, pending):
                await self._settle_unconfirmed(incident, pending)
                return
            # Not applied. Its key may already be recorded, and a recorded key is never sent
            # again, so the request goes out under the next key (the old row stays incomplete
            # and reconciliation flags it).
            seq = exe.get("seq", 0) + 1
            pending = {**pending, "key": f"{incident['metadata']['name']}-{seq}"}
            written = await self._write(
                incident,
                {
                    "execution": {
                        "idempotencyKey": pending["key"],
                        "seq": seq,
                        "pending": pending,
                    }
                },
            )
            if written is not None:
                await self._issue(written, pending)
            return
        started = parse_time(exe["startedAt"])
        if (self.clock() - started).total_seconds() > entry.execute_timeout_s:
            await self._move(
                incident,
                "Escalated",
                "execution_timeout",
                execution={"result": "timeout"},
            )
            return
        if (
            self.gate.advisory
            and state_of(await self.api.get_configmap(KILLSWITCH)) != "active"
        ):
            # advisory, per request: a halt stops a restart between two evictions (K4 is the
            # authoritative copy, M3)
            await self._move(
                incident, "Blocked", "killswitch", execution={"result": "killswitch"}
            )
            return
        seq = exe.get("seq", 0) + 1
        key = f"{incident['metadata']['name']}-{seq}"
        if plan["actionId"] == "scale_deployment":
            nxt = {
                "key": key,
                "kind": "scale",
                "replicas": plan["replicas"],
                "previous": plan["previous"],
            }
        else:
            nxt = await self._next_eviction(incident, ns, name, plan, exe, key)
            if nxt is None:
                return
            if nxt == "done":
                await self._to_verifying(incident, exe)
                return
        written = await self._write(
            incident, {"execution": {"idempotencyKey": key, "seq": seq, "pending": nxt}}
        )
        if written is None:
            return
        await self._issue(written, nxt)

    async def _next_eviction(self, incident, ns, name, plan, exe, key):
        """The next pending eviction, "done", or None while the replacement is not Ready."""
        deployment = await self.api.get_deployment(ns, name)
        if deployment is None:
            await self._move(
                incident,
                "Escalated",
                "execution_timeout",
                execution={"result": "error"},
            )
            return None
        pods = live_pods(await self.api.list_pods(ns, selector_of(deployment)))
        evicted = exe.get("evicted") or []
        want = (deployment.get("spec") or {}).get("replicas", 1)
        present = {p["metadata"]["uid"] for p in pods}
        for orig in plan["pods"]:
            if orig["uid"] not in evicted and orig["uid"] in present:
                if evicted and not (
                    len(pods) == want and all(pod_ready(p) for p in pods)
                ):
                    return None  # one at a time: the replacement must be Ready first
                return {
                    "key": key,
                    "kind": "evict",
                    "pod": orig["name"],
                    "uid": orig["uid"],
                }
        return "done"

    async def _applied(self, ns: str, name: str, pending: dict) -> bool:
        """Did the pending request already take effect? Judged from the real cluster state."""
        if pending["kind"] == "scale":
            scale = await self.api.get_scale(ns, name)
            return (
                scale is not None
                and (scale.get("spec") or {}).get("replicas") == pending["replicas"]
            )
        deployment = await self.api.get_deployment(ns, name)
        pods = await self.api.list_pods(ns, selector_of(deployment or {}))
        for p in pods:
            if p["metadata"]["uid"] == pending["uid"]:
                return bool(p["metadata"].get("deletionTimestamp"))
        return True

    def _intent(
        self, incident: dict, pending: dict, compensation: bool = False
    ) -> ActionIntent:
        plan = (incident["status"].get("execution") or {}).get("plan") or {}
        if pending["kind"] == "scale":
            action_id, params = "scale_deployment", {"replicas": pending["replicas"]}
            previous = {"replicas": pending["previous"]}
        else:
            action_id, params = "restart_workload", {"pod": pending["pod"]}
            previous = {"pod": pending["pod"], "uid": pending["uid"]}
        return ActionIntent(
            idempotency_key=pending["key"],
            incident_uid=incident["metadata"]["uid"],
            action_id=plan.get("actionId", action_id) if compensation else action_id,
            parameters=params,
            action_class="compensation" if compensation else "executed",
            previous_state=previous,
            intent_at=self.clock(),
        )

    async def _send(
        self, incident: dict, pending: dict, compensation: bool = False
    ) -> Executed:
        target = incident["spec"]["target"]
        ns, name = target["namespace"], target["name"]
        intent = self._intent(incident, pending, compensation)
        if pending["kind"] == "scale":
            done = await self.executor.scale(intent, ns, name, pending["replicas"])
        else:
            done = await self.executor.evict(intent, ns, pending["pod"], pending["uid"])
        METRICS.inc(ACTIONS, action_id=intent.action_id, result=done.result)
        return done

    def _completion(self, pending: dict, done: Executed) -> dict | None:
        if not done.unrecorded:
            return None
        return {
            "key": pending["key"],
            "requestAt": iso(self.clock()),
            "auditId": done.audit_id,
            "httpStatus": done.http_status,
            "outcome": done.outcome,
        }

    async def _issue(self, incident: dict, pending: dict) -> None:
        """Send the pending request and record the outcome."""
        done = await self._send(incident, pending)
        if done.result == "replayed":
            return  # recorded already: next tick judges it from the cluster, never re-sends
        exe = incident["status"]["execution"]
        audit = [
            *(exe.get("auditIds") or []),
            *([done.audit_id] if done.audit_id else []),
        ]
        base = {"auditIds": audit, "pending": None, "idempotencyKey": pending["key"]}
        completion = self._completion(pending, done)
        if completion:
            base["completion"] = completion
        if done.result == "admitted":
            evicted = [
                *(exe.get("evicted") or []),
                *([pending["uid"]] if pending["kind"] == "evict" else []),
            ]
            base |= {"result": "admitted", "evicted": evicted}
            if pending["kind"] == "scale":
                await self._to_verifying(incident, exe | base, must=True, extra=base)
            else:
                await self._write(incident, {"execution": base}, must=True)
            return
        base["result"] = done.result
        if done.detail:
            base["error"] = done.detail[:300]
        phase = "Escalated" if done.result == "error" else "Blocked"
        await self._move(incident, phase, done.reason, must=True, execution=base)

    async def _settle_unconfirmed(self, incident: dict, pending: dict) -> None:
        """The request took effect but its outcome was never stored: close the row, move on."""
        exe = incident["status"]["execution"]
        try:
            await self.recorder.complete_action(
                pending["key"], self.clock(), None, None, "applied_unconfirmed"
            )
        except RecorderUnavailable:
            pass  # the row stays incomplete; reconciliation flags it (§17)
        evicted = [
            *(exe.get("evicted") or []),
            *([pending["uid"]] if pending["kind"] == "evict" else []),
        ]
        base = {"pending": None, "result": "admitted", "evicted": evicted}
        if pending["kind"] == "scale":
            await self._to_verifying(incident, exe | base, must=True, extra=base)
        else:
            await self._write(incident, {"execution": base}, must=True)

    async def _retry_completion(self, incident: dict) -> dict | None:
        exe = incident["status"]["execution"]
        completion = exe.get("completion")
        if not completion:
            return incident
        try:
            await self.recorder.complete_action(
                completion["key"],
                parse_time(completion["requestAt"]),
                completion.get("auditId"),
                completion.get("httpStatus"),
                completion["outcome"],
            )
        except RecorderUnavailable:
            return incident  # try again next tick; the request already happened
        return await self._write(incident, {"execution": {"completion": None}}) or None

    async def _to_verifying(self, incident, exe, *, must=False, extra=None) -> None:
        plan = exe["plan"]
        criteria = CATALOGUE[plan["actionId"]].criteria_id
        fields = {"execution": extra} if extra else {}
        await self._move(
            incident,
            "Verifying",
            must=must,
            verification={
                "criteria": criteria,
                "startedAt": iso(self.clock()),
                "result": "pending",
            },
            **fields,
        )

    # ---- Verifying -----------------------------------------------------------------------
    async def _verifying(self, incident: dict) -> None:
        status = incident["status"]
        plan = status["execution"]["plan"]
        entry = CATALOGUE[plan["actionId"]]
        state = status["verification"]
        now = self.clock()
        verdict = await self.verifier.check(
            entry.criteria_id,
            incident,
            state,
            now,
            entry.verify_timeout_s,
            plan.get("replicas"),
        )
        if verdict.result != "pending":
            # one row per (incident, criteria): the final verdict only
            try:
                await self.recorder.record_verification(
                    incident["metadata"]["uid"],
                    entry.criteria_id,
                    verdict.observations,
                    verdict.result,
                    now,
                )
            except RecorderUnavailable:
                logger.warning("verification row not recorded")
        if verdict.result == "met":
            await self._move(
                incident, "Resolved", "remediation_verified", verification=verdict.state
            )
        elif verdict.result == "not_met":
            await self._move(incident, "Compensating", verification=verdict.state)
        elif verdict.state != state:
            await self._write(incident, {"verification": verdict.state})

    # ---- Compensating --------------------------------------------------------------------
    async def _compensating(self, incident: dict) -> None:
        status = incident["status"]
        exe = status["execution"]
        plan = exe["plan"]
        entry = CATALOGUE[plan["actionId"]]
        if entry.compensation is None:
            await self._move(incident, "Escalated", "verification_failed")
            return
        target = incident["spec"]["target"]
        ns, name = target["namespace"], target["name"]
        comp = exe.get("compensation") or {}
        since = parse_time(
            (status.get("timestamps") or {}).get("compensating") or iso(self.clock())
        )
        if (
            comp.get("result") is None
            and (self.clock() - since).total_seconds() > COMPENSATION_TIMEOUT_S
        ):
            await self._move(incident, "Escalated", "compensation_failed")
            return
        pending = comp.get("pending")
        if pending and await self._applied(ns, name, pending):
            await self._move(
                incident,
                "Escalated",
                "verification_failed",
                must=True,
                execution={"compensation": {"pending": None, "result": "admitted"}},
            )
            return
        if not pending:
            # a gated request like any other: kill switch, then the dry-run of the same request
            ks = state_of(await self.api.get_configmap(KILLSWITCH))
            if ks != "active":
                await self._move(
                    incident,
                    "Escalated",
                    "compensation_blocked",
                    execution={"compensation": {"result": f"killswitch {ks}"}},
                )
                return
            dry = await self.mutations.patch_scale(
                ns, name, plan["previous"], dry_run=True
            )
            if not dry.ok:
                await self._move(
                    incident,
                    "Escalated",
                    "compensation_blocked",
                    execution={
                        "compensation": {"result": f"dry-run HTTP {dry.status}"}
                    },
                )
                return
            pending = {
                "key": f"{incident['metadata']['name']}-c1",
                "kind": "scale",
                "replicas": plan["previous"],
                "previous": plan["replicas"],
            }
            written = await self._write(
                incident, {"execution": {"compensation": {"pending": pending}}}
            )
            if written is None:
                return
            incident = written
        done = await self._send(incident, pending, compensation=True)
        if done.result == "replayed":
            # a recorded key is never sent again: the next tick uses the next compensation key
            head, n = pending["key"].rsplit("-c", 1)
            nxt = {**pending, "key": f"{head}-c{int(n) + 1}"}
            await self._write(
                incident, {"execution": {"compensation": {"pending": nxt}}}
            )
            return
        audit = [
            *(exe.get("auditIds") or []),
            *([done.audit_id] if done.audit_id else []),
        ]
        fields = {"auditIds": audit}
        completion = self._completion(pending, done)
        if completion:
            fields["completion"] = completion
        if done.result == "admitted":
            await self._move(
                incident,
                "Escalated",
                "verification_failed",
                must=True,
                execution=fields
                | {"compensation": {"pending": None, "result": "admitted"}},
            )
            return
        reason = (
            "compensation_failed" if done.result == "error" else "compensation_blocked"
        )
        if done.result == "recorder_unavailable":
            reason = "compensation_failed"
        await self._move(
            incident,
            "Escalated",
            reason,
            must=True,
            execution=fields
            | {"compensation": {"pending": None, "result": done.result}},
        )
