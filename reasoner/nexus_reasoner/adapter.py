"""The Model Adapter (spec section 16): one call, no tool loop; at most one retry.

It transports the two prompt strings to the configured provider and returns the final message
text. Settings are the spec's: temperature 0, at most 1,024 output tokens, JSON-schema output where
the provider supports it, a seed where supported. Reasoning traces are requested hidden where the
provider has the option and are never read or stored.

Time (ADR-035 addendum, 2026-11-20): the model's `timeoutSeconds` (default 30 s, at most 180 s) is
the whole call's budget. A transient failure (a network error, HTTP 5xx, or HTTP 429 with a
Retry-After) is retried once, after Retry-After (429) or RETRY_GAP_S, only when at least
MIN_ATTEMPT_S of the budget would remain for the second attempt. A timeout is never retried: it
used the budget. A 429 without Retry-After is not retried. Anything else fails at once, and the
operator falls back to R1 as before.

Providers: groq (R2, R3 today) and nvidia (NVIDIA NIM, the R3 candidates of the bake-off), both
OpenAI-compatible /chat/completions, and ollama (offline demo fallback, not an experimental arm).
Groq gets `reasoning_effort`, `include_reasoning: false` and `max_completion_tokens`; NIM gets
`max_tokens` and none of Groq's options. The answer is `message.content`; a NIM thinking model's
trace is `message.reasoning_content`, returned beside it as untrusted text (Completion.reasoning)
and never parsed. The anthropic provider served the Haiku arm only and went with it (TASKS.md D3,
D10). Everything provider-specific (URL, model id, key variable, effort) comes from config. The key is read from the environment when a call is made and appears
nowhere else: not in errors, logs or results.
"""

from __future__ import annotations

import email.utils
import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Config, ModelEntry
from .prompt import PROPOSAL_SCHEMA


class AdapterError(Exception):
    """reason: no_credentials | not_configured | timeout | transport | http_<code> | bad_response.
    retry_after: the provider's Retry-After in seconds (an HTTP error that sent one), else None."""

    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


RETRY_GAP_S = 1.0  # before retrying a network error or a 5xx
MIN_ATTEMPT_S = 5.0  # a second attempt needs at least this much budget left


def retry_after(value, now: float | None = None) -> float | None:
    """Retry-After as seconds: a non-negative number, or an HTTP date (RFC 9110) from `now`
    (Unix seconds, default the current time). None when absent or unreadable."""
    if value is None:
        return None
    text = str(value).strip()
    try:
        secs = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None or when.tzinfo is None:
            return None
        secs = when.timestamp() - (time.time() if now is None else now)
    if not math.isfinite(secs):
        return None
    return max(0.0, secs)


def retry_wait(e: AdapterError) -> float | None:
    """How long to wait before the one retry, or None when the failure is not transient."""
    if e.reason == "transport":
        return RETRY_GAP_S
    if e.reason == "http_429":
        return e.retry_after  # None without a Retry-After: not retried
    if e.reason.startswith("http_5") and len(e.reason) == 8:
        return RETRY_GAP_S
    return None


@dataclass
class Completion:
    text: str
    model: str
    usage: dict
    settings: dict
    reasoning: str | None = (
        None  # untrusted model text, never parsed or used for a decision
    )


# Sent on every provider call. Without it urllib sends "Python-urllib/3.x", which Groq's edge
# refuses with HTTP 403 (Cloudflare error 1010, a blocked client signature): every live R3 call of
# 2026-11-17 failed that way while curl, with the same key, got 200.
USER_AGENT = "nexus-reasoner/1.0"


def _post(url: str, headers: dict, body: dict, timeout: float, opener) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            **headers,
        },
        method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        headers = e.headers if e.headers is not None else {}
        raise AdapterError(
            f"http_{e.code}", retry_after(headers.get("Retry-After"))
        ) from None
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
    *,
    clock=time.monotonic,
    sleep=time.sleep,
) -> Completion:
    """One call within the model's budget: a second attempt only for a transient failure and
    only when the wait leaves MIN_ATTEMPT_S of it (module docstring)."""
    deadline = clock() + entry.timeout_s
    try:
        return _attempt(cfg, entry, system, user, effort, opener, entry.timeout_s, 1)
    except AdapterError as e:
        wait = retry_wait(e)
        if wait is None or deadline - clock() - wait < MIN_ATTEMPT_S:
            raise
    sleep(wait)
    return _attempt(cfg, entry, system, user, effort, opener, deadline - clock(), 2)


def _attempt(
    cfg: Config,
    entry: ModelEntry,
    system: str,
    user: str,
    effort: str | None,
    opener,
    timeout: float,
    attempt: int,
) -> Completion:
    effort = effort or entry.reasoning_effort
    if not entry.base_url or not entry.configured:
        raise AdapterError("not_configured")
    base = entry.base_url.rstrip("/")
    settings = {
        "temperature": 0,
        "max_output_tokens": entry.max_output_tokens,
        "seed": cfg.seed,
        "timeout_s": entry.timeout_s,
        "attempts": attempt,
    }
    try:
        if entry.provider in ("groq", "nvidia"):
            body: dict = {
                "model": entry.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "seed": cfg.seed,
            }
            if entry.provider == "groq":
                body |= {
                    "max_completion_tokens": entry.max_output_tokens,
                    "reasoning_effort": effort,
                    "include_reasoning": False,
                }
                settings["reasoning_effort"] = effort
            else:  # no include_reasoning or reasoning_effort outside Groq
                body["max_tokens"] = entry.max_output_tokens
                settings["reasoning_effort"] = None
            if entry.response_format == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "proposal_v1", "schema": PROPOSAL_SCHEMA},
                }
            elif entry.response_format == "json_object":
                body["response_format"] = {"type": "json_object"}
            settings["response_format"] = entry.response_format
            doc = _post(
                f"{base}/chat/completions",
                {"Authorization": f"Bearer {_key(entry)}"},
                body,
                timeout,
                opener,
            )
            message = doc["choices"][0]["message"]
            text = message["content"]
            reasoning = message.get("reasoning_content")
            usage = doc.get("usage") or {}
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
                    "num_predict": entry.max_output_tokens,
                },
            }
            doc = _post(f"{base}/api/chat", {}, body, timeout, opener)
            text = doc["message"]["content"]
            usage = {k: doc[k] for k in ("prompt_eval_count", "eval_count") if k in doc}
            reasoning = None
            settings["reasoning_effort"] = None
        else:  # parse() rejects other providers
            raise AdapterError("not_configured")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AdapterError("bad_response") from None
    if not isinstance(text, str):
        raise AdapterError("bad_response")
    if not isinstance(reasoning, str):
        reasoning = None
    return Completion(
        text=text,
        model=entry.model,
        usage=usage,
        settings=settings,
        reasoning=reasoning,
    )


def parse_json(text: str):
    """The model's message as JSON. Only a Markdown fence around the object is tolerated."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return json.loads(t)
