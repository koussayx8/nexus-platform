"""The Action Catalogue (spec §9), frozen: four entries, and the only actions a proposal may name.

Verification criteria and compensation belong here, never to the Reasoner. The proposal's
expectedEffect and confidence are recorded and never read by any decision.
"""

import dataclasses

FAULT_CLASSES = frozenset(
    {
        "gray_failure",
        "cpu_saturation",
        "bad_rollout",
        "traffic_spike",
        "dependency_failure",
        "image_pull",
        "cert_expiry",
        "dns_failure",
        "disk_pressure",
        "unknown",
    }
)
RECOMMENDATION_KINDS = frozenset(
    {
        "git_revert",
        "config_change",
        "resource_limit_change",
        "dependency_investigation",
        "investigate",
    }
)

REPLICAS_MIN, REPLICAS_MAX, REPLICAS_MAX_STEP = 1, 5, 2


@dataclasses.dataclass(frozen=True)
class Entry:
    action_id: str
    mutates: bool
    criteria_id: (
        str | None
    )  # verification criteria; None for entries that mutate nothing
    verify_timeout_s: int  # Verifying phase timeout (§7)
    execute_timeout_s: int  # Executing phase timeout (§7)
    compensation: str | None  # None: failure ends as Escalated


CATALOGUE: dict[str, Entry] = {
    e.action_id: e
    for e in (
        Entry("restart_workload", True, "restart_v1", 180, 300, None),
        Entry("scale_deployment", True, "scale_v1", 240, 30, "scale_back"),
        Entry("escalate", False, None, 0, 0, None),
        Entry("no_action", False, None, 0, 0, None),
    )
}
