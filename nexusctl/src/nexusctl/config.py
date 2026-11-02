"""Configuration: a YAML file, then NEXUSCTL_* environment overrides (spec §16A "Configuration").
Secrets arrive only through the environment and are never read here."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path("~/.config/nexusctl/config.yaml").expanduser()


@dataclass(frozen=True)
class Config:
    incident_namespace: str = "nexus-system"
    approval_max_ttl: str = "15m"
    max_field_chars: int = 400
    color: str = "auto"


def load(path: Path | None = None, env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    data: dict[str, object] = {}
    p = path or Path(env.get("NEXUSCTL_CONFIG", str(DEFAULT_PATH)))
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        if isinstance(loaded, dict):
            data = loaded
    approval = data.get("approval")
    render = data.get("render")
    approval = approval if isinstance(approval, dict) else {}
    render = render if isinstance(render, dict) else {}
    return Config(
        incident_namespace=env.get(
            "NEXUSCTL_INCIDENT_NAMESPACE", str(data.get("incidentNamespace", "nexus-system"))),
        approval_max_ttl=env.get("NEXUSCTL_APPROVAL_MAXTTL", str(approval.get("maxTtl", "15m"))),
        max_field_chars=int(env.get("NEXUSCTL_RENDER_MAXFIELDCHARS",
                                    str(render.get("maxFieldChars", 400)))),
        color=env.get("NEXUSCTL_RENDER_COLOR", str(render.get("color", "auto"))),
    )
