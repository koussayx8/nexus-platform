"""Configuration (spec section 16): everything that varies lives here, nothing in code.

The file is JSON, mounted from a ConfigMap (path in NEXUS_REASONER_CONFIG). API keys are never in
it: an entry names the environment variable that holds its key (`apiKeyEnv`), which a Secret the
owner creates provides. A missing key makes that provider unavailable (503), it never falls back
to another provider.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

PROVIDERS = ("groq", "ollama")  # no anthropic: the Haiku arm was cut (TASKS.md D3, D10)
# A model's call budget, retries included (ADR-035 addendum 2026-11-20): 30 s by default, at most
# 180 s, the operator's own reasonerTimeoutSeconds bound.
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 180.0
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

    def entry_for_model(self, model_id: str) -> ModelEntry | None:
        for e in self.models.values():
            if e.model == model_id:
                return e
        return None


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
    models: dict[str, ModelEntry] = {}
    for name, m in doc["models"].items():
        if m.get("provider") not in PROVIDERS:
            raise ConfigError(f"{name}: provider must be one of {PROVIDERS}")
        if not m.get("model"):
            raise ConfigError(f"{name}: model id missing")
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
        )
    return Config(
        index_path=doc["indexPath"],
        score_floor=float(floor),
        host=doc.get("host", "0.0.0.0"),
        port=int(doc.get("port", 8080)),
        timeout_s=default_timeout,
        max_output_tokens=int(doc.get("maxOutputTokens", 1024)),
        seed=int(doc.get("seed", 0)),
        models=models,
    )


def load(path: str | None = None) -> Config:
    path = path or os.environ.get(
        "NEXUS_REASONER_CONFIG", "/etc/nexus-reasoner/reasoner.json"
    )
    with open(path) as f:
        return parse(json.load(f))
