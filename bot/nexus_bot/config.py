"""Configuration: one YAML file (a ConfigMap in Git) and one token file (a Secret outside Git).

The token is read from a file, never from an environment variable or argv, and never appears in
a log line or an error message. `Config.__repr__` leaves it out.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = "/etc/nexus-bot/config.yaml"
DEFAULT_TOKEN_FILE = "/var/run/secrets/nexus-bot/telegram/token"
NAMESPACES = ("nexus-dev", "nexus-prod")  # D21: the only namespaces /pods and /status read

_TOKEN = re.compile(r"^[0-9]{5,16}:[A-Za-z0-9_-]{30,64}$")
_USERNAME = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$")


class ConfigError(Exception):
    """The configuration is unusable. Messages never contain the token."""


@dataclass(frozen=True)
class Config:
    allowlist: dict[int, str]  # Telegram user ID -> the approver username impersonated for it
    approver_group: str = "nexus-approvers"
    namespaces: tuple[str, ...] = NAMESPACES
    poll_seconds: float = 10.0
    catch_up_minutes: float = 15.0
    token: str = field(default="", repr=False)


def _allowlist(raw: Any) -> dict[int, str]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("allowlist must map at least one Telegram user ID to an approver username")
    out: dict[int, str] = {}
    for key, user in raw.items():
        text = str(key)
        if not text.isdigit() or int(text) <= 0:
            raise ConfigError("allowlist keys must be positive numeric Telegram user IDs")
        if not isinstance(user, str) or not _USERNAME.fullmatch(user):
            raise ConfigError(f"allowlist entry {text}: the approver username is not a valid name")
        out[int(text)] = user
    return out


def parse(data: Any, token: str) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("the configuration must be a mapping")
    if not _TOKEN.fullmatch(token):
        raise ConfigError("the token file does not hold a Telegram bot token")
    group = data.get("approverGroup", "nexus-approvers")
    if not isinstance(group, str) or not _USERNAME.fullmatch(group):
        raise ConfigError("approverGroup is not a valid name")
    namespaces = tuple(data.get("namespaces", NAMESPACES))
    if not namespaces or any(ns not in NAMESPACES for ns in namespaces):
        raise ConfigError(f"namespaces must be a subset of {', '.join(NAMESPACES)}")
    poll = float(data.get("pollSeconds", 10))
    if not 2 <= poll <= 300:
        raise ConfigError("pollSeconds must be between 2 and 300")
    catch_up = float(data.get("catchUpMinutes", 15))
    if not 0 <= catch_up <= 60:
        raise ConfigError("catchUpMinutes must be between 0 and 60")
    return Config(allowlist=_allowlist(data.get("allowlist")), approver_group=group,
                  namespaces=namespaces, poll_seconds=poll, catch_up_minutes=catch_up, token=token)


def load(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    config_path = Path(env.get("NEXUS_BOT_CONFIG", DEFAULT_CONFIG))
    token_path = Path(env.get("NEXUS_BOT_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    try:
        data = yaml.safe_load(config_path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {config_path}: {type(e).__name__}") from None
    try:
        token = token_path.read_text().strip()
    except OSError as e:
        # the exception text is a path and an errno, but stay terse: the path names a Secret
        raise ConfigError(f"cannot read the token file: {type(e).__name__}") from None
    return parse(data, token)
