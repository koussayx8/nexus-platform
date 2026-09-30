"""Kopf status-persistence spike (M1b-6 6b; spec §4 runtime settings, §7, §8, §11 RBAC, §27).

Question: does a Kopf operator run on the staged M1b RBAC only (incidents get/list/watch/create,
incidents/status get/patch/update, two ConfigMaps get, namespaces get/list/watch) with the §4
settings: standalone, progress and diff-base in the Incident status, no event posting, no
finalizers, one namespace (nexus-system) and a 5 s reconcile cadence?

Run 1 (2026-09-28) built the cadence with @kopf.timer. Kopf 1.44.6 gives every timer and daemon a
finalizer; the staged RBAC denies that metadata patch, and Kopf never ran the create handlers.
Owner decision at the 6b gate: the cadence is a 5 s loop started at operator startup that writes
only through the status subresource, and settings.scanning.disabled declares the restricted mode.

Single writer per status field (owner, 6b gate):
  reconcile loop   status.phase, status.reason, status.timestamps.detected and .terminal
  intake handlers  status.autonomyLevel, status.timestamps.intake (a spike-only marker)
  Kopf             status.kopf (progress and diff-base)
Writers patch their own keys, never a whole map. The loop computes each transition from fields
the intake handlers own, so the write carries the resourceVersion it was computed from: a 409
means re-read, recompute and retry, never a blind overwrite.

Reconcile rules (§7, plan M1b-8): no phase -> Detected (entry = creation); Detected, intake done,
L0 -> Recorded / level_observe; still Detected 20 s after creation -> Escalated / evidence_error
(no Evidence Collector yet). Terminal Incidents are never written (§7 field mutability).

Create handlers, in the order Kopf runs them:
  intake_level  status.autonomyLevel from the target namespace's nexus.io/autonomy-level label;
                missing or invalid means 0 (fail closed, §12)
  intake_stamp  sleeps SPIKE_STEP_SLEEP_S, so a kill can land mid-handler, then
                status.timestamps.intake (intake done)
"SPIKE ..." log lines let run-spike.sh count runs across a kill.

Test hook (P5, no lost updates): for the Incident named SPIKE_RACE_INCIDENT, intake_level waits
until the loop has read the Incident, and the loop writes only after the handler's patch has
landed, so the loop's first write carries a stale resourceVersion.

Login: only the envtest operator kubeconfig named by NEXUS_SPIKE_KUBECONFIG, checked below, for
Kopf and for the spike's own client. A registered login handler replaces Kopf's fallbacks, so
KUBECONFIG and ~/.kube/config are never read. Run by run-spike.sh, never against a real cluster.
"""

import asyncio
import contextlib
import datetime
import json
import logging
import os
import re
import ssl

import aiohttp
import kopf
import yaml

GROUP, VERSION, PLURAL = "nexus.io", "v1alpha1", "incidents"
INCIDENTS = (GROUP, VERSION, PLURAL)
NAMESPACE = "nexus-system"
LEVEL_LABEL = "nexus.io/autonomy-level"
LOOP_INTERVAL_S = 5
DETECTED_TIMEOUT_S = 20
WRITE_ATTEMPTS = 3
STEP_SLEEP_S = float(os.environ.get("SPIKE_STEP_SLEEP_S", "2"))
RACE_INCIDENT = os.environ.get("SPIKE_RACE_INCIDENT", "")

logger = logging.getLogger("spike")


class Conflict(Exception):
    """HTTP 409: the resourceVersion a write carried is stale."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(t: datetime.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc
    )


def _credentials() -> tuple[str, str, str]:
    """Server, CA path and token of the envtest operator kubeconfig, after the guard checks."""
    path = os.environ["NEXUS_SPIKE_KUBECONFIG"]
    ca_want = os.path.join(os.environ["NEXUS_ENVTEST_DIR"], "pki", "ca.crt")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if len(cfg["clusters"]) != 1 or len(cfg["users"]) != 1:
        raise kopf.PermanentError(
            "expected one cluster and one user in the envtest kubeconfig"
        )
    cluster, user = cfg["clusters"][0]["cluster"], cfg["users"][0]["user"]
    m = re.fullmatch(r"https://127\.0\.0\.1:(\d+)", cluster["server"])
    if not m or m.group(1) == "6443":
        raise kopf.PermanentError("the server is not an envtest port on 127.0.0.1")
    if os.path.realpath(cluster["certificate-authority"]) != os.path.realpath(ca_want):
        raise kopf.PermanentError("the kubeconfig does not use the envtest CA")
    return cluster["server"], cluster["certificate-authority"], user["token"]


class Api:
    """The spike's own client, with the operator identity: the loop, and the level read."""

    def __init__(self) -> None:
        server, ca, token = _credentials()
        self._server = server
        self._incidents = (
            f"{server}/apis/{GROUP}/{VERSION}/namespaces/{NAMESPACE}/{PLURAL}"
        )
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {token}", "User-Agent": "nexus-spike"},
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=ca)),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def _call(self, method: str, url: str, **kwargs) -> dict:
        async with self._session.request(method, url, **kwargs) as r:
            if r.status == 409:
                raise Conflict(url)
            r.raise_for_status()
            return await r.json()

    async def list_incidents(self) -> list[dict]:
        return (await self._call("GET", self._incidents))["items"]

    async def get_incident(self, name: str) -> dict:
        return await self._call("GET", f"{self._incidents}/{name}")

    async def patch_status(
        self, name: str, status: dict, resource_version: str
    ) -> dict:
        """Merge-patch status through the subresource; the resourceVersion is a precondition."""
        body = {"metadata": {"resourceVersion": resource_version}, "status": status}
        return await self._call(
            "PATCH",
            f"{self._incidents}/{name}/status",
            data=json.dumps(body),
            headers={"Content-Type": "application/merge-patch+json"},
        )

    async def namespace_level(self, namespace: str) -> int:
        body = await self._call("GET", f"{self._server}/api/v1/namespaces/{namespace}")
        value = (body["metadata"].get("labels") or {}).get(LEVEL_LABEL)
        return int(value) if value in {"0", "1", "2", "3"} else 0

    async def close(self) -> None:
        await self._session.close()


class Runtime:
    api: Api | None = None
    loop_task: asyncio.Task | None = None
    race_read = asyncio.Event()  # the loop has read the race Incident (P5 test hook)


RUNTIME = Runtime()


@kopf.on.login()
def login(**_) -> kopf.ConnectionInfo:
    server, ca, token = _credentials()
    return kopf.ConnectionInfo(server=server, ca_path=ca, scheme="Bearer", token=token)


@kopf.on.startup()
async def start(settings: kopf.OperatorSettings, **_) -> None:
    settings.posting.enabled = False  # §11 grants no events create
    settings.scanning.disabled = (
        True  # no CRD or namespace watches: restricted mode, declared
    )
    settings.persistence.progress_storage = kopf.StatusProgressStorage(
        field="status.kopf.progress", touch_field="status.kopf.dummy"
    )
    settings.persistence.diffbase_storage = kopf.StatusDiffBaseStorage(
        field="status.kopf.last-handled-configuration"
    )
    RUNTIME.api = Api()
    RUNTIME.loop_task = asyncio.create_task(reconcile_loop(), name="reconcile loop")


@kopf.on.cleanup()
async def stop(**_) -> None:
    if RUNTIME.loop_task is not None:
        RUNTIME.loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await RUNTIME.loop_task
    if RUNTIME.api is not None:
        await RUNTIME.api.close()
    logger.info("SPIKE loop stopped")


@kopf.on.create(*INCIDENTS, id="intake_level")
async def intake_level(name: str, spec: kopf.Spec, patch: kopf.Patch, **_) -> None:
    logger.info("SPIKE start intake_level %s", name)
    if name == RACE_INCIDENT:
        try:
            await asyncio.wait_for(RUNTIME.race_read.wait(), timeout=15)
        except TimeoutError:
            logger.warning("SPIKE race: the loop did not read %s within 15 s", name)
    level = await RUNTIME.api.namespace_level(spec["target"]["namespace"])
    patch.status["autonomyLevel"] = level
    logger.info("SPIKE done intake_level %s level=%d", name, level)


@kopf.on.create(*INCIDENTS, id="intake_stamp")
async def intake_stamp(name: str, patch: kopf.Patch, **_) -> None:
    logger.info("SPIKE start intake_stamp %s (sleep %.0f s)", name, STEP_SLEEP_S)
    await asyncio.sleep(STEP_SLEEP_S)
    patch.status["timestamps"] = {"intake": _iso(_now())}
    logger.info("SPIKE done intake_stamp %s", name)


def decide(incident: dict, now: datetime.datetime) -> dict | None:
    """The loop's next status write for an Incident, or None."""
    status = incident.get("status") or {}
    phase = status.get("phase")
    created = incident["metadata"]["creationTimestamp"]
    if phase is None:
        return {"phase": "Detected", "timestamps": {"detected": created}}
    if phase != "Detected":
        return None
    intake_done = "intake" in (status.get("timestamps") or {})
    if intake_done and status.get("autonomyLevel") == 0:
        return {
            "phase": "Recorded",
            "reason": "level_observe",
            "timestamps": {"terminal": _iso(now)},
        }
    if (now - _parse(created)).total_seconds() >= DETECTED_TIMEOUT_S:
        return {
            "phase": "Escalated",
            "reason": "evidence_error",
            "timestamps": {"terminal": _iso(now)},
        }
    return None


async def _race_hold(name: str) -> None:
    """P5 test hook: release intake_level, then wait until its patch has landed."""
    RUNTIME.race_read.set()
    for _ in range(75):
        incident = await RUNTIME.api.get_incident(name)
        if "autonomyLevel" in (incident.get("status") or {}):
            return
        await asyncio.sleep(0.2)
    logger.warning("SPIKE race: no intake patch on %s within 15 s", name)


async def reconcile(incident: dict) -> None:
    name = incident["metadata"]["name"]
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        status = decide(incident, _now())
        if status is None:
            return
        read_rv = incident["metadata"]["resourceVersion"]
        if name == RACE_INCIDENT and not RUNTIME.race_read.is_set():
            await _race_hold(name)
        try:
            written = await RUNTIME.api.patch_status(name, status, read_rv)
        except Conflict:
            logger.info(
                "SPIKE loop conflict %s rv=%s attempt %d: re-read and retry",
                name,
                read_rv,
                attempt,
            )
            incident = await RUNTIME.api.get_incident(name)
            continue
        logger.info(
            "SPIKE loop write %s rv=%s -> %s %s",
            name,
            read_rv,
            written["metadata"]["resourceVersion"],
            json.dumps(status, sort_keys=True),
        )
        return
    logger.warning("SPIKE loop gave up on %s after %d conflicts", name, WRITE_ATTEMPTS)


async def reconcile_loop() -> None:
    """Every 5 s: list the Incidents in nexus-system and reconcile each one (§4 cadence)."""
    clock = asyncio.get_running_loop()
    tick = 0
    while True:
        started = clock.time()
        tick += 1
        try:
            incidents = await RUNTIME.api.list_incidents()
            for incident in incidents:
                await reconcile(incident)
            logger.info("SPIKE loop tick %d incidents=%d", tick, len(incidents))
        except Exception:
            logger.exception("SPIKE loop tick %d failed", tick)
        await asyncio.sleep(max(0.0, LOOP_INTERVAL_S - (clock.time() - started)))
