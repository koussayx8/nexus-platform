"""Configuration (spec section 16): everything that varies lives here, nothing in code.

The file is JSON, mounted from a ConfigMap (path in NEXUS_REASONER_CONFIG). API keys are never in
it: an entry names the environment variable that holds its key (`apiKeyEnv`), which a Secret the
owner creates provides. A missing key makes that provider unavailable (503), it never falls back
to another provider.

Named model configs and one switch (owner, 2026-11-20): `models` holds named entries (R2, r3-groq,
r3-primary, r3-backup-1, r3-backup-2, demo) and `active` maps an arm name the operator sends to the
entry that serves it, e.g. {"R3": "r3-groq"}. Switching R3 is a one-line, reviewed change to
`active` and a Reasoner restart; nothing switches by itself. An entry whose `model` is "" is not
filled in yet: it can be listed, never made active, and a call to it is `not_configured`. Each
entry may set its own `timeoutSeconds`, `maxOutputTokens` (a thinking model needs about 8K) and
`responseFormat` (json_schema | json_object | none; groq json_schema, nvidia none by default:
whether a NIM model honours json_schema is UNVERIFIED, as in experiments/bakeoff/models.yaml).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# groq and nvidia (NVIDIA NIM, https://integrate.api.nvidia.com/v1) are OpenAI-compatible; no
# anthropic: the Haiku arm was cut (TASKS.md D3, D10).
PROVIDERS = ("groq", "nvidia", "ollama")
RESPONSE_FORMATS = ("json_schema", "json_object", "none")
MAX_OUTPUT_TOKENS_MAX = 32768
# A model's call budget, retries included (ADR-035 addendum 2026-11-20): 30 s by default, at most
# 300 s (owner, 2026-11-20; was 180), the operator's own reasonerTimeoutSeconds bound.
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0
EFFORTS = ("low", "medium", "high")
MODES = ("rules", "llm", "adversarial")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelEntry:
    name: str  # R2, R3, demo
    provider: str
    model: str
    base_url: str
    api_key_env: str | None = None
    reasoning_effort: str = "low"
    timeout_s: float = DEFAULT_TIMEOUT_S  # the call's whole budget, a retry included
    max_output_tokens: int = 1024
    response_format: str = "json_schema"

    @property
    def configured(self) -> bool:
        return bool(self.model)


@dataclass(frozen=True)
class Config:
    index_path: str
    score_floor: float
    host: str = "0.0.0.0"
    port: int = 8080
    timeout_s: float = DEFAULT_TIMEOUT_S  # the default for a model without its own
    max_output_tokens: int = 1024
    seed: int = 0
    models: dict[str, ModelEntry] = field(default_factory=dict)
    active: dict[str, str] = field(default_factory=dict)  # arm name -> entry name

    def entry_for_model(self, model_id: str) -> ModelEntry | None:
        for e in self.models.values():
            if e.configured and e.model == model_id:
                return e
        return None

    def resolve(self, requested: str) -> ModelEntry | None:
        """The entry a request's `model` names: an arm of `active` (R3), an entry's name, or a
        model id (as the operator sent before named configs); None when nothing matches."""
        if requested in self.active:
            return self.models[self.active[requested]]
        if requested in self.models:
            return self.models[requested]
        return self.entry_for_model(requested)


def _timeout(value, where: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < value <= MAX_TIMEOUT_S
    ):
        raise ConfigError(
            f"{where}: timeoutSeconds must be a number in (0, {MAX_TIMEOUT_S:g}]"
        )
    return float(value)


def _output_cap(value, where: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_OUTPUT_TOKENS_MAX
    ):
        raise ConfigError(
            f"{where}: maxOutputTokens must be an integer in [1, {MAX_OUTPUT_TOKENS_MAX}]"
        )
    return value


def _response_format(m: dict, where: str) -> str:
    default = "none" if m.get("provider") == "nvidia" else "json_schema"
    value = m.get("responseFormat", default)
    if value not in RESPONSE_FORMATS:
        raise ConfigError(f"{where}: responseFormat must be one of {RESPONSE_FORMATS}")
    return value


def parse(doc: dict) -> Config:
    if not isinstance(doc, dict):
        raise ConfigError("config must be an object")
    for key in ("indexPath", "scoreFloor", "models"):
        if key not in doc:
            raise ConfigError(f"config missing {key}")
    floor = doc["scoreFloor"]
    if isinstance(floor, bool) or not isinstance(floor, int | float) or floor < 0:
        raise ConfigError("scoreFloor must be a number >= 0")
    default_timeout = _timeout(doc.get("timeoutSeconds", DEFAULT_TIMEOUT_S), "config")
    default_cap = _output_cap(doc.get("maxOutputTokens", 1024), "config")
    models: dict[str, ModelEntry] = {}
    for name, m in doc["models"].items():
        if m.get("provider") not in PROVIDERS:
            raise ConfigError(f"{name}: provider must be one of {PROVIDERS}")
        if not isinstance(m.get("model"), str):
            raise ConfigError(f'{name}: model id missing ("" when not filled in yet)')
        if m["provider"] != "ollama" and not m.get("apiKeyEnv"):
            raise ConfigError(
                f"{name}: apiKeyEnv missing (the variable name, never the key)"
            )
        effort = m.get("reasoningEffort", "low")
        if effort not in EFFORTS:
            raise ConfigError(f"{name}: reasoningEffort must be one of {EFFORTS}")
        models[name] = ModelEntry(
            name,
            m["provider"],
            m["model"],
            m.get("baseUrl", ""),
            m.get("apiKeyEnv"),
            effort,
            _timeout(m.get("timeoutSeconds", default_timeout), name),
            _output_cap(m.get("maxOutputTokens", default_cap), name),
            _response_format(m, name),
        )
    active = doc.get("active", {})
    if not isinstance(active, dict):
        raise ConfigError("active must be an object: arm name -> model config name")
    for arm, target in active.items():
        if arm in models:
            raise ConfigError(f"active: {arm!r} is also a model config name")
        if target not in models:
            raise ConfigError(f"active: {arm} -> {target!r}, which is not in models")
        if not models[target].configured:
            raise ConfigError(
                f"active: {arm} -> {target!r}, whose model id is not filled in"
            )
    return Config(
        index_path=doc["indexPath"],
        score_floor=float(floor),
        host=doc.get("host", "0.0.0.0"),
        port=int(doc.get("port", 8080)),
        timeout_s=default_timeout,
        max_output_tokens=default_cap,
        seed=int(doc.get("seed", 0)),
        models=models,
        active=dict(active),
    )


def load(path: str | None = None) -> Config:
    path = path or os.environ.get(
        "NEXUS_REASONER_CONFIG", "/etc/nexus-reasoner/reasoner.json"
    )
    with open(path) as f:
        return parse(json.load(f))
