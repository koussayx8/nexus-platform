"""Kopf status-persistence spike (M1b-6 6b; spec §4 runtime settings, §11 RBAC, §27).

Question: does a Kopf operator run on the staged M1b RBAC only (incidents get/list/watch/create,
incidents/status get/patch/update, two ConfigMaps get, namespaces get/list/watch) with the §4
settings: standalone, progress and diff-base in the Incident status, no event posting, no delete
handlers (so no finalizers), one namespace (nexus-system) and a 5 s timer?

Handlers, in the order Kopf runs them on a new Incident:
  record_detected   status.phase Detected
  record_observed   sleeps SPIKE_STEP_SLEEP_S, so a kill can land mid-handler, then
                    status.phase Recorded, reason level_observe (the L0 path)
  reconcile_tick    every 5 s while the phase is not terminal: status.timestamps.lastTick
Each run logs "SPIKE start|done <handler> <name>", so the runner counts runs across a kill.

Login: only the envtest operator kubeconfig named by NEXUS_SPIKE_KUBECONFIG, checked below. A
registered login handler replaces Kopf's fallbacks, so KUBECONFIG and ~/.kube/config are never
read. Run by run-spike.sh, never against a real cluster.
"""

import asyncio
import datetime
import logging
import os
import re

import kopf
import yaml

INCIDENTS = ("nexus.io", "v1alpha1", "incidents")
TERMINAL = {
    "Recorded",
    "Recommended",
    "Dismissed",
    "Resolved",
    "Escalated",
    "Blocked",
    "Rejected",
    "Expired",
}
STEP_SLEEP_S = float(os.environ.get("SPIKE_STEP_SLEEP_S", "2"))

logger = logging.getLogger("spike")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@kopf.on.login()
def login(**_) -> kopf.ConnectionInfo:
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
    return kopf.ConnectionInfo(
        server=cluster["server"],
        ca_path=cluster["certificate-authority"],
        scheme="Bearer",
        token=user["token"],
    )


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_) -> None:
    settings.posting.enabled = False  # §11 grants no events create
    settings.persistence.progress_storage = kopf.StatusProgressStorage(
        field="status.kopf.progress", touch_field="status.kopf.dummy"
    )
    settings.persistence.diffbase_storage = kopf.StatusDiffBaseStorage(
        field="status.kopf.last-handled-configuration"
    )


@kopf.on.create(*INCIDENTS, id="record_detected")
def record_detected(name: str, patch: kopf.Patch, **_) -> None:
    logger.info("SPIKE start record_detected %s", name)
    patch.status["phase"] = "Detected"
    patch.status["timestamps"] = {"detected": _now()}
    logger.info("SPIKE done record_detected %s", name)


@kopf.on.create(*INCIDENTS, id="record_observed")
async def record_observed(name: str, patch: kopf.Patch, **_) -> None:
    logger.info("SPIKE start record_observed %s (sleep %.0f s)", name, STEP_SLEEP_S)
    await asyncio.sleep(STEP_SLEEP_S)
    patch.status["phase"] = "Recorded"
    patch.status["reason"] = "level_observe"
    patch.status["autonomyLevel"] = 0
    patch.status["timestamps"] = {"recorded": _now()}
    logger.info("SPIKE done record_observed %s", name)


def _non_terminal(status: kopf.Status, **_) -> bool:
    return status.get("phase") not in TERMINAL


@kopf.timer(*INCIDENTS, id="reconcile_tick", interval=5, when=_non_terminal)
def reconcile_tick(name: str, patch: kopf.Patch, **_) -> None:
    logger.info("SPIKE tick reconcile_tick %s", name)
    patch.status["timestamps"] = {"lastTick": _now()}
