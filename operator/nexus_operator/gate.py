"""Safety Gate stages 1-7 (spec §9, D11): advisory checks in fixed order.

Stage 8 (approval validation) is approval.py; at L2 this gate runs stages 4-6 before
AwaitingApproval and 1-7 again once an approval is valid (the dry-run waits for the approval,
because K3 refuses the request without one). Stages 9-11 (RBAC, Kyverno,
PDB) are not here: they are the Trusted Enforcement Plane, and this gate only previews them with
the stage-7 dry-run. Every stage here is a convenience that fails early with a clear reason; none
is the reason a request is safe. With advisoryChecks off, stages 2-7 are skipped and recorded as
skipped — stage 1 still runs because an unparseable proposal cannot be turned into a request.

The first failing stage ends the gate. Its phase is Escalated for the breaker and the cool-down
(D9: they hand off to a human) and Blocked for every other stage.
"""

import asyncio
import dataclasses
import datetime

from . import breaker, killswitch, proposal
from .catalogue import (
    CATALOGUE,
    REPLICAS_MAX,
    REPLICAS_MAX_STEP,
    REPLICAS_MIN,
)
from .kube import TRANSPORT_ERRORS, ApiError, KubeApi
from .mutations import Mutations
from .recorder import ActRecorder, RecorderUnavailable

GATE_TIMEOUT_S = 30

ESCALATING = frozenset({"circuit_open", "remediation_induced", "gate_timeout"})


@dataclasses.dataclass
class Decision:
    stage: int
    verdict: str  # pass | fail | skipped
    detail: str = ""


@dataclasses.dataclass
class GateResult:
    passed: bool
    reason: str | None = None  # the failing stage's reason code
    stage: int | None = None
    detail: str = ""
    decisions: list[Decision] = dataclasses.field(default_factory=list)
    dry_run_audit_id: str | None = None

    @property
    def phase(self) -> str | None:
        if self.passed:
            return None
        return "Escalated" if self.reason in ESCALATING else "Blocked"

    def status(self) -> dict:
        """status.gate, as §8 shows it."""
        return {
            "verdict": "allowed" if self.passed else "denied",
            "firstFailingStage": "none" if self.passed else self.stage,
            "reason": self.reason,
            "dryRun": "allowed" if self.passed else "not_reached",
        }


def rollout_in_progress(deployment: dict) -> bool:
    spec = deployment.get("spec") or {}
    st = deployment.get("status") or {}
    want = spec.get("replicas", 1)
    meta = deployment.get("metadata") or {}
    return (
        st.get("observedGeneration", 0) < meta.get("generation", 0)
        or st.get("updatedReplicas", 0) < want
        or st.get("replicas", 0) > st.get("updatedReplicas", 0)
        or st.get("availableReplicas", 0) < st.get("updatedReplicas", 0)
    )


def ready_replicas(deployment: dict) -> int:
    return (deployment.get("status") or {}).get("readyReplicas", 0) or 0


def selector_of(deployment: dict) -> str:
    labels = ((deployment.get("spec") or {}).get("selector") or {}).get(
        "matchLabels"
    ) or {}
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def live_pods(pods: list[dict]) -> list[dict]:
    """Pods that are neither terminating nor finished."""
    return [
        p
        for p in pods
        if not (p.get("metadata") or {}).get("deletionTimestamp")
        and (p.get("status") or {}).get("phase") not in ("Succeeded", "Failed")
    ]


def pod_ready(pod: dict) -> bool:
    return any(
        c.get("type") == "Ready" and c.get("status") == "True"
        for c in (pod.get("status") or {}).get("conditions") or []
    )


class Gate:
    def __init__(
        self,
        api: KubeApi,
        mutations: Mutations,
        recorder: ActRecorder,
        *,
        advisory: bool = True,
        breaker_epoch: int = 0,
        clock=None,
    ) -> None:
        self.api = api
        self.mutations = mutations
        self.recorder = recorder
        self.advisory = advisory
        self.epoch = breaker_epoch
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    async def run(
        self, incident: dict, doc: dict, upto: int = 7, level: int = 3
    ) -> GateResult:
        """Stages 1..upto for one proposal/v1 document, bounded by the 30 s gate timeout.

        upto=3 is for a namespace below L2, where nothing executes: the proposal is still
        validated before it is recommended or handed off. level is the namespace level the
        stage-4 pre-check expects: 3 to act, 2 to ask for approval.
        """
        try:
            return await asyncio.wait_for(
                self._run(incident, doc, upto, level), GATE_TIMEOUT_S
            )
        except TimeoutError:
            return GateResult(False, "gate_timeout", None, "gate exceeded 30 s")

    async def _run(
        self, incident: dict, doc: dict, upto: int, level: int = 3
    ) -> GateResult:
        res = GateResult(True)

        def fail(stage: int, reason: str, detail: str) -> GateResult:
            res.passed, res.stage, res.reason, res.detail = False, stage, reason, detail
            res.decisions.append(Decision(stage, "fail", detail))
            return res

        # 1  schema
        errors = proposal.validate(doc)
        if errors:
            return fail(1, "schema_invalid", "; ".join(errors[:5]))
        res.decisions.append(Decision(1, "pass"))
        action = doc["action"]
        if not self.advisory:
            res.decisions += [
                Decision(n, "skipped", "advisoryChecks off") for n in range(2, 8)
            ]
            return res
        # 2  catalogue
        entry = CATALOGUE.get(action["id"])
        if entry is None:
            return fail(2, "not_in_catalogue", f"unknown action {action['id']!r}")
        res.decisions.append(Decision(2, "pass"))
        # 3  bounds and target binding
        spec_target = incident["spec"]["target"]
        if action["target"] != {
            k: spec_target[k] for k in ("namespace", "kind", "name")
        }:
            return fail(
                3, "target_mismatch", "proposal target differs from spec.target"
            )
        params = action["parameters"]
        deployment = None
        if entry.mutates:
            try:
                deployment = await self.api.get_deployment(
                    spec_target["namespace"], spec_target["name"]
                )
            except (ApiError, *TRANSPORT_ERRORS) as e:
                return fail(6, "precondition_failed", f"cannot read the target: {e}")
        if action["id"] == "restart_workload" and params:
            return fail(3, "bounds_exceeded", "restart_workload takes no parameters")
        if action["id"] == "scale_deployment":
            want = params.get("replicas")
            if want is None or not REPLICAS_MIN <= want <= REPLICAS_MAX:
                return fail(3, "bounds_exceeded", f"replicas {want} outside [1, 5]")
            if deployment is not None:
                have = (deployment.get("spec") or {}).get("replicas", 1)
                if abs(want - have) > REPLICAS_MAX_STEP:
                    return fail(
                        3, "bounds_exceeded", f"change {have} -> {want} exceeds 2"
                    )
        res.decisions.append(Decision(3, "pass"))
        if not entry.mutates or upto < 4:
            why = "no mutation" if not entry.mutates else "level below L3"
            res.decisions += [Decision(n, "skipped", why) for n in range(4, 8)]
            return res
        # 4  autonomy pre-check, against the live label
        try:
            ns = await self.api.get_namespace(spec_target["namespace"])
        except (ApiError, *TRANSPORT_ERRORS) as e:
            return fail(4, "autonomy_denied", f"cannot read the namespace: {e}")
        label = (
            ((ns or {}).get("metadata") or {})
            .get("labels", {})
            .get("nexus.io/autonomy-level")
        )
        if label != str(level):
            return fail(
                4, "autonomy_denied", f"namespace level is {label!r}, L{level} expected"
            )
        res.decisions.append(Decision(4, "pass"))
        # 5  kill switch, breaker, cool-down
        try:
            ks = killswitch.state_of(await self.api.get_configmap(killswitch.CONFIGMAP))
            if ks != "active":
                return fail(5, "killswitch", f"kill switch is {ks}")
            now = self.clock()
            rows = await self.recorder.recent_actions(breaker.lookback(now))
        except (ApiError, RecorderUnavailable, *TRANSPORT_ERRORS) as e:
            return fail(5, "precondition_failed", f"cannot evaluate stage 5: {e}")
        ns_, name = spec_target["namespace"], spec_target["name"]
        target = f"{ns_}/{name}"
        why = breaker.breaker_open(rows, now, self.epoch, target)
        if why:
            return fail(5, "circuit_open", why)
        if breaker.in_cooldown(
            rows, now, self.epoch, target, incident["metadata"]["uid"]
        ):
            return fail(
                5, "remediation_induced", "an action ran on this target within 5 min"
            )
        res.decisions.append(Decision(5, "pass"))
        # 6  preconditions
        if deployment is None:
            return fail(6, "precondition_failed", "target Deployment not found")
        if rollout_in_progress(deployment):
            return fail(6, "precondition_failed", "a rollout is in progress")
        if action["id"] == "restart_workload" and ready_replicas(deployment) < 2:
            return fail(6, "precondition_failed", "fewer than 2 Ready replicas")
        if action["id"] == "scale_deployment" and params["replicas"] == have:
            return fail(6, "precondition_failed", f"already at {have} replicas")
        res.decisions.append(Decision(6, "pass"))
        if (
            upto < 7
        ):  # L2 before approval: K3 would refuse the dry-run, so stage 8 sends it
            res.decisions.append(Decision(7, "skipped", "awaiting approval"))
            return res
        # 7  server-side dry-run of the exact request
        try:
            if action["id"] == "scale_deployment":
                out = await self.mutations.patch_scale(
                    ns_, name, params["replicas"], dry_run=True
                )
            else:
                pods = live_pods(await self.api.list_pods(ns_, selector_of(deployment)))
                if not pods:
                    return fail(6, "precondition_failed", "no pod to evict")
                p = pods[0]["metadata"]
                out = await self.mutations.evict(ns_, p["name"], p["uid"], dry_run=True)
        except (ApiError, *TRANSPORT_ERRORS) as e:
            return fail(7, "dry_run_denied", f"dry-run failed: {e}")
        res.dry_run_audit_id = out.audit_id
        if not out.ok:
            return fail(7, "dry_run_denied", f"HTTP {out.status}: {out.message}")
        res.decisions.append(Decision(7, "pass", f"audit-id {out.audit_id}"))
        return res
