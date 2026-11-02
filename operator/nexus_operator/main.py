"""Kopf wiring for the NEXUS Operator (spec §4 runtime settings; ADR-023; ADR-025).

Run as:
  kopf run --standalone --namespace nexus-system \
    --liveness=http://0.0.0.0:8080/healthz -m nexus_operator.main

Kopf settings, all from ADR-023: progress and diff-base in the Incident status, event posting off
(§11 grants no events), scanning disabled (no CRD or namespace watches), no timers, daemons,
delete handlers or finalizers. The 5 s cadence is the startup loop in reconcile.py.

Login: the in-cluster ServiceAccount only, for Kopf and for the operator's own client. Kopf's
login_with_service_account returns None when no token file exists, and Kopf then has no
credentials and stops; it never falls back to a kubeconfig. Test logins live in operator/tests/.

The liveness probe fails (HTTP 500) when either loop has stopped or has not finished a tick for
three periods (at least 30 s). Metrics and a ServiceMonitor arrive in M2.
"""

import asyncio
import contextlib
import logging

import kopf

from . import config, hooks, reconcile
from .act import ActPath
from .executor import Executor
from .gate import Gate
from .kube import KubeApi, service_account_credentials
from .model import INCIDENTS, now
from .mutations import Mutations
from .poller import Poller
from .recorder import AuditedClient, UnavailableRecorder
from .verifier import Prometheus, Verifier

logger = logging.getLogger("nexus.operator")


class Runtime:
    def __init__(self) -> None:
        self.credentials = service_account_credentials  # tests replace it
        self.api: KubeApi | None = None
        self.loop: reconcile.Loop | None = None
        self.poller: Poller | None = None
        self.tasks: list[asyncio.Task] = []
        # The Flight Recorder Writer (task 1). Until it is wired in here, every mutation is
        # refused as recorder_unavailable: nothing is sent that cannot first be recorded (§14).
        self.recorder = UnavailableRecorder()
        self.mutations: Mutations | None = None
        self.metrics: Prometheus | None = None


RUNTIME = Runtime()


@kopf.on.login(id="service_account")
def login(**kwargs) -> kopf.ConnectionInfo | None:
    return kopf.login_with_service_account(**kwargs)


@kopf.on.startup()
async def start(settings: kopf.OperatorSettings, **_) -> None:
    settings.posting.enabled = False
    settings.scanning.disabled = True
    settings.persistence.progress_storage = kopf.StatusProgressStorage(
        field="status.kopf.progress", touch_field="status.kopf.dummy"
    )
    settings.persistence.diffbase_storage = kopf.StatusDiffBaseStorage(
        field="status.kopf.last-handled-configuration"
    )
    try:
        RUNTIME.api = KubeApi(RUNTIME.credentials)
    except (OSError, KeyError) as e:
        raise kopf.PermanentError(f"no in-cluster credentials: {e!r}") from e
    try:
        cfg = config.parse(await RUNTIME.api.get_configmap(config.CONFIGMAP))
    except config.ConfigError as e:
        raise kopf.PermanentError(f"configuration: {e}") from e
    logger.info(
        "config alertmanagerURL=%s alertPollSeconds=%d reconcileSeconds=%d "
        "detectedTimeoutSeconds=%d handlerTimeout=%d",
        cfg.alertmanager_url,
        cfg.alert_poll_s,
        cfg.reconcile_s,
        cfg.detected_timeout_s,
        reconcile.HANDLER_TIMEOUT_S,
    )
    timing = reconcile.Timing(
        detected_timeout_s=cfg.detected_timeout_s, reconcile_s=cfg.reconcile_s
    )
    RUNTIME.metrics = Prometheus(cfg.prometheus_url) if cfg.prometheus_url else None
    RUNTIME.mutations = Mutations(AuditedClient.create(RUNTIME.credentials))
    gate = Gate(
        RUNTIME.api,
        RUNTIME.mutations,
        RUNTIME.recorder,
        advisory=cfg.advisory_checks,
        breaker_epoch=cfg.breaker_epoch,
        clock=now,
    )
    act = ActPath(
        RUNTIME.api,
        RUNTIME.mutations,
        RUNTIME.recorder,
        gate,
        Executor(RUNTIME.mutations, RUNTIME.recorder, now),
        Verifier(RUNTIME.api, RUNTIME.metrics),
        now,
    )
    logger.info(
        "act path advisoryChecks=%s breakerEpoch=%d prometheus=%s",
        "on" if cfg.advisory_checks else "off",
        cfg.breaker_epoch,
        "set" if cfg.prometheus_url else "unset",
    )
    RUNTIME.loop = reconcile.Loop(RUNTIME.api, timing, act)
    RUNTIME.poller = Poller(RUNTIME.api, cfg.alertmanager_url, cfg.alert_poll_s)
    RUNTIME.tasks = [
        asyncio.create_task(RUNTIME.loop.run(), name="reconcile loop"),
        asyncio.create_task(RUNTIME.poller.run(), name="alert poller"),
    ]


@kopf.on.cleanup()
async def stop(**_) -> None:
    for task in RUNTIME.tasks:
        task.cancel()
    for task in RUNTIME.tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if RUNTIME.poller is not None:
        await RUNTIME.poller.close()
    if RUNTIME.metrics is not None:
        await RUNTIME.metrics.close()
    if RUNTIME.mutations is not None:
        await RUNTIME.mutations.close()
    if RUNTIME.api is not None:
        await RUNTIME.api.close()
    logger.info("loops stopped")


@kopf.on.create(
    *INCIDENTS,
    id="intake_level",
    timeout=reconcile.HANDLER_TIMEOUT_S,
    backoff=2,
)
async def intake_level(name: str, spec: kopf.Spec, patch: kopf.Patch, **_) -> None:
    """status.autonomyLevel from the target namespace's label (§12; missing means 0)."""
    if hooks.before_intake is not None:
        await hooks.before_intake(name)
    namespace = spec["target"]["namespace"]
    try:
        ns = await asyncio.wait_for(
            RUNTIME.api.get_namespace(namespace), reconcile.HANDLER_TIMEOUT_S
        )
    except Exception as e:
        raise kopf.TemporaryError(f"namespace {namespace}: {e}", delay=2) from e
    level = reconcile.level_of(ns)
    patch.status["autonomyLevel"] = level
    logger.info("intake %s level=%d", name, level)


def _loop_age(component, period: float) -> str | None:
    """None if the component's loop is healthy, else why not."""
    if component is None or component.last_tick is None:
        return None  # not started yet, or before its first tick
    age = asyncio.get_running_loop().time() - component.last_tick
    limit = max(3 * period, 30.0)
    return None if age <= limit else f"last tick {age:.0f} s ago (limit {limit:.0f} s)"


@kopf.on.probe(id="loops")
async def loops_alive(**_) -> dict:
    dead = [t.get_name() for t in RUNTIME.tasks if t.done()]
    if dead:
        raise kopf.PermanentError(f"stopped: {', '.join(dead)}")
    stale = {
        "reconcile loop": _loop_age(
            RUNTIME.loop, RUNTIME.loop.timing.reconcile_s if RUNTIME.loop else 0
        ),
        "alert poller": _loop_age(
            RUNTIME.poller, RUNTIME.poller.poll_s if RUNTIME.poller else 0
        ),
    }
    stale = {k: v for k, v in stale.items() if v}
    if stale:
        raise kopf.PermanentError(f"stale: {stale}")
    return {
        "killswitch": RUNTIME.poller.killswitch if RUNTIME.poller else None,
        "alertmanagerErrors": RUNTIME.poller.errors if RUNTIME.poller else 0,
    }
