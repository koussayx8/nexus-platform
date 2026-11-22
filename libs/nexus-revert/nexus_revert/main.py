"""Run C8 on its own, log-only (ADR-041 Block 6 checklist step 1: before the bot core, the bot pod
watches Incidents, opens the pull request and writes to its log). After #252 merges, the bot's
main loop calls Service.tick and routes `notify` to the chat as well."""

import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ConfigError, load
from .gitcmd import SubprocessGit
from .github import GitHub, HttpTransport
from .service import Service

log = logging.getLogger("nexus_revert")


class KubeIncidentReader:
    """In-cluster, as the bot's own ServiceAccount: list Incidents in nexus-system (read only)."""

    def __init__(self) -> None:
        from kubernetes import client, config

        config.load_incluster_config()  # type: ignore[no-untyped-call]
        self._api = client.CustomObjectsApi()

    def list_raw(self) -> list[dict[str, Any]]:
        data = self._api.list_namespaced_custom_object("nexus.io", "v1alpha1", "nexus-system", "incidents")
        items = data.get("items", []) if isinstance(data, dict) else []
        return [i for i in items if isinstance(i, dict)]


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        cfg = load()
    except ConfigError as e:
        log.error("config: %s", e)
        return 2
    scratch = Path(os.environ.get("NEXUS_REVERT_SCRATCH", "/tmp"))

    def git_factory(work: Path) -> SubprocessGit:
        return SubprocessGit(work, push_url=cfg.fork_url, login=cfg.machine, token=cfg.token)

    svc = Service(cfg, KubeIncidentReader(), GitHub(HttpTransport(cfg.token, cfg.github_api), cfg.upstream),
                  git_factory, lambda text: log.info("%s", text), now=lambda: datetime.now(UTC),
                  scratch=scratch)
    log.info("started: %d branch(es), mode %s, %d open revert pull request(s) recovered",
             len(cfg.branches), cfg.mode, svc.recover())
    while True:
        try:
            svc.tick()
        except Exception as e:  # one bad tick must not stop the loop
            log.warning("tick: %s", type(e).__name__)
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
