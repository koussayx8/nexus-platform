"""The embedded Rules Engine R1 (spec §16): deterministic proposals from the evidence bundle.

Derived only from the Decision sections of the four in-corpus runbooks; first match wins; it never
reads log text, so injected text in a log cannot steer it (S6). The output is proposal/v1 and is
still validated by Safety Gate stage 1 like any other proposal.

Reading of the six rules against the evidence/v1.1 bundle (each is checked by a test):
  1  workload.rolloutInProgress, or fewer Ready than desired replicas while some are updated
  2  a container waiting with reason ImagePullBackOff or ErrImagePull
  3  CPU and p95 latency Z > 3, all replicas Ready                      -> scale to desired + 1
  4  (p95 latency Z > 3, or in-flight Z > 3 with request-rate Z < -3),
     CPU Z <= 1, all Ready, no rollout                                   -> restart
     The in-flight branch (ADR-039, an owner-approved deviation from spec v1.0 §16) covers a full
     hang: requests stop completing, so in-flight requests pile up, the completed rate drops and
     the p95 of the completed requests can stay flat. In-flight Z is bounded by client
     concurrency: fewer than 4 waiting clients cannot reach in-flight Z > 3 (stddev floor 1).
  5  request-rate Z > 3, latency Z <= 3, error ratio below 1 %          -> no_action
     The bundle carries Z-scores only, so the 1 % reads the absolute `metrics.errorRatio` when the
     bundle has it; without it rule 5 cannot be established and the engine falls to rule 6.
  6  anything else                                                       -> escalate, unknown
A missing Z-score never satisfies a condition: an evidence/v1 bundle (no in-flight Z) or a missing
request-rate Z never takes the in-flight branch.
"""

IMAGE_PULL = {"ImagePullBackOff", "ErrImagePull"}


def _z(bundle: dict, key: str) -> float | None:
    v = ((bundle.get("metrics") or {}).get("zscores") or {}).get(key)
    return None if isinstance(v, bool) or not isinstance(v, int | float) else float(v)


def _above(v: float | None, limit: float) -> bool:
    return v is not None and v > limit


def _at_most(v: float | None, limit: float) -> bool:
    return v is not None and v <= limit


def _below(v: float | None, limit: float) -> bool:
    return v is not None and v < limit


def _proposal(bundle, fault, summary, refs, action_id, params=None, escalation=None):
    target = (bundle.get("incident") or {}).get("target") or {}
    return {
        "schema": "proposal/v1",
        "hypothesis": {"faultClass": fault, "summary": summary},
        "evidenceRefs": refs,
        "runbookRefs": [],
        "action": {
            "id": action_id,
            "target": {k: target.get(k, "") for k in ("namespace", "kind", "name")},
            "parameters": params or {},
        },
        "expectedEffect": summary,
        "confidence": 1.0,
        "escalation": escalation,
        "abstain": False,
    }


def _escalate(kind: str, reason: str) -> dict:
    return {"reason": reason, "recommendation": {"kind": kind, "detail": reason}}


def _image_pull(bundle: dict) -> bool:
    for pod in bundle.get("pods") or []:
        for c in pod.get("containers") or []:
            if c.get("state") == "waiting" and c.get("reason") in IMAGE_PULL:
                return True
    return False


def propose(bundle: dict) -> dict:
    """The first matching R1 rule's proposal for a redacted evidence bundle."""
    wl = bundle.get("workload") or {}
    desired, ready, updated = wl.get("desired"), wl.get("ready"), wl.get("updated")
    rollout = bool(wl.get("rolloutInProgress"))
    all_ready = isinstance(desired, int) and desired >= 1 and ready == desired
    cpu, lat = _z(bundle, "cpu"), _z(bundle, "latencyP95")
    rate, inflight = _z(bundle, "requestRate"), _z(bundle, "inflight")

    if rollout or (
        isinstance(desired, int)
        and isinstance(ready, int)
        and ready < desired
        and isinstance(updated, int)
        and updated > 0
    ):
        why = "A rollout is in progress or the new revision has unavailable replicas"
        return _proposal(
            bundle,
            "bad_rollout",
            why,
            ["workload.rolloutInProgress"],
            "escalate",
            escalation=_escalate("git_revert", why),
        )
    if _image_pull(bundle):
        why = "A container is waiting on an image pull"
        return _proposal(
            bundle,
            "image_pull",
            why,
            ["pods"],
            "escalate",
            escalation=_escalate("investigate", why),
        )
    if all_ready and _above(cpu, 3) and _above(lat, 3):
        return _proposal(
            bundle,
            "cpu_saturation",
            "CPU and p95 latency are anomalous with every replica Ready",
            ["metrics.zscores.cpu", "metrics.zscores.latencyP95"],
            "scale_deployment",
            {"replicas": desired + 1},
        )
    if all_ready and not rollout and _at_most(cpu, 1):
        if _above(lat, 3):
            return _proposal(
                bundle,
                "gray_failure",
                "p95 latency is anomalous, CPU is flat and every pod is Ready",
                ["metrics.zscores.latencyP95", "metrics.zscores.cpu"],
                "restart_workload",
            )
        if _above(inflight, 3) and _below(rate, -3):
            return _proposal(
                bundle,
                "gray_failure",
                "Requests pile up in flight while the completed rate drops; CPU is flat and"
                " every pod is Ready",
                [
                    "metrics.zscores.inflight",
                    "metrics.zscores.requestRate",
                    "metrics.zscores.cpu",
                ],
                "restart_workload",
            )
    err = (bundle.get("metrics") or {}).get("errorRatio")
    if (
        _above(rate, 3)
        and _at_most(lat, 3)
        and isinstance(err, int | float)
        and not isinstance(err, bool)
        and err < 0.01
    ):
        return _proposal(
            bundle,
            "traffic_spike",
            "Request rate is anomalous with no errors and normal latency",
            ["metrics.zscores.requestRate"],
            "no_action",
        )
    why = "No rule matches the evidence"
    return _proposal(
        bundle,
        "unknown",
        why,
        ["metrics.zscores"],
        "escalate",
        escalation=_escalate("investigate", why),
    )
