"""The Model Adapter (spec section 16): one call, one attempt, no retry, no tool loop.

It transports the two prompt strings to the configured provider and returns the final message
text. Settings are the spec's: temperature 0, at most 1,024 output tokens, JSON-schema output where
the provider supports it, a seed where supported, a 30 s timeout. Reasoning traces are requested
hidden where the provider has the option and are never read or stored.

Providers: groq (R2, R3; OpenAI-compatible), anthropic (the Haiku arm), ollama (offline demo
fallback, not an experimental arm). Everything provider-specific (URL, model id, key variable,
effort) comes from config. The key is read from the environment when a call is made and appears
nowhere else: not in errors, logs or results.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Config, ModelEntry
from .prompt import PROPOSAL_SCHEMA


class AdapterError(Exception):
    """reason: no_credentials | not_configured | timeout | transport | http_<code> | bad_response"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Completion:
    text: str
    model: str
    usage: dict
    settings: dict


def _post(url: str, headers: dict, body: dict, timeout: float, opener) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AdapterError(f"http_{e.code}") from None
    except TimeoutError:
        raise AdapterError("timeout") from None
    except urllib.error.URLError as e:
        raise AdapterError(
            "timeout"
            if isinstance(e.reason, TimeoutError | socket.timeout)
            else "transport"
        ) from None
    except (ValueError, OSError):
        raise AdapterError("bad_response") from None


def _key(entry: ModelEntry) -> str:
    key = os.environ.get(entry.api_key_env or "", "")
    if not key:
        raise AdapterError("no_credentials")
    return key


def complete(
    cfg: Config,
    entry: ModelEntry,
    system: str,
    user: str,
    effort: str | None = None,
    opener=None,
) -> Completion:
    effort = effort or entry.reasoning_effort
    if not entry.base_url:
        raise AdapterError("not_configured")
    base = entry.base_url.rstrip("/")
    settings = {
        "temperature": 0,
        "max_output_tokens": cfg.max_output_tokens,
        "seed": cfg.seed,
        "timeout_s": cfg.timeout_s,
        "attempts": 1,
    }
    try:
        if entry.provider == "groq":
            body = {
                "model": entry.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "max_completion_tokens": cfg.max_output_tokens,
                "seed": cfg.seed,
                "reasoning_effort": effort,
                "include_reasoning": False,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "proposal_v1", "schema": PROPOSAL_SCHEMA},
                },
            }
            doc = _post(
                f"{base}/chat/completions",
                {"Authorization": f"Bearer {_key(entry)}"},
                body,
                cfg.timeout_s,
                opener,
            )
            text = doc["choices"][0]["message"]["content"]
            usage = doc.get("usage") or {}
            settings["reasoning_effort"] = effort
        elif entry.provider == "anthropic":
            # No seed and no schema output on this API; the schema is in the prompt. The effort
            # control is not sent: how the Haiku arm sets it is fixed in the M4 pre-registration.
            body = {
                "model": entry.model,
                "max_tokens": cfg.max_output_tokens,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
            headers = {"x-api-key": _key(entry), "anthropic-version": "2023-06-01"}
            doc = _post(f"{base}/v1/messages", headers, body, cfg.timeout_s, opener)
            text = "".join(
                b.get("text", "") for b in doc["content"] if b.get("type") == "text"
            )
            usage = doc.get("usage") or {}
            settings["seed"] = None
            settings["reasoning_effort"] = None
        elif entry.provider == "ollama":
            body = {
                "model": entry.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": PROPOSAL_SCHEMA,
                "options": {
                    "temperature": 0,
                    "seed": cfg.seed,
                    "num_predict": cfg.max_output_tokens,
                },
            }
            doc = _post(f"{base}/api/chat", {}, body, cfg.timeout_s, opener)
            text = doc["message"]["content"]
            usage = {k: doc[k] for k in ("prompt_eval_count", "eval_count") if k in doc}
            settings["reasoning_effort"] = None
        else:  # parse() rejects other providers
            raise AdapterError("not_configured")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AdapterError("bad_response") from None
    if not isinstance(text, str):
        raise AdapterError("bad_response")
    return Completion(text=text, model=entry.model, usage=usage, settings=settings)


def parse_json(text: str):
    """The model's message as JSON. Only a Markdown fence around the object is tolerated."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return json.loads(t)
