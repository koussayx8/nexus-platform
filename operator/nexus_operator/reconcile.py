"""The Incident Reconciler: a status-only loop started at operator startup (ADR-023).

Single writer per status field (ADR-023):
  this loop        status.phase, status.reason, status.timestamps.detected and .terminal
  intake handler   status.autonomyLevel (main.py)
  Kopf             status.kopf (progress and diff-base)
Writes are JSON merge patches of the loop's own keys through /status, each carrying the
resourceVersion it was computed from: a 409 means re-read, recompute and retry, never overwrite.

M1b rules (§7; plan M1b-8 rev 3):
  no phase                               -> Detected; timestamps.detected = creationTimestamp
  Detected, intake done, L0              -> Recorded / level_observe
  Detected, detectedTimeoutSeconds after
  creation (not after spec.detectedAt)   -> Escalated / evidence_error (no Evidence Collector yet)
  terminal                               -> never written again
The loop never moves an Incident to a terminal phase while Kopf progress is pending in
status.kopf.progress. The wait is bounded: each handler has HANDLER_TIMEOUT_S, and once the
oldest pending handler started more than HANDLER_TIMEOUT_S plus one loop period ago, the timeout
path escalates anyway (ADR-023 owner rule; ADR-025).
"""

import asyncio
import dataclasses
import datetime
import json
import logging
from collections.abc import Callable

from . import hooks
from .kube import Conflict, KubeApi
from .model import TERMINAL_PHASES, iso, now, parse_time

HANDLER_TIMEOUT_S = 10
WRITE_ATTEMPTS = 3

logger = logging.getLogger("nexus.reconcile")


@dataclasses.dataclass(frozen=True)
class Timing:
    detected_timeout_s: float = 20
    reconcile_s: float = 5
    handler_timeout_s: float = HANDLER_TIMEOUT_S


def level_of(namespace: dict | None) -> int:
    """nexus.io/autonomy-level of a Namespace; missing, invalid or no namespace means 0 (§12)."""
    if namespace is None:
        return 0
    value = ((namespace.get("metadata") or {}).get("labels") or {}).get(
        "nexus.io/autonomy-level"
    )
    return int(value) if value in {"0", "1", "2", "3"} else 0


def pending_since(status: dict) -> datetime.datetime | None:
    """The oldest start among Kopf progress records still in status, or None if none are."""
    progress = (status.get("kopf") or {}).get("progress") or {}
    starts = []
    for record in progress.values():
        if not record:
            continue
        try:
            starts.append(parse_time(record.get("started") or ""))
        except ValueError:
            starts.append(datetime.datetime.min.replace(tzinfo=datetime.timezone.utc))
    return min(starts) if starts else None


def decide(incident: dict, at: datetime.datetime, timing: Timing) -> dict | None:
    """The loop's next status write for an Incident, or None."""
    status = incident.get("status") or {}
    phase = status.get("phase")
    created = parse_time(incident["metadata"]["creationTimestamp"])
    if phase in TERMINAL_PHASES:
        return None
    if phase is None:
        return {"phase": "Detected", "timestamps": {"detected": iso(created)}}
    if phase != "Detected":
        return None  # no other non-terminal phase exists in M1b
    since = pending_since(status)
    if since is None and status.get("autonomyLevel") == 0:
        return {
            "phase": "Recorded",
            "reason": "level_observe",
            "timestamps": {"terminal": iso(at)},
        }
    bound = timing.handler_timeout_s + timing.reconcile_s
    stuck = since is not None and (at - since).total_seconds() > bound
    timed_out = (at - created).total_seconds() >= timing.detected_timeout_s
    if timed_out and (since is None or stuck):
        return {
            "phase": "Escalated",
            "reason": "evidence_error",
            "timestamps": {"terminal": iso(at)},
        }
    return None


async def reconcile(
    api: KubeApi,
    incident: dict,
    timing: Timing,
    clock: Callable[[], datetime.datetime] = now,
) -> dict | None:
    """Write decide()'s result with the read resourceVersion; on 409 re-read and retry."""
    name = incident["metadata"]["name"]
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        status = decide(incident, clock(), timing)
        if status is None:
            return None
        read_rv = incident["metadata"]["resourceVersion"]
        if hooks.before_status_write is not None:
            await hooks.before_status_write(name)
        try:
            written = await api.patch_status(name, status, read_rv)
        except Conflict:
            logger.info(
                "loop conflict %s rv=%s attempt %d: re-read and retry",
                name,
                read_rv,
                attempt,
            )
            incident = await api.get_incident(name)
            if incident is None:
                return None
            continue
        logger.info(
            "loop write %s rv=%s -> %s %s",
            name,
            read_rv,
            written["metadata"]["resourceVersion"],
            json.dumps(status, sort_keys=True),
        )
        return status
    logger.warning("loop gave up on %s after %d conflicts", name, WRITE_ATTEMPTS)
    return None


class Loop:
    """Every reconcile_s: list the Incidents in nexus-system and reconcile each one (§4)."""

    def __init__(self, api: KubeApi, timing: Timing) -> None:
        self.api = api
        self.timing = timing
        # Event-loop time of the last finished tick, failed or not: liveness asks whether the
        # loop still turns, not whether the API answered.
        self.last_tick: float | None = None

    async def tick(self) -> int:
        incidents = await self.api.list_incidents()
        for incident in incidents:
            await reconcile(self.api, incident, self.timing)
        return len(incidents)

    async def run(self) -> None:
        clock = asyncio.get_running_loop()
        n = 0
        while True:
            started = clock.time()
            n += 1
            try:
                count = await self.tick()
                logger.debug("loop tick %d incidents=%d", n, count)
            except Exception:
                logger.exception("loop tick %d failed", n)
            self.last_tick = clock.time()
            await asyncio.sleep(
                max(0.0, self.timing.reconcile_s - (clock.time() - started))
            )
