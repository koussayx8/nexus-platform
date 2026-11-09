"""A fake cluster, Flight Recorder and metrics source for the act-path tests."""

import copy
import datetime

from fakes import T0, FakeApi
from nexus_operator.recorder import (
    ActionIntent,
    ActionRecord,
    ApiResult,
    RecorderUnavailable,
)

NS, DEP = "nexus-dev", "sample-api"
ACTIVE = {"data": {"state": "active"}}


class FakeCluster(FakeApi):
    """FakeApi plus one Deployment with pods, the Scale and Eviction subresources, Audit-Ids."""

    def __init__(self, replicas: int = 2, level: str = "3", killswitch=ACTIVE):
        super().__init__(
            namespaces={NS: {"nexus.io/autonomy-level": level}},
            configmaps={"nexus-killswitch": killswitch},
        )
        self.replicas = replicas
        self.pods: list[dict] = []
        self.n_pod = 0
        for _ in range(replicas):
            self._new_pod(ready=True)
        self.limit = "1"
        self.rolling = False
        self.audit_n = 0
        self.audit_prefix = "audit"  # a shared database needs ids unique across rigs
        self.requests: list[
            tuple
        ] = []  # every request that reached the API: (verb, args, dry)
        self.refuse = (
            None  # callable(verb, args, dry) -> ApiResult | None: the enforcement plane
        )
        self.raise_on = None  # callable(verb, args, dry) -> exception | None
        self.auto_ready = True  # replacement pods become Ready at once
        self.events: list[str] = []  # shared with FakeRecorder.order by the harness
        self.replica_sets: list[dict] = [
            {
                "metadata": {
                    "name": f"{DEP}-7d9",
                    "annotations": {"deployment.kubernetes.io/revision": "3"},
                },
                "spec": {
                    "template": {
                        "spec": {"containers": [{"image": f"reg/x@sha256:{'a' * 64}"}]}
                    }
                },
            },
            {
                "metadata": {
                    "name": f"{DEP}-6c1",
                    "annotations": {"deployment.kubernetes.io/revision": "2"},
                },
                "spec": {
                    "template": {"spec": {"containers": [{"image": "reg/x:old"}]}}
                },
            },
        ]
        self.events_list: list[dict] = []
        self.application: dict | None = {
            "status": {
                "sync": {"status": "Synced", "revision": "c0ffee"},
                "health": {"status": "Healthy"},
            }
        }
        self.deployments_missing = False
        self.logs_current: dict[str, str] = {}
        self.logs_previous: dict[str, str] = {}

    def _new_pod(self, ready: bool) -> dict:
        self.n_pod += 1
        pod = {
            "metadata": {"name": f"{DEP}-{self.n_pod}", "uid": f"pod-uid-{self.n_pod}"},
            "status": {
                "phase": "Running",
                "conditions": [
                    {"type": "Ready", "status": "True" if ready else "False"}
                ],
                "containerStatuses": [{"restartCount": 0}],
            },
        }
        self.pods.append(pod)
        return pod

    def heal(self) -> None:
        for p in self.pods:
            p["status"]["conditions"] = [{"type": "Ready", "status": "True"}]

    def _audit(self) -> str:
        self.audit_n += 1
        return f"{self.audit_prefix}-{self.audit_n}"

    def _enforce(self, verb: str, args: tuple, dry: bool) -> ApiResult | None:
        self.requests.append((verb, args, dry))
        if not dry:
            self.events.append(f"request:{verb}")
        if self.raise_on and (exc := self.raise_on(verb, args, dry)):
            raise exc
        if self.refuse and (out := self.refuse(verb, args, dry)):
            return out
        return None

    # --- reads ---
    async def get_deployment(self, namespace, name):
        if (namespace, name) != (NS, DEP) or self.deployments_missing:
            return None
        ready = sum(
            1
            for p in self.pods
            if p["status"]["conditions"][0]["status"] == "True"
            and "deletionTimestamp" not in p["metadata"]
        )
        return {
            "metadata": {"name": DEP, "generation": 1},
            "spec": {
                "replicas": self.replicas,
                "selector": {"matchLabels": {"app": DEP}},
                "template": {
                    "spec": {
                        "containers": [{"resources": {"limits": {"cpu": self.limit}}}]
                    }
                },
            },
            "status": {
                "observedGeneration": 1,
                "replicas": self.replicas,
                "updatedReplicas": self.replicas if not self.rolling else 1,
                "readyReplicas": ready,
                "availableReplicas": ready,
            },
        }

    async def list_pods(self, namespace, selector):
        return copy.deepcopy(self.pods)

    async def list_replicasets(self, namespace, selector):
        return copy.deepcopy(self.replica_sets)

    async def list_events(self, namespace):
        return copy.deepcopy(self.events_list)

    async def get_application(self, name):
        return copy.deepcopy(self.application)

    async def pod_log(self, namespace, pod, *, previous, tail):
        return (self.logs_previous if previous else self.logs_current).get(pod)

    async def get_scale(self, namespace, name):
        return {"spec": {"replicas": self.replicas}}

    # --- mutations ---
    async def patch_scale(self, namespace, name, replicas, dry_run=False):
        if out := self._enforce("scale", (namespace, name, replicas), dry_run):
            return out
        if not dry_run:
            self.replicas = replicas
            while len(self.pods) < replicas:
                self._new_pod(ready=self.auto_ready)
            while len(self.pods) > replicas:
                self.pods.pop()
        return ApiResult(200, self._audit(), {})

    async def evict(self, namespace, pod, uid, dry_run=False):
        if out := self._enforce("evict", (namespace, pod, uid), dry_run):
            return out
        if not dry_run:
            self.pods = [p for p in self.pods if p["metadata"]["uid"] != uid]
            self._new_pod(ready=self.auto_ready)
        return ApiResult(201, self._audit(), {})

    def mutations(self) -> list[tuple]:
        return [r for r in self.requests if not r[2]]


def res(status: int, audit_id: str | None, message: str = "") -> ApiResult:
    """An API response as AuditedClient returns it: a Status body carries the message."""
    return ApiResult(status, audit_id, {"message": message})


class FakeRecorder:
    """The Flight Recorder as the act path sees it, with the real database's constraints:
    rows need their incident (foreign keys), keys are idempotent, gate verdicts are pass or fail,
    and verifications are one row per (incident, criteria). Signatures are the real ones
    (test_fakes_match_the_real_api)."""

    def __init__(self):
        self.incidents: dict[str, dict] = {}
        self.actions: dict[str, dict] = {}
        self.order: list[
            str
        ] = []  # recorder and request events, for ordering assertions
        self.transitions: list[tuple] = []
        self.gates: list[tuple] = []
        self.verifications: dict[tuple, tuple] = {}
        self.proposals: dict[str, object] = {}
        self.bundles: dict[str, tuple] = {}
        self.proposal_rows: dict[str, dict] = {}
        self.fail_intent = False
        self.fail_complete = False
        self.fail_all = False

    def _up(self) -> None:
        if self.fail_all:
            raise RecorderUnavailable("down")

    def _fk(self, uid: str) -> None:
        if uid not in self.incidents:
            raise RecorderUnavailable("ForeignKeyViolationError")

    async def open_incident(
        self, incident_uid, target, source, detected_at, run_id=None
    ):
        self._up()
        if incident_uid in self.incidents:
            return False
        self.incidents[incident_uid] = {
            "target": target,
            "terminal": None,
            "run_id": run_id,
        }
        return True

    async def close_incident(self, incident_uid, terminal_phase, reason):
        self._up()
        self._fk(incident_uid)
        if not reason:
            raise RecorderUnavailable("CheckViolationError")
        row = self.incidents[incident_uid]
        if row["terminal"]:
            return False
        row["terminal"] = (terminal_phase, reason)
        return True

    async def record_transition(
        self, incident_uid, seq, from_phase, to_phase, at, stage, reason=None
    ):
        self._up()
        self._fk(incident_uid)
        if any(t[:2] == (incident_uid, seq) for t in self.transitions):
            return False
        self.transitions.append((incident_uid, seq, from_phase, to_phase, reason))
        return True

    async def record_bundle(
        self,
        bundle_hash,
        incident_uid,
        schema_version,
        content,
        redaction_counts,
        collected_at,
    ):
        self._up()
        self._fk(incident_uid)
        if bundle_hash in self.bundles:
            return False
        self.bundles[bundle_hash] = (incident_uid, content, redaction_counts)
        return True

    async def record_proposal(
        self,
        proposal_hash,
        incident_uid,
        reasoner_mode,
        schema_valid,
        *,
        model_id=None,
        prompt_version=None,
        index_hash=None,
        retrieved_chunk_ids=(),
        raw_output=None,
        parsed_proposal=None,
        latency_ms=None,
        fallback_used=False,
        request_id=None,
        reject_reason=None,
    ):
        self._up()
        self._fk(incident_uid)
        if proposal_hash in self.proposal_rows:
            return False
        self.proposal_rows[proposal_hash] = {
            "incident_uid": incident_uid,
            "mode": reasoner_mode,
            "valid": schema_valid,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "index_hash": index_hash,
            "chunks": list(retrieved_chunk_ids),
            "raw_output": raw_output,
            "parsed": parsed_proposal,
            "latency_ms": latency_ms,
            "fallback_used": fallback_used,
            "request_id": request_id,
            "reject_reason": reject_reason,
        }
        self.proposals[proposal_hash] = (
            parsed_proposal if parsed_proposal is not None else (raw_output or "")
        )
        return True

    async def record_gate_decision(self, incident_uid, stage, verdict, detail, at):
        self._up()
        self._fk(incident_uid)
        if verdict not in ("pass", "fail") or not 1 <= stage <= 11:
            raise RecorderUnavailable("CheckViolationError")
        if any(g[:2] == (incident_uid, stage) for g in self.gates):
            return False
        self.gates.append((incident_uid, stage, verdict))
        return True

    async def record_verification(
        self, incident_uid, criteria_id, observations, result, at
    ):
        self._up()
        self._fk(incident_uid)
        key = (incident_uid, criteria_id)
        if key in self.verifications:
            return False
        self.verifications[key] = (result, observations)
        return True

    async def begin_action(self, intent):
        if self.fail_intent:
            raise RecorderUnavailable("down")
        self._up()
        self._fk(intent.incident_uid)
        if intent.idempotency_key in self.actions:
            return False
        self.order.append(f"intent:{intent.idempotency_key}")
        self.actions[intent.idempotency_key] = {
            "intent": intent,
            "intent_at": intent.intent_at or T0,
            "request_at": None,
            "audit_id": None,
            "http_status": None,
            "result": None,
        }
        return True

    async def complete_action(
        self, idempotency_key, request_at, audit_id, http_status, result
    ):
        if self.fail_complete:
            raise RecorderUnavailable("down")
        self._up()
        row = self.actions.get(idempotency_key)
        if row is None or row["request_at"] is not None:
            return False
        self.order.append(f"complete:{idempotency_key}")
        row.update(
            request_at=request_at,
            audit_id=audit_id,
            http_status=http_status,
            result=result,
        )
        return True

    async def count_actions(self, target, since):
        self._up()
        return len([r for r in self._records(since) if r.target == target])

    async def last_action_at(self, target):
        self._up()
        times = [r.intent_at for r in self._records(None) if r.target == target]
        return max(times) if times else None

    async def recent_actions(self, since):
        self._up()
        return self._records(since)

    async def get_proposal(self, proposal_hash):
        self._up()
        return self.proposals.get(proposal_hash)

    def _records(self, since):
        out = []
        for row in self.actions.values():
            i = row["intent"]
            if since is None or row["intent_at"] >= since:
                out.append(
                    ActionRecord(
                        i.incident_uid,
                        i.action_id,
                        self.incidents[i.incident_uid]["target"],
                        row["intent_at"],
                        row["result"],
                        i.action_class,
                    )
                )
        return out

    # --- test helpers ---
    def seed(
        self,
        uid,
        target,
        minutes_ago,
        now,
        *,
        action_id="scale_deployment",
        result="applied",
        action_class="executed",
    ):
        """An action recorded earlier, by another incident."""
        self.incidents.setdefault(
            uid, {"target": target, "terminal": None, "run_id": None}
        )
        key = f"{uid}-{len(self.actions) + 1}"
        self.actions[key] = {
            "intent": ActionIntent(key, uid, action_id, {}, action_class),
            "intent_at": now - datetime.timedelta(minutes=minutes_ago),
            "request_at": None,
            "audit_id": None,
            "http_status": None,
            "result": result,
        }

    def completed(self, key) -> dict:
        return self.actions[key]


class FakeMetrics:
    def __init__(self, p95=0.05, cpu=0.1, **zscores):
        z = {
            "cpu": 0.0,
            "latencyP95": 0.0,
            "errorRatio": 0.0,
            "requestRate": 0.0,
            "inflight": 0.0,
        } | zscores
        self.values = {
            "namespace:nexus_sample_api_latency_p95:2m": p95,
            "namespace:nexus_sample_api_cpu:rate2m": cpu,
            "namespace:nexus_sample_api_cpu:zscore15m": z["cpu"],
            "namespace:nexus_sample_api_latency_p95:zscore15m": z["latencyP95"],
            "namespace:nexus_sample_api_error_ratio:zscore15m": z["errorRatio"],
            "namespace:nexus_sample_api_requests:zscore15m": z["requestRate"],
            "namespace:nexus_sample_api_inflight:zscore15m": z["inflight"],
            "namespace:nexus_sample_api_error_ratio:rate2m": 0.0,
            "namespace:nexus_sample_api_latency_p95:baseline_avg15m": 0.05,
            "namespace:nexus_sample_api_latency_p95:baseline_stddev15m": 0.01,
        }

    async def scalar(self, query, namespace):
        return self.values[query]


class Clock:
    def __init__(self):
        self.t = T0 + datetime.timedelta(seconds=1)

    def __call__(self):
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += datetime.timedelta(seconds=seconds)


BASELINE = {"latencyP95": {"mean": 0.05, "stddev": 0.01}}


def proposal_doc(action="restart_workload", replicas=None, **over) -> dict:
    params = {} if replicas is None else {"replicas": replicas}
    doc = {
        "schema": "proposal/v1",
        "hypothesis": {"faultClass": "gray_failure", "summary": "s"},
        "evidenceRefs": ["metrics.zscores.latencyP95"],
        "runbookRefs": [],
        "action": {
            "id": action,
            "target": {"namespace": NS, "kind": "Deployment", "name": DEP},
            "parameters": params,
        },
        "expectedEffect": "x",
        "confidence": 0.5,
        "escalation": None,
        "abstain": False,
    }
    if action == "escalate":
        doc["escalation"] = {
            "reason": "r",
            "recommendation": {"kind": "investigate", "detail": "d"},
        }
    return doc | over
