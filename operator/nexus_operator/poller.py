"""The Alert Poller: Alertmanager alerts -> Incident CRs (spec §4; plan M1b-8 rev 3 §1.1).

Every alertPollSeconds: GET /api/v2/alerts (active, not silenced, not inhibited).

Target. Only alerts with a nexus_target label count; the four Nexus alerts carry
'{{ $labels.namespace }}/sample-api'. It must read <namespace>/<name>, the namespace must be one
CRD Validation C3 admits, and it must equal the alert's namespace label. Any other alert with
nexus_target is ignored and logged once; alerts without it (Watchdog, ...) are ignored silently.

Dedupe by firing episode. The key is (fingerprint, startsAt), looked up in the API on every poll
across all phases, terminal ones included, never in process memory: each Incident carries the
label nexus.io/fingerprint and spec.detectedAt = the alert's startsAt (compared as instants).
The name inc-<fingerprint>-<startsAt epoch seconds> is a backstop: a second create of the same
episode fails with 409 AlreadyExists.

Absorb. An open (non-terminal) Incident on the same target absorbs other episodes; each
absorption is logged once per process. An absorbed episode has no Incident of its own, so once
the absorbing Incident is terminal, the next poll gives it one if it still fires (owner, rev 3).
Smoke and real Incidents never absorb each other.

Smoke. nexus_smoke="true" on the alert -> the label nexus.io/smoke: "true" on the Incident, so
smoke Incidents never count as detections (select '!nexus.io/smoke').
"""

import asyncio
import dataclasses
import datetime
import logging
import re

import aiohttp

from . import killswitch
from .kube import AlreadyExists, ApiError, KubeApi
from .model import (
    FINGERPRINT_LABEL,
    NAMESPACE,
    SMOKE_LABEL,
    TARGET_NAMESPACES,
    is_terminal,
    parse_time,
)

logger = logging.getLogger("nexus.poller")

_FINGERPRINT = re.compile(r"[0-9a-f]{1,63}")
_NAME = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")
ALERT_QUERY = {"active": "true", "silenced": "false", "inhibited": "false"}


@dataclasses.dataclass(frozen=True)
class Episode:
    """One firing episode of one alert."""

    alertname: str
    fingerprint: str
    starts_at: str
    starts: datetime.datetime
    namespace: str
    name: str
    smoke: bool

    @property
    def key(self) -> tuple[str, datetime.datetime]:
        return (self.fingerprint, self.starts)

    @property
    def target(self) -> tuple[str, str, bool]:
        return (self.namespace, self.name, self.smoke)


def parse_alert(alert: dict) -> Episode | str | None:
    """An Episode; a reason string if the alert has nexus_target but is unusable; None if it
    has no nexus_target at all."""
    labels = alert.get("labels") or {}
    target = labels.get("nexus_target")
    if not target:
        return None
    state = (alert.get("status") or {}).get("state", "active")
    if state != "active":
        return f"state is {state!r}"
    ns, sep, name = target.partition("/")
    if not sep or not _NAME.fullmatch(ns) or not _NAME.fullmatch(name):
        return f"nexus_target {target!r} is not <namespace>/<name>"
    if ns not in TARGET_NAMESPACES:
        return f"nexus_target namespace {ns!r} is outside C3"
    if labels.get("namespace") != ns:
        return f"nexus_target namespace {ns!r} differs from the namespace label {labels.get('namespace')!r}"
    alertname = labels.get("alertname") or ""
    if not 1 <= len(alertname) <= 128:
        return "alertname is empty or longer than 128"
    fingerprint = alert.get("fingerprint") or ""
    if not _FINGERPRINT.fullmatch(fingerprint):
        return f"fingerprint {fingerprint!r} is not 1-63 lowercase hex digits"
    starts_at = alert.get("startsAt") or ""
    try:
        starts = parse_time(starts_at)
    except ValueError:
        return f"startsAt {starts_at!r} is not RFC 3339"
    return Episode(
        alertname=alertname,
        fingerprint=fingerprint,
        starts_at=starts_at,
        starts=starts,
        namespace=ns,
        name=name,
        smoke=labels.get("nexus_smoke") == "true",
    )


def incident_name(ep: Episode) -> str:
    return f"inc-{ep.fingerprint}-{int(ep.starts.timestamp())}"


def incident_body(ep: Episode) -> dict:
    labels = {FINGERPRINT_LABEL: ep.fingerprint}
    if ep.smoke:
        labels[SMOKE_LABEL] = "true"
    return {
        "apiVersion": "nexus.io/v1alpha1",
        "kind": "Incident",
        "metadata": {
            "name": incident_name(ep),
            "namespace": NAMESPACE,
            "labels": labels,
        },
        "spec": {
            "source": {
                "type": "detector",
                "alertname": ep.alertname,
                "fingerprint": ep.fingerprint,
            },
            "target": {
                "namespace": ep.namespace,
                "kind": "Deployment",
                "name": ep.name,
            },
            "detectedAt": ep.starts_at,
        },
    }


def episodes_in(incidents: list[dict]) -> set[tuple[str, datetime.datetime]]:
    """(fingerprint, detectedAt) of every Incident with the fingerprint label, all phases."""
    found = set()
    for inc in incidents:
        fp = ((inc.get("metadata") or {}).get("labels") or {}).get(FINGERPRINT_LABEL)
        try:
            detected = parse_time((inc.get("spec") or {}).get("detectedAt") or "")
        except ValueError:
            continue
        if fp:
            found.add((fp, detected))
    return found


def open_by_target(incidents: list[dict]) -> dict[tuple[str, str, bool], str]:
    """The oldest open Incident per (namespace, name, smoke)."""
    found: dict[tuple[str, str, bool], tuple[str, str]] = {}
    for inc in incidents:
        if is_terminal(inc):
            continue
        meta = inc.get("metadata") or {}
        target = (inc.get("spec") or {}).get("target") or {}
        smoke = (meta.get("labels") or {}).get(SMOKE_LABEL) == "true"
        key = (target.get("namespace"), target.get("name"), smoke)
        entry = (meta.get("creationTimestamp") or "", meta.get("name") or "")
        if key not in found or entry < found[key]:
            found[key] = entry
    return {k: v[1] for k, v in found.items()}


def classify(
    ep: Episode,
    episodes: set[tuple[str, datetime.datetime]],
    open_targets: dict[tuple[str, str, bool], str],
) -> tuple[str, str | None]:
    """("exists", None), ("absorb", <Incident>) or ("create", None)."""
    if ep.key in episodes:
        return ("exists", None)
    if ep.target in open_targets:
        return ("absorb", open_targets[ep.target])
    return ("create", None)


class Poller:
    def __init__(self, api: KubeApi, alertmanager_url: str, poll_s: float) -> None:
        self.api = api
        self.url = f"{alertmanager_url}/api/v2/alerts"
        self.poll_s = poll_s
        self.errors = 0
        self.last_tick: float | None = None
        self.killswitch: str | None = None
        self._ignored: set[tuple[str, str]] = set()
        self._absorbed: set[tuple[str, datetime.datetime, str]] = set()
        self._session: aiohttp.ClientSession | None = None

    async def fetch_alerts(self) -> list[dict]:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "nexus-operator"},
                timeout=aiohttp.ClientTimeout(total=5),
            )
        async with self._session.get(self.url, params=ALERT_QUERY) as r:
            r.raise_for_status()
            alerts = await r.json()
        if not isinstance(alerts, list):
            raise TypeError("Alertmanager did not return a list")
        return alerts

    async def check_killswitch(self) -> str:
        try:
            state = killswitch.state_of(
                await self.api.get_configmap(killswitch.CONFIGMAP)
            )
        except ApiError as e:
            state = f"unreadable (HTTP {e.status})"
        if state != self.killswitch:
            log = logger.info if state == "active" else logger.warning
            log("kill switch %s (changes no transition in M1b-8)", state)
            self.killswitch = state
        return state

    async def poll_once(self) -> dict[str, int]:
        counts = {"alerts": 0, "created": 0, "absorbed": 0, "exists": 0, "ignored": 0}
        await self.check_killswitch()
        try:
            alerts = await self.fetch_alerts()
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, TypeError) as e:
            # Alertmanager down or answering garbage: fail quiet (§16), and count it
            self.errors += 1
            logger.warning("Alertmanager poll failed (%d so far): %s", self.errors, e)
            return counts
        counts["alerts"] = len(alerts)
        incidents = await self.api.list_incidents()
        episodes = episodes_in(incidents)
        open_targets = open_by_target(incidents)
        for alert in sorted(
            alerts, key=lambda a: (a.get("startsAt") or "", a.get("fingerprint") or "")
        ):
            ep = parse_alert(alert)
            if ep is None:
                continue
            if isinstance(ep, str):
                counts["ignored"] += 1
                key = (alert.get("fingerprint") or "", ep)
                if key not in self._ignored:
                    self._ignored.add(key)
                    logger.warning(
                        "ignored alertname=%s fingerprint=%s: %s",
                        (alert.get("labels") or {}).get("alertname"),
                        key[0],
                        ep,
                    )
                continue
            action, incident = classify(ep, episodes, open_targets)
            if action == "exists":
                counts["exists"] += 1
            elif action == "absorb":
                counts["absorbed"] += 1
                if (ep.fingerprint, ep.starts, incident) not in self._absorbed:
                    self._absorbed.add((ep.fingerprint, ep.starts, incident))
                    logger.info(
                        "absorbed alertname=%s fingerprint=%s startsAt=%s incident=%s",
                        ep.alertname,
                        ep.fingerprint,
                        ep.starts_at,
                        incident,
                    )
            else:
                await self._create(ep, episodes, open_targets, counts)
        return counts

    async def _create(self, ep, episodes, open_targets, counts) -> None:
        body = incident_body(ep)
        name = body["metadata"]["name"]
        try:
            await self.api.create_incident(body)
        except AlreadyExists:
            logger.warning(
                "create %s: 409 AlreadyExists, episode already recorded", name
            )
            episodes.add(ep.key)
            return
        except ApiError as e:
            logger.error("create %s failed: %s", name, e)
            return
        episodes.add(ep.key)
        open_targets[ep.target] = name
        counts["created"] += 1
        logger.info(
            "created incident=%s alertname=%s fingerprint=%s startsAt=%s target=%s/%s smoke=%s",
            name,
            ep.alertname,
            ep.fingerprint,
            ep.starts_at,
            ep.namespace,
            ep.name,
            str(ep.smoke).lower(),
        )

    async def run(self) -> None:
        clock = asyncio.get_running_loop()
        while True:
            started = clock.time()
            try:
                counts = await self.poll_once()
                logger.debug("poll %s", counts)
            except Exception:
                logger.exception("poll failed")
            self.last_tick = clock.time()
            await asyncio.sleep(max(0.0, self.poll_s - (clock.time() - started)))

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
