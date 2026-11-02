"""The act path (spec §4, §6, §7): Proposed -> Gate -> Executing -> Verifying -> terminal.

One call to ActPath.step() moves one Incident by at most one step, level-triggered: it derives
the next step from the status and the observed cluster, never from an event. Phases it owns:

  Proposed      stages 1-3 for every proposal; abstain, escalate, no_action and the levels
                L1 (Recommended) and L2 (Escalated, approval is M3) end here; at L3 stages 4-7
                run and a pass moves to Executing
  Executing     one request per call, write-ahead; a restart evicts one pod at a time and waits
                for the replacement to be Ready; a scale is one request
  Verifying     the catalogue's criteria against the raw baseline (verifier.py)
  Compensating  scale back, itself a gated request (kill switch, dry-run); restart has none

Detected and Diagnosing belong to the Evidence Collector and the Reasoner path, which are not part
of this change: the act path starts at a Proposed Incident whose proposal/v1 document the
recorder holds (Recorder.get_proposal).

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

import logging

from . import proposal
from .catalogue import CATALOGUE
from .executor import Executed, Executor
from .gate import Gate, GateResult, live_pods, pod_ready, selector_of
from .killswitch import CONFIGMAP as KILLSWITCH
from .killswitch import state_of
from .kube import Conflict, KubeApi
from .model import iso, parse_time
from .recorder import ActionRow, Recorder, RecorderUnavailable
from .verifier import Verifier

logger = logging.getLogger("nexus.act")

ACT_PHASES = frozenset({"Proposed", "Executing", "Verifying", "Compensating"})
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
    "Proposed": "Safety Gate",
    "Executing": "Action Executor",
    "Verifying": "Verifier",
    "Compensating": "Compensator",
}


def _level(status: dict) -> int:
    v = status.get("autonomyLevel")
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 3 else 0


class ActPath:
    def __init__(
        self,
        api: KubeApi,
        recorder: Recorder,
        gate: Gate,
        executor: Executor,
        verifier: Verifier,
        clock,
    ) -> None:
        self.api, self.recorder, self.gate = api, recorder, gate
        self.executor, self.verifier, self.clock = executor, verifier, clock

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
        terminal = phase not in PHASE_SEQ
        stamp = "terminal" if terminal else phase.lower()
        status = {"phase": phase, "timestamps": {stamp: iso(now)}, **fields}
        if reason is not None:
            status["reason"] = reason
        written = await self._write(incident, status, must=must)
        if written is None:
            return None
        try:
            await self.recorder.record_transition(
                incident["metadata"]["uid"],
                TERMINAL_SEQ if terminal else PHASE_SEQ[phase],
                from_phase=old,
                to_phase=phase,
                stage=OWNER.get(old, "Incident Reconciler"),
                reason=reason,
                at=now,
            )
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
            "Proposed": self._proposed,
            "Executing": self._executing,
            "Verifying": self._verifying,
            "Compensating": self._compensating,
        }.get(phase)
        if handler is not None:
            await handler(incident)

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
        try:
            doc = await self.recorder.get_proposal(uid)
        except RecorderUnavailable:
            return  # retried next tick, until the 30 s timeout
        if doc is None:
            return
        level = _level(status)
        if not proposal.validate(doc) and doc["abstain"]:
            await self._move(incident, "Escalated", "abstain")
            return
        result = await self.gate.run(incident, doc, upto=7 if level == 3 else 3)
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
                "Escalated",
                "autonomy_denied",
                gate=gate | {"detail": "L2 approval is M3"},
            )
        elif level != 3:
            await self._move(incident, "Blocked", "autonomy_denied", gate=gate)
        else:
            await self._begin(incident, doc, gate)

    async def _record_gate(self, uid: str, result: GateResult) -> None:
        for d in result.decisions:
            try:
                await self.recorder.record_gate_decision(
                    uid, d.stage, d.verdict, d.detail, self.clock()
                )
            except RecorderUnavailable:
                logger.warning("gate decision for stage %d not recorded", d.stage)

    async def _begin(self, incident: dict, doc: dict, gate: dict) -> None:
        """Gate passed at L3: record the plan and enter Executing. No request is sent here."""
        target = incident["spec"]["target"]
        ns, name = target["namespace"], target["name"]
        action = doc["action"]
        plan: dict = {"actionId": action["id"], "proposalHash": proposal.hash_of(doc)}
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
            await self._issue(incident, pending)  # not applied: the same key, sent now
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

    def _row(
        self, incident: dict, pending: dict, compensation: bool = False
    ) -> ActionRow:
        plan = (incident["status"].get("execution") or {}).get("plan") or {}
        target = incident["spec"]["target"]
        if pending["kind"] == "scale":
            action_id, params = "scale_deployment", {"replicas": pending["replicas"]}
            previous = {"replicas": pending["previous"]}
        else:
            action_id, params = "restart_workload", {"pod": pending["pod"]}
            previous = {"pod": pending["pod"], "uid": pending["uid"]}
        return ActionRow(
            idempotency_key=pending["key"],
            incident_uid=incident["metadata"]["uid"],
            action_id=action_id
            if not compensation
            else plan.get("actionId", action_id),
            parameters=params,
            previous_state=previous,
            intent_time=self.clock(),
            target_namespace=target["namespace"],
            target_name=target["name"],
            action_class="compensation" if compensation else "executed",
        )

    async def _send(
        self, incident: dict, pending: dict, compensation: bool = False
    ) -> Executed:
        target = incident["spec"]["target"]
        ns, name = target["namespace"], target["name"]
        row = self._row(incident, pending, compensation)
        if pending["kind"] == "scale":
            return await self.executor.scale(row, ns, name, pending["replicas"])
        return await self.executor.evict(row, ns, pending["pod"], pending["uid"])

    def _completion(self, pending: dict, done: Executed) -> dict | None:
        if not done.unrecorded:
            return None
        return {
            "key": pending["key"],
            "auditId": done.audit_id,
            "httpStatus": done.http_status,
            "result": done.result,
        }

    async def _issue(self, incident: dict, pending: dict) -> None:
        """Send the pending request and record the outcome."""
        done = await self._send(incident, pending)
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
                pending["key"],
                audit_id=None,
                http_status=None,
                result="admitted_unconfirmed",
                request_time=self.clock(),
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
                audit_id=completion.get("auditId"),
                http_status=completion.get("httpStatus"),
                result=completion["result"],
                request_time=self.clock(),
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
                incident, "Escalated", "verification_failed", must=True,
                execution={"compensation": {"pending": None, "result": "admitted"}},
            )  # fmt: skip
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
            dry = await self.api.patch_scale(ns, name, plan["previous"], dry_run=True)
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
                incident, "Escalated", "verification_failed", must=True,
                execution=fields | {"compensation": {"pending": None, "result": "admitted"}},
            )  # fmt: skip
            return
        reason = (
            "compensation_failed" if done.result == "error" else "compensation_blocked"
        )
        if done.result == "recorder_unavailable":
            reason = "compensation_failed"
        await self._move(
            incident, "Escalated", reason, must=True,
            execution=fields | {"compensation": {"pending": None, "result": done.result}},
        )  # fmt: skip
