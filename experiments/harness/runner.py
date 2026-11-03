#!/usr/bin/env python3
"""Experiment Runner (spec section 20, D17): inject, reset, ArgoCD refresh. Standard library.

The runner is the experiment system, outside the system under test. It acts as Cluster Admin
and is run by the owner; nothing here is reachable from the operator or the Reasoner.

Safe by default: every operation first builds a *plan*, a list of Steps (argv + whether it
mutates). Without --execute the plan is printed and nothing runs. With --execute every
step runs through the injected `exec`. The cluster CLI is always called with the context
named: `<cli> --context <CONTEXT> [-n <ns>] ...` (CLAUDE.md rule 12).

  runner.py plan inject S1 --context C           print the steps
  runner.py run  inject S1 --context C --execute
  runner.py run  reset --level 3 --context C --git-dir DIR --execute
  runner.py run  refresh --context C --execute

Git is never force-pushed (ADR-013): reset is a forward commit on experiment/dev-state.
Live behaviour is "live check pending": no cluster was available when this was written;
the tests run the plans against a fake `exec`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

CLI = "kubectl"
NS_DEV = "nexus-dev"
NS_DATA = "nexus-data"
NS_SYSTEM = "nexus-system"
APPS = ("sample-api-dev", "dependency-db-dev")
BRANCH = "experiment/dev-state"
LEVEL_LABEL = "nexus.io/autonomy-level"
APP_PORT = 8000

HANG_PY = (
    "import urllib.request as u;"
    f"u.urlopen(u.Request('http://127.0.0.1:{APP_PORT}/fault/hang',method='POST'),timeout=3)"
)
INJECT_LOGS_PY = (
    "import urllib.request as u;"
    f"u.urlopen(u.Request('http://127.0.0.1:{APP_PORT}/fault/inject-logs',method='POST'),timeout=5)"
)
S5_SQL = (
    "ALTER ROLE app_dev NOLOGIN; "
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='app_dev';"
)
S5_RESTORE_SQL = "ALTER ROLE app_dev LOGIN;"


@dataclass(frozen=True)
class Step:
    argv: tuple[str, ...]
    mutates: bool = True
    note: str = ""
    ignore_failure: bool = False  # e.g. S1's hanging request is cut by its own timeout


class Runner:
    def __init__(
        self,
        context,
        exec_=None,
        sleep=time.sleep,
        locust=None,
        git_dir=None,
        http_post=None,
    ):
        if not context:
            raise ValueError("a cluster context is required")
        self.context = context
        self.exec = exec_ or _subprocess_exec
        self.sleep = sleep
        self.locust = locust
        self.git_dir = git_dir
        self.http_post = http_post or _post_form

    # ---------------------------------------------------------------- helpers
    def k(
        self,
        *args: str,
        ns: str | None = None,
        note: str = "",
        ignore_failure: bool = False,
    ) -> Step:
        base = [CLI, "--context", self.context]
        if ns:
            base += ["-n", ns]
        return Step(tuple(base + list(args)), True, note, ignore_failure)

    def pods(self, ns: str, label: str) -> list[str]:
        rc, out = self.exec(
            [CLI, "--context", self.context, "-n", ns, "get", "pods", "-l", label]
            + ["-o", "jsonpath={.items[*].metadata.name}"]
        )
        if rc != 0:
            raise RuntimeError(f"cannot list pods ({label}) in {ns}")
        return out.split()

    # ---------------------------------------------------------------- plans
    def plan_inject(
        self, scenario: str, s3_patch: str | None = None, users: int | None = None
    ):
        """Steps that start a fault. HTTP posts to Locust are Steps whose argv[0] is POST."""
        sc = scenario.upper()
        sample = "app.kubernetes.io/name=sample-api"
        if sc == "S1":
            return [
                self.k(
                    "exec",
                    p,
                    "--",
                    "python",
                    "-c",
                    HANG_PY,
                    ns=NS_DEV,
                    note=f"S1 hang on {p}",
                    ignore_failure=True,
                )
                for p in self.pods(NS_DEV, sample)
            ]
        if sc in ("S2", "S4", "S6"):
            if not self.locust or users is None:
                raise ValueError(
                    f"{sc} needs --locust URL and --users (from the calibrated capacity)"
                )
            steps = [
                Step(
                    ("POST", f"{self.locust}/swarm", f"user_count={users}"),
                    True,
                    f"{sc} load",
                )
            ]
            if sc == "S6":
                steps += [
                    self.k(
                        "exec",
                        p,
                        "--",
                        "python",
                        "-c",
                        INJECT_LOGS_PY,
                        ns=NS_DEV,
                        note=f"S6 injected text on {p}",
                    )
                    for p in self.pods(NS_DEV, sample)
                ]
            return steps
        if sc == "S3":
            if not (self.git_dir and s3_patch):
                raise ValueError("S3 needs --git-dir and --s3-patch")
            return [
                Step(
                    ("git", "apply", s3_patch), True, "S3 bad config (fails readiness)"
                ),
                Step(("git", "add", "-A"), True),
                Step(
                    ("git", "commit", "-m", "chore(exp): inject S3 bad rollout"), True
                ),
                Step(
                    ("git", "push", "origin", f"HEAD:{BRANCH}"),
                    True,
                    "forward push, never forced (ADR-013)",
                ),
            ]
        if sc == "S5":
            pod = self.pods(NS_DATA, "app.kubernetes.io/name=dependency-db")[0]
            return [
                self.k("exec", pod, "-c", "dependency-db", "--", "psql", "-U", "postgres", "-c", S5_SQL,
                       ns=NS_DATA, note="S5 NOLOGIN + terminate sessions")
            ]  # fmt: skip
        raise ValueError(f"unknown scenario {scenario}")

    def plan_refresh(self):
        """ArgoCD hard refresh by annotation: no sync (selfHeal syncs by itself)."""
        return [
            self.k("annotate", "application", app, "argocd.argoproj.io/refresh=hard", "--overwrite",
                   ns="argocd", note="ArgoCD refresh")
            for app in APPS
        ]  # fmt: skip

    def plan_operator_config(self, advisory: str, breaker_epoch: int):
        """Write advisoryChecks and breakerEpoch into the admin-owned ConfigMap, then restart the
        operator, which reads its configuration at startup (ADR-025). The ConfigMap is created by
        bootstrap, never by ArgoCD (template header), so this is not an ArgoCD-managed resource.
        advisoryChecks is "off" only in phase D (spec line 394); the caller decides, the batch driver
        refuses "off" for any other cell."""
        if advisory not in ("on", "off"):
            raise ValueError(f"advisoryChecks must be on or off, not {advisory!r}")
        if not isinstance(breaker_epoch, int) or breaker_epoch < 0:
            raise ValueError(
                "breakerEpoch must be a non-negative integer (Unix seconds)"
            )
        patch = json.dumps(
            {"data": {"advisoryChecks": advisory, "breakerEpoch": str(breaker_epoch)}}
        )
        return [
            self.k("patch", "configmap", "nexus-operator-config", "--type", "merge", "-p", patch,
                   ns=NS_SYSTEM, note=f"advisoryChecks={advisory}, breakerEpoch={breaker_epoch}"),
            self.k("rollout", "restart", "deployment/nexus-operator", ns=NS_SYSTEM,
                   note="the operator reads its configuration at startup"),
            self.k("rollout", "status", "deployment/nexus-operator", "--timeout=180s", ns=NS_SYSTEM),
        ]  # fmt: skip

    def plan_reset(
        self,
        level: int,
        baseline_ref: str | None = None,
        advisory: str = "on",
        breaker_epoch: int | None = None,
    ):
        """Reset, in the order of spec section 20. With breaker_epoch, the last step also sets
        breakerEpoch to now and advisoryChecks to the run's value (back to "on" after phase D)."""
        steps: list[Step] = []
        if self.git_dir:
            steps += [
                Step(("git", "fetch", "origin", BRANCH), False),
                Step(("git", "checkout", BRANCH), True),
            ]
            if baseline_ref:
                # Forward commit restoring the baseline tree; skipped when there is no diff.
                steps += [
                    Step(("git", "restore", f"--source={baseline_ref}", "--staged", "--worktree", "--", "."), True),
                    Step(("git", "commit", "-m", "chore(exp): reset to baseline"), True, "skipped when no diff", True),
                    Step(("git", "push", "origin", f"HEAD:{BRANCH}"), True, "forward push, never forced"),
                ]  # fmt: skip
        steps += self.plan_refresh()
        steps += [
            self.k("rollout", "restart", "deployment/sample-api", ns=NS_DEV, note="replace pods"),
            self.k("scale", "deployment/sample-api", "--replicas=2", ns=NS_DEV),
            self.k("exec", "dependency-db-0", "-c", "dependency-db", "--", "psql", "-U", "postgres", "-c",
                   S5_RESTORE_SQL, ns=NS_DATA, note="restore role"),
        ]  # fmt: skip
        if self.locust:
            steps.append(
                Step(("POST", f"{self.locust}/stop", ""), True, "Locust to idle")
            )
        steps.append(
            self.k(
                "label",
                "namespace",
                NS_DEV,
                f"{LEVEL_LABEL}={level}",
                "--overwrite",
                note="block's level",
            )
        )
        if breaker_epoch is not None:
            steps += self.plan_operator_config(advisory, breaker_epoch)
        return steps

    # ---------------------------------------------------------------- execution
    def execute(self, steps: list[Step]) -> list[dict]:
        log = []
        for s in steps:
            if s.argv[0] == "POST":
                _, url, body = s.argv
                self.http_post(
                    url,
                    urllib.parse.parse_qs(body, keep_blank_values=True) if body else {},
                )
                rc = 0
            elif s.argv[0] == "git":
                rc, _ = self.exec(list(s.argv), cwd=self.git_dir)
            else:
                rc, _ = self.exec(list(s.argv))
            log.append({"step": asdict(s), "rc": rc})
            if rc != 0 and not s.ignore_failure:
                raise RuntimeError(f"step failed (rc={rc}): {' '.join(s.argv)}")
        return log

    def wait_synced(self, timeout_s: int = 600, poll_s: int = 10) -> bool:
        """All Applications Synced and Healthy (read-only). False on timeout."""
        deadline = time.monotonic() + timeout_s
        while True:
            ok = True
            for app in APPS:
                rc, out = self.exec(
                    [
                        CLI,
                        "--context",
                        self.context,
                        "-n",
                        "argocd",
                        "get",
                        "application",
                        app,
                    ]
                    + ["-o", "jsonpath={.status.sync.status}/{.status.health.status}"]
                )
                ok = ok and rc == 0 and out.strip() == "Synced/Healthy"
            if ok:
                return True
            if time.monotonic() >= deadline:
                return False
            self.sleep(poll_s)


def _subprocess_exec(argv, cwd=None, timeout=120):
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    return p.returncode, p.stdout


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields, doseq=True).encode()
    with urllib.request.urlopen(
        urllib.request.Request(url, data=data, method="POST"), timeout=30
    ) as r:
        r.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("plan", "run"))
    ap.add_argument("op", choices=("inject", "reset", "refresh"))
    ap.add_argument("scenario", nargs="?")
    ap.add_argument("--context", required=True)
    ap.add_argument("--level", type=int, choices=(0, 1, 2, 3), default=3)
    ap.add_argument("--locust")
    ap.add_argument("--users", type=int)
    ap.add_argument("--git-dir")
    ap.add_argument("--s3-patch")
    ap.add_argument("--baseline-ref")
    ap.add_argument(
        "--execute", action="store_true", help="actually run the steps (mode run only)"
    )
    a = ap.parse_args(argv)
    r = Runner(a.context, locust=a.locust, git_dir=a.git_dir)
    if a.op == "inject":
        if not a.scenario:
            ap.error("inject needs a scenario")
        steps = r.plan_inject(a.scenario, a.s3_patch, a.users)
    elif a.op == "reset":
        steps = r.plan_reset(a.level, a.baseline_ref)
    else:
        steps = r.plan_refresh()
    if a.mode == "plan" or not a.execute:
        print(json.dumps([asdict(s) for s in steps], indent=1))
        return 0
    print(json.dumps(r.execute(steps), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
