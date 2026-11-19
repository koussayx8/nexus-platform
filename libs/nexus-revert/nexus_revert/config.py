"""Configuration: a YAML file (the bot's ConfigMap, in Git) and the GitHub token file (a Secret).

The branch map is static (ADR-041 decision 5). The token is read from a file and never appears in
a log line, an error message or `repr`.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = "/etc/nexus-bot/revert.yaml"
DEFAULT_TOKEN_FILE = "/var/run/secrets/nexus-bot/github/token"

_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
_PATH = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,254}")
_GLOB = re.compile(r"[A-Za-z0-9._*-]+(?:/[A-Za-z0-9._*-]+)*")
_NAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_EMAIL = re.compile(r"[^\s@<>]+@[^\s@<>]+")
MODES = ("pull_request", "compare_link")


class ConfigError(Exception):
    """The configuration is unusable. Messages never contain the token."""


@dataclass(frozen=True)
class Target:
    branch: str  # the branch the namespace's Argo CD application tracks
    overlay: str  # the application's path; every file a reverted commit changes must be under it
    application: str
    # check 4b: every changed file must match one of `allow` (fnmatch, same path depth), and none may
    # be in `exclude` (the Namespace and its autonomy level). Empty `allow` admits nothing.
    allow: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    # check 4c: the one `images` entry (by name) whose newName/newTag/digest a revert may change
    image: str | None = None


@dataclass(frozen=True)
class Config:
    upstream: str
    machine: str
    fork: str
    branches: dict[str, Target]
    mode: str = "pull_request"
    fetch_depth: int = 50
    max_age_hours: float = 24
    poll_seconds: float = 30
    merge_timeout_hours: float = 24
    catch_up_minutes: float = 60
    author_name: str = "nexus-revert"
    author_email: str = "nexus-revert@users.noreply.github.com"
    github_api: str = "https://api.github.com"
    git_base: str = "https://github.com"
    token: str = field(default="", repr=False)

    @property
    def upstream_url(self) -> str:
        return f"{self.git_base}/{self.upstream}.git"

    @property
    def fork_url(self) -> str:
        return f"{self.git_base}/{self.fork}.git"


def _check(rx: re.Pattern[str], value: Any, what: str) -> str:
    if not isinstance(value, str) or not rx.fullmatch(value) or ".." in value:
        raise ConfigError(f"{what} is not valid")
    return value


def _target(ns: str, raw: Any) -> Target:
    if not isinstance(raw, dict):
        raise ConfigError(f"branches.{ns} must be a mapping")
    overlay = _check(_PATH, raw.get("overlay"), f"branches.{ns}.overlay")
    exclude = tuple(_check(_PATH, p, f"branches.{ns}.exclude") for p in raw.get("exclude") or [])
    allow = tuple(_check(_GLOB, p, f"branches.{ns}.allow") for p in raw.get("allow") or [])
    if not allow:
        raise ConfigError(f"branches.{ns}.allow must list the workload files a revert may touch")
    if any(not p.startswith(overlay + "/") for p in exclude + allow):
        raise ConfigError(f"branches.{ns}.allow and exclude entries must be under the overlay")
    image = raw.get("image")
    if image is not None:
        image = _check(_IMAGE, image, f"branches.{ns}.image")
    return Target(branch=_check(_BRANCH, raw.get("branch"), f"branches.{ns}.branch"), overlay=overlay,
                  application=_check(_NAME, raw.get("application"), f"branches.{ns}.application"),
                  allow=allow, exclude=exclude, image=image)


def parse(data: Any, token: str) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("the configuration must be a mapping")
    if not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token):
        raise ConfigError("the token file does not hold a GitHub token")
    machine = _check(_LOGIN, data.get("machineAccount"), "machineAccount")
    upstream = _check(_REPO, data.get("upstream", "koussayx8/nexus-platform"), "upstream")
    fork = _check(_REPO, data.get("fork", f"{machine}/{upstream.split('/')[1]}"), "fork")
    if fork.split("/")[0].lower() != machine.lower():
        raise ConfigError("the fork must be owned by the machine account")
    if fork.lower() == upstream.lower():
        raise ConfigError("the fork must not be the upstream repository")
    raw = data.get("branches")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("branches must map at least one namespace")
    branches = {_check(_NAME, ns, "a branches key"): _target(ns, t) for ns, t in raw.items()}
    mode = data.get("prMode", "pull_request")
    if mode not in MODES:
        raise ConfigError(f"prMode must be one of {', '.join(MODES)}")
    depth = int(data.get("fetchDepth", 50))
    if not 2 <= depth <= 200:
        raise ConfigError("fetchDepth must be between 2 and 200")
    max_age = float(data.get("maxAgeHours", 24))
    if not 1 <= max_age <= 168:
        raise ConfigError("maxAgeHours must be between 1 and 168")
    author = data.get("gitAuthor") or {}
    name = author.get("name", "nexus-revert")
    email = author.get("email", f"{machine}@users.noreply.github.com")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9 ._-]{1,64}", name):
        raise ConfigError("gitAuthor.name is not valid")
    if not isinstance(email, str) or not _EMAIL.fullmatch(email):
        raise ConfigError("gitAuthor.email is not valid")
    return Config(upstream=upstream, machine=machine, fork=fork, branches=branches, mode=mode,
                  fetch_depth=depth, max_age_hours=max_age, poll_seconds=float(data.get("pollSeconds", 30)),
                  merge_timeout_hours=float(data.get("mergeTimeoutHours", 24)),
                  catch_up_minutes=float(data.get("catchUpMinutes", 60)),
                  author_name=name, author_email=email, token=token)


def load(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    path = Path(env.get("NEXUS_REVERT_CONFIG", DEFAULT_CONFIG))
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {path}: {type(e).__name__}") from None
    try:
        token = Path(env.get("NEXUS_REVERT_TOKEN_FILE", DEFAULT_TOKEN_FILE)).read_text().strip()
    except OSError as e:
        raise ConfigError(f"cannot read the token file: {type(e).__name__}") from None
    return parse(data, token)
