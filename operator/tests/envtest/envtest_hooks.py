"""Test-only Kopf additions for the envtest integration test (M1b-8). Never in the image.

run-envtest.sh loads this file with PYTHONPATH=operator:
  kopf run --standalone --namespace nexus-system ... operator/tests/envtest/envtest_hooks.py
Importing nexus_operator.main registers the production handlers; this file then adds:
  login        the envtest operator kubeconfig (NEXUS_TEST_KUBECONFIG), after the guard checks,
               for Kopf and for the operator's own client. The production login returns None here
               (no ServiceAccount token file), so only these credentials exist.
  test_hold    a create handler that sleeps TEST_HOLD_S for the Incident named TEST_HOLD, so a
               SIGKILL can land mid-handler (P1, P3)
  _race_hold   P5: for the Incident named TEST_RACE, the intake handler waits until the loop has
               read it, and the loop writes only after the intake patch has landed, so its first
               write carries a stale resourceVersion
"""

import asyncio
import logging
import os
import re

import kopf
import yaml

from nexus_operator import hooks, main
from nexus_operator.kube import Credentials
from nexus_operator.model import INCIDENTS

HOLD = os.environ.get("TEST_HOLD", "")
HOLD_S = float(os.environ.get("TEST_HOLD_S", "2"))
RACE = os.environ.get("TEST_RACE", "")

logger = logging.getLogger("nexus.test")


def envtest_credentials() -> Credentials:
    """Server, CA and token of the envtest operator kubeconfig, after the guard checks."""
    path = os.environ["NEXUS_TEST_KUBECONFIG"]
    ca_want = os.path.join(os.environ["NEXUS_ENVTEST_DIR"], "pki", "ca.crt")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if len(cfg["clusters"]) != 1 or len(cfg["users"]) != 1:
        raise kopf.PermanentError("expected one cluster and one user in the kubeconfig")
    cluster, user = cfg["clusters"][0]["cluster"], cfg["users"][0]["user"]
    m = re.fullmatch(r"https://127\.0\.0\.1:(\d+)", cluster["server"])
    if not m or m.group(1) == "6443":
        raise kopf.PermanentError("the server is not an envtest port on 127.0.0.1")
    if os.path.realpath(cluster["certificate-authority"]) != os.path.realpath(ca_want):
        raise kopf.PermanentError("the kubeconfig does not use the envtest CA")
    return Credentials(
        cluster["server"], cluster["certificate-authority"], user["token"]
    )


main.RUNTIME.credentials = envtest_credentials


@kopf.on.login(id="envtest")
def login(**_) -> kopf.ConnectionInfo:
    c = envtest_credentials()
    return kopf.ConnectionInfo(
        server=c.server, ca_path=c.ca_path, scheme="Bearer", token=c.token
    )


@kopf.on.create(*INCIDENTS, id="test_hold", timeout=120)
async def test_hold(name: str, **_) -> None:
    if name != HOLD:
        return
    logger.info("TEST start test_hold %s (sleep %.0f s)", name, HOLD_S)
    await asyncio.sleep(HOLD_S)
    logger.info("TEST done test_hold %s", name)


_race_read: asyncio.Event | None = None


def _event() -> asyncio.Event:
    global _race_read
    if _race_read is None:
        _race_read = asyncio.Event()
    return _race_read


async def _intake_waits(name: str) -> None:
    if name != RACE:
        return
    try:
        await asyncio.wait_for(_event().wait(), timeout=15)
    except TimeoutError:
        logger.warning("TEST race: the loop did not read %s within 15 s", name)


async def _race_hold(name: str) -> None:
    """Release the intake handler, then wait until its patch has landed."""
    if name != RACE or _event().is_set():
        return
    _event().set()
    for _ in range(75):
        incident = await main.RUNTIME.api.get_incident(name)
        if "autonomyLevel" in ((incident or {}).get("status") or {}):
            return
        await asyncio.sleep(0.2)
    logger.warning("TEST race: no intake patch on %s within 15 s", name)


hooks.before_intake = _intake_waits
hooks.before_status_write = _race_hold
