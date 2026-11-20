"""Model bake-off for the new R3: rank candidate models on the real trial bundles.

  python -m experiments.bakeoff.bakeoff run --bundles DIR --out DIR [--dry-run] [--show-thinking]
  python -m experiments.bakeoff.bakeoff run --round 2 --from DIR --bundles DIR --out DIR
  python -m experiments.bakeoff.bakeoff rank --out DIR

Round 1 sends exactly the Reasoner's prompt: retrieval over the baked index (top 3 at the score
floor, reasoner/nexus_reasoner/retrieval.py), then prompt.plan on the bundle with the Reasoner's
own output reservation (1,024 tokens), level L3 and catalogue version "1", as the operator sends.
The bake-off's larger budget (max_tokens) changes the call, never the prompt. Decision expansion
("(a)") is not merged on dev: round 1 has none; round 2 adds it.

Round 2 is the top 2 of round 1 again with a big prompt (~16K tokens): more log lines per pod, all
events, the top 5 chunks plus the Decision section of every runbook among them. It is NOT the
Reasoner's prompt and is labelled `big` in every record.

Only message.content is parsed for the proposal; the thinking (message.reasoning_content, or
message.reasoning on Groq) is measured and saved to reasoning/<model>-<bundle>-<n>.txt, never parsed.
Keys come from the environment variable each model names and appear nowhere: not in output,
records, errors or the reasoning files. Calls: one attempt each, a hard deadline per call (past it:
`hang`), at most requests_per_minute overall, models in parallel (one thread per model), or one at a
time with --show-thinking so the live trace stays readable.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

REPO = pathlib.Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "operator", REPO / "reasoner"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from nexus_operator import catalogue, proposal
from nexus_reasoner import adapter, prompt
from nexus_reasoner.retrieval import Retriever
from nexus_reasoner.service import resolves

from experiments.harness import bm25, metrics

HERE = pathlib.Path(__file__).resolve().parent
LEVEL = "L3"  # every trial bundle ran at L3
CATALOGUE_VERSION = "1"  # operator/nexus_operator/reasoner_client.py CATALOGUE_VERSION
REASONER_OUTPUT_TOKENS = (
    1024  # reasoner/k8s/configmap.yaml maxOutputTokens: what prompt.plan reserves
)
SCORE_FLOOR = 3.1739  # reasoner/k8s/configmap.yaml scoreFloor
CORPUS_HASH = "fad76a74d15f075c8a8818d63fbce6d9911cdb45161ffc4a0984ef53d917e6d8"  # live /healthz, 2026-11-19
USER_AGENT = (
    "nexus-bakeoff/1.0"  # urllib's default agent is refused by Groq's edge (adapter.py)
)
SEED = 0

BIG_BUDGET_TOKENS = 16_000
BIG_TOP_K = 5
# (log lines per pod, characters per line); every event is kept. The first that fits is used.
BIG_TRIM_LEVELS = ((200, 400), (150, 300), (120, 300), (90, 300), (60, 300), (40, 300))

CSV_FIELDS = (
    "round", "prompt_variant", "model", "model_id", "preview", "reference", "scenario",
    "repeat", "max_tokens", "timeout_s", "outcome", "http_status", "latency_s",
    "valid_json", "schema_valid", "refs_ok", "action", "fault_class", "recommendation_kind",
    "replicas", "action_ok", "fault_ok", "followed_injection", "correct", "prompt_tokens",
    "completion_tokens", "reasoning_tokens", "reasoning_chars", "prompt_estimate",
    "trim_level", "chunks", "error",
)  # fmt: skip

_SECRETISH = re.compile(r"(nvapi-|gsk_|sk-|Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True)
class Model:
    name: str
    provider: str
    base_url: str
    model: str
    key_env: str
    max_tokens: int = 8192
    timeout_s: float = 180.0
    preview: bool = False
    reference: bool = False
    response_format: str = "none"
    max_tokens_field: str = "max_tokens"
    min_interval_s: float = (
        0.0  # between two starts of this model's calls (Groq: tokens a minute)
    )
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Truth:
    action: str
    fault_class: str
    kind: str | None = None
    injected: bool = False


@dataclass(frozen=True)
class Bundle:
    scenario: str
    hash: str
    truth: Truth
    recorded: dict = field(default_factory=dict)
    incident_uid: str = ""


def _yaml(path):
    try:
        import yaml
    except ImportError:
        raise ConfigError(
            "PyYAML is needed to read the YAML files: pip install pyyaml==6.0.3"
        ) from None
    with open(path) as f:
        return yaml.safe_load(f)


def parse_models(doc) -> tuple[list[Model], dict]:
    if not isinstance(doc, dict) or not isinstance(doc.get("models"), list):
        raise ConfigError("models file: a `models` list is required")
    d = doc.get("defaults") or {}
    out, seen = [], set()
    for i, m in enumerate(doc["models"]):
        if not isinstance(m, dict):
            raise ConfigError(f"models[{i}] is not a mapping")
        for k in ("provider", "base_url", "model", "key_env"):
            if not isinstance(m.get(k), str) or not m[k]:
                raise ConfigError(f"models[{i}]: {k} is required")
        if m["provider"] not in ("nim", "groq"):
            raise ConfigError(f"models[{i}]: provider must be nim or groq")
        if m.get("response_format", "none") not in (
            "none",
            "json_object",
            "json_schema",
        ):
            raise ConfigError(
                f"models[{i}]: response_format must be none, json_object or json_schema"
            )
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", m["key_env"]):
            raise ConfigError(
                f"models[{i}]: key_env must be a variable NAME, never the key"
            )
        name = m.get("name") or m["model"]
        if name in seen:
            raise ConfigError(f"models[{i}]: duplicate name {name}")
        seen.add(name)
        extra = m.get("extra") or {}
        if not isinstance(extra, dict):
            raise ConfigError(f"models[{i}]: extra must be a mapping")
        out.append(
            Model(
                name=name, provider=m["provider"], base_url=m["base_url"].rstrip("/"),
                model=m["model"], key_env=m["key_env"],
                max_tokens=int(m.get("max_tokens", d.get("max_tokens", 8192))),
                timeout_s=float(m.get("timeout_s", d.get("timeout_s", 180))),
                preview=bool(m.get("preview", False)), reference=bool(m.get("reference", False)),
                response_format=m.get("response_format", "none"),
                max_tokens_field=m.get("max_tokens_field", "max_tokens"), extra=extra,
                min_interval_s=float(m.get("min_interval_s", 0)),
            )
        )  # fmt: skip
    defaults = {
        "repeats": int(d.get("repeats", 2)),
        "requests_per_minute": float(d.get("requests_per_minute", 40)),
    }
    if not out:
        raise ConfigError("models file: no model")
    return out, defaults


def parse_bundles(doc) -> list[Bundle]:
    if not isinstance(doc, dict) or not isinstance(doc.get("bundles"), list):
        raise ConfigError("bundles file: a `bundles` list is required")
    out = []
    for b in doc["bundles"]:
        t = b.get("truth") or {}
        if t.get("action") not in catalogue.CATALOGUE:
            raise ConfigError(f"{b.get('scenario')}: truth.action not in the catalogue")
        if t.get("fault_class") not in catalogue.FAULT_CLASSES:
            raise ConfigError(f"{b.get('scenario')}: truth.fault_class unknown")
        if (
            t.get("kind") is not None
            and t["kind"] not in catalogue.RECOMMENDATION_KINDS
        ):
            raise ConfigError(f"{b.get('scenario')}: truth.kind unknown")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(b.get("hash", ""))):
            raise ConfigError(f"{b.get('scenario')}: hash must be sha256:<64 hex>")
        out.append(
            Bundle(
                b["scenario"], b["hash"],
                Truth(t["action"], t["fault_class"], t.get("kind"), bool(t.get("injected"))),
                b.get("recorded") or {}, b.get("incident_uid", ""),
            )
        )  # fmt: skip
    return out


def load_bundle(directory, b: Bundle) -> dict:
    """The exported bundle, checked: valid JSON and the canonical hash the Incident recorded."""
    path = pathlib.Path(directory) / f"{b.scenario}.json"
    try:
        doc = json.loads(path.read_text())
    except OSError as e:
        raise ConfigError(f"{b.scenario}: cannot read {path}: {e.strerror}") from None
    except ValueError:
        raise ConfigError(f"{b.scenario}: {path} is not JSON") from None
    if not isinstance(doc, dict):
        raise ConfigError(f"{b.scenario}: {path} is not a JSON object")
    got = proposal.hash_of({k: v for k, v in doc.items() if k != "hash"})
    if doc.get("hash") != b.hash or got != b.hash:
        raise ConfigError(
            f"{b.scenario}: bundle hash {got} is not the recorded {b.hash}"
        )
    return doc


# ---------------------------------------------------------------- prompts


def load_index(path) -> Retriever:
    r = Retriever.load(str(path), SCORE_FLOOR)
    if r.corpus_hash != CORPUS_HASH:
        print(
            f"WARNING: index corpus hash {r.corpus_hash[:12]} is not the live {CORPUS_HASH[:12]}:"
            " the runbooks changed since the trial runs",
            file=sys.stderr,
        )
    return r


def reasoner_prompt(
    bundle: dict, retriever: Retriever
) -> tuple[prompt.Prompt, list[dict]]:
    """Round 1: what the Reasoner sends for this bundle (service.Reasoner._llm)."""
    chunks = retriever.retrieve(bundle)
    p = prompt.plan(
        bundle, chunks, LEVEL, CATALOGUE_VERSION, output_tokens=REASONER_OUTPUT_TOKENS
    )
    return p, chunks


def big_chunks(bundle: dict, retriever: Retriever) -> list[dict]:
    """The top BIG_TOP_K chunks at the floor, then the Decision section of each runbook among them."""
    ranked = sorted(
        retriever.index.score(bm25.build_query(bundle)), key=lambda x: (-x[1], x[0])
    )
    by_id = retriever._by_id
    top = [
        {**by_id[cid], "score": s}
        for cid, s in ranked[:BIG_TOP_K]
        if s >= SCORE_FLOOR and s > 0
    ]
    have = {c["id"] for c in top}
    for rb in dict.fromkeys(c["runbook"] for c in top):
        for c in retriever.index.chunks:
            if (
                c["runbook"] == rb
                and c.get("heading") == "Decision"
                and c["id"] not in have
            ):
                top.append({**by_id[c["id"]], "score": None})
                have.add(c["id"])
    return top


def big_prompt(bundle: dict, retriever: Retriever) -> tuple[prompt.Prompt, list[dict]]:
    """Round 2: the Reasoner's rules, catalogue, schema and layout, with a ~16K-token excerpt."""
    chunks = big_chunks(bundle, retriever)
    segments = prompt._segments(bundle)
    events = bundle.get("events") if isinstance(bundle.get("events"), list) else []
    found = None
    for n, (per_pod, line_chars) in enumerate(BIG_TRIM_LEVELS):
        logs, stats = prompt._excerpt(segments, per_pod, line_chars)
        view = {k: v for k, v in bundle.items() if k not in ("logs", "events")}
        view["events"] = events
        sel = {"pods": stats, "eventsShown": len(events), "eventsTotal": len(events)}
        view["logs"] = logs | {"excerpt": sel}
        user = prompt._user(view, chunks, LEVEL, CATALOGUE_VERSION)
        est = prompt.estimate_tokens(prompt.RULES) + prompt.estimate_tokens(user)
        found = prompt.Prompt(prompt.RULES, user, est, n, sel)
        if est <= BIG_BUDGET_TOKENS:
            break
    return found, chunks


# ---------------------------------------------------------------- calls


class RateLimiter:
    """At most `per_minute` request starts per minute, across every thread."""

    def __init__(self, per_minute: float, clock=time.monotonic, sleep=time.sleep):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = self.clock()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        if start > now:
            self.sleep(start - now)


def redact(text: str) -> str:
    return _SECRETISH.sub(lambda m: m.group(1) + "[REDACTED]", text)


def request_body(m: Model, system: str, user: str, stream: bool) -> dict:
    body = {
        "model": m.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "seed": SEED,
        m.max_tokens_field: m.max_tokens,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    if m.response_format == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif m.response_format == "json_schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "proposal_v1", "schema": prompt.PROPOSAL_SCHEMA},
        }
    body.update(m.extra)
    return body


def _http_request(m: Model, body: dict, key: str) -> urllib.request.Request:
    return urllib.request.Request(
        f"{m.base_url}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if body.get("stream") else "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )


def _reasoning_of(obj: dict) -> str:
    v = obj.get("reasoning_content")
    if not isinstance(v, str):
        v = obj.get("reasoning")
    return v if isinstance(v, str) else ""


def _exchange(m, body, key, opener, cancel, on_reasoning, on_content) -> dict:
    """One HTTP exchange; returns {content, reasoning, usage} or raises adapter.AdapterError."""
    req = _http_request(m, body, key)
    try:
        resp = (opener or urllib.request.urlopen)(req, timeout=m.timeout_s)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - the status is what counts
            detail = ""
        err = adapter.AdapterError(f"http_{e.code}")
        err.status, err.detail = e.code, redact(detail)
        raise err from None
    except (TimeoutError, OSError) as e:
        reason = (
            "timeout"
            if "timed out" in str(e) or isinstance(e, TimeoutError)
            else "transport"
        )
        raise adapter.AdapterError(reason) from None
    with resp:
        if not body.get("stream"):
            try:
                doc = json.loads(resp.read())
                msg = doc["choices"][0]["message"]
            except (ValueError, KeyError, IndexError, TypeError, OSError):
                raise adapter.AdapterError("bad_response") from None
            content = msg.get("content")
            return {
                "content": content if isinstance(content, str) else "",
                "reasoning": _reasoning_of(msg),
                "usage": doc.get("usage") or {},
            }
        content, reasoning, usage = [], [], {}
        for raw in resp:
            if cancel.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                r = _reasoning_of(delta)
                if r:
                    reasoning.append(r)
                    on_reasoning(r)
                c = delta.get("content")
                if isinstance(c, str) and c:
                    content.append(c)
                    on_content(c)
        return {
            "content": "".join(content),
            "reasoning": "".join(reasoning),
            "usage": usage,
        }


def call(m: Model, system: str, user: str, *, stream=False, opener=None, env=None,
         on_reasoning=lambda s: None, on_content=lambda s: None) -> dict:  # fmt: skip
    """One attempt under a hard deadline (m.timeout_s). The key never leaves this function's
    request header. Returns outcome ok | no_credentials | hang | timeout | transport |
    bad_response | http_<code>, plus content, reasoning, usage, latency_s."""
    key = (os.environ if env is None else env).get(m.key_env, "")
    if not key:
        return {"outcome": "no_credentials", "latency_s": None}
    body = request_body(m, system, user, stream)
    box: dict = {}
    cancel = threading.Event()

    def run():
        try:
            box["result"] = _exchange(
                m, body, key, opener, cancel, on_reasoning, on_content
            )
        except adapter.AdapterError as e:
            box["error"] = e

    t0 = time.monotonic()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(m.timeout_s)
    latency = round(time.monotonic() - t0, 3)
    if th.is_alive():
        cancel.set()
        return {"outcome": "hang", "latency_s": latency}
    if "error" in box:
        e = box["error"]
        return {
            "outcome": e.reason,
            "http_status": getattr(e, "status", None),
            "error": getattr(e, "detail", ""),
            "latency_s": latency,
        }
    return {"outcome": "ok", "latency_s": latency, **box["result"]}


# ---------------------------------------------------------------- scoring


def score(content: str | None, bundle: dict, chunks: list[dict], truth: Truth) -> dict:
    """Judge message.content only. valid_json: it parses (a Markdown fence is tolerated, as the
    Reasoner does); schema_valid: proposal/v1 by the operator's own validator; refs_ok: the
    Reasoner's ref check (runbookRefs retrieved, evidenceRefs in the full bundle)."""
    out = {
        "valid_json": False, "schema_valid": False, "refs_ok": False, "action": None,
        "fault_class": None, "recommendation_kind": None, "replicas": None,
        "action_ok": False, "fault_ok": False, "followed_injection": False, "correct": False,
    }  # fmt: skip
    if not content:
        return out
    try:
        doc = adapter.parse_json(content)
    except ValueError:
        return out
    out["valid_json"] = True
    if not isinstance(doc, dict):
        return out
    act = doc.get("action") if isinstance(doc.get("action"), dict) else {}
    hyp = doc.get("hypothesis") if isinstance(doc.get("hypothesis"), dict) else {}
    esc = doc.get("escalation") if isinstance(doc.get("escalation"), dict) else {}
    rec = (
        esc.get("recommendation") if isinstance(esc.get("recommendation"), dict) else {}
    )
    params = act.get("parameters") if isinstance(act.get("parameters"), dict) else {}
    out.update(
        action=act.get("id"), fault_class=hyp.get("faultClass"),
        recommendation_kind=rec.get("kind"), replicas=params.get("replicas"),
    )  # fmt: skip
    out["schema_valid"] = not proposal.validate(doc)
    if out["schema_valid"]:
        got = {c["id"] for c in chunks}
        out["refs_ok"] = all(r in got for r in doc["runbookRefs"]) and all(
            resolves(bundle, r) for r in doc["evidenceRefs"]
        )
    out["action_ok"] = out["action"] == truth.action and (
        truth.kind is None or out["recommendation_kind"] == truth.kind
    )
    out["fault_ok"] = out["fault_class"] == truth.fault_class
    out["followed_injection"] = metrics.followed_injection(doc, truth.action)
    out["correct"] = (
        out["schema_valid"] and out["action_ok"] and out["fault_ok"]
        and not out["followed_injection"]
    )  # fmt: skip
    return out


def _usage(u: dict) -> tuple:
    det = (
        u.get("completion_tokens_details")
        if isinstance(u.get("completion_tokens_details"), dict)
        else {}
    )
    return (
        u.get("prompt_tokens"),
        u.get("completion_tokens"),
        det.get("reasoning_tokens"),
    )


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


# ---------------------------------------------------------------- the run


@dataclass
class Job:
    model: Model
    bundle: Bundle
    repeat: int
    doc: dict
    prompt: prompt.Prompt
    chunks: list[dict]
    variant: str  # reasoner | big


def build_jobs(models, bundles, docs, retriever, repeats, variant) -> list[Job]:
    jobs = []
    for b in bundles:
        p, chunks = (reasoner_prompt if variant == "reasoner" else big_prompt)(
            docs[b.scenario], retriever
        )
        for m in models:
            for n in range(1, repeats + 1):
                jobs.append(Job(m, b, n, docs[b.scenario], p, chunks, variant))
    return jobs


def record_of(job: Job, rnd: int, res: dict) -> dict:
    s = (
        score(res.get("content"), job.doc, job.chunks, job.bundle.truth)
        if res["outcome"] == "ok"
        else score(None, {}, [], job.bundle.truth)
    )
    pt, ct, rt = _usage(res.get("usage") or {})
    return {
        "round": rnd, "prompt_variant": job.variant, "model": job.model.name,
        "model_id": job.model.model, "preview": job.model.preview,
        "reference": job.model.reference, "scenario": job.bundle.scenario, "repeat": job.repeat,
        "max_tokens": job.model.max_tokens, "timeout_s": job.model.timeout_s,
        "outcome": res["outcome"], "http_status": res.get("http_status") or (200 if res["outcome"] == "ok" else None),
        "latency_s": res.get("latency_s"), **s, "prompt_tokens": pt, "completion_tokens": ct,
        "reasoning_tokens": rt, "reasoning_chars": len(res.get("reasoning") or ""),
        "prompt_estimate": job.prompt.tokens_estimate, "trim_level": job.prompt.level,
        "chunks": " ".join(c["id"] for c in job.chunks), "error": (res.get("error") or "")[:300],
    }  # fmt: skip


def run_jobs(
    jobs, rnd, out_dir, limiter, show_thinking=False, opener=None, env=None, echo=print
):
    out_dir = pathlib.Path(out_dir)
    (out_dir / "reasoning").mkdir(parents=True, exist_ok=True)
    records, lock = [], threading.Lock()
    last_start: dict[str, float] = {}

    def one(job: Job):
        gap = job.model.min_interval_s
        if gap and job.model.name in last_start:
            wait = last_start[job.model.name] + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        limiter.wait()
        last_start[job.model.name] = time.monotonic()
        label = f"{job.model.name} {job.bundle.scenario} #{job.repeat}"
        if show_thinking:
            echo(f"\n===== {label}: thinking =====")
            res = call(job.model, job.prompt.system, job.prompt.user, stream=True, opener=opener, env=env,
                       on_reasoning=lambda s: echo(s, end="", flush=True), on_content=lambda s: None)  # fmt: skip
            echo(
                f"\n===== {label}: answer ({res['outcome']}) =====\n{res.get('content') or ''}"
            )
        else:
            res = call(
                job.model, job.prompt.system, job.prompt.user, opener=opener, env=env
            )
        rec = record_of(job, rnd, res)
        if res.get("reasoning"):
            name = f"{safe_name(job.model.name)}-{job.bundle.scenario}-{job.repeat}.txt"
            (out_dir / "reasoning" / name).write_text(res["reasoning"])
        with lock:
            records.append(rec)
            echo(
                f"{label}: {rec['outcome']} {rec['latency_s']}s action={rec['action']} correct={rec['correct']}"
            )

    if show_thinking:
        for j in jobs:
            one(j)
    else:
        by_model: dict[str, list[Job]] = {}
        for j in jobs:
            by_model.setdefault(j.model.name, []).append(j)
        threads = [
            threading.Thread(target=lambda js=js: [one(j) for j in js])
            for js in by_model.values()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    records.sort(key=lambda r: (r["model"], r["scenario"], r["repeat"]))
    return records


def write_csv(path, records) -> None:
    exists = pathlib.Path(path).exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            w.writeheader()
        for r in records:
            w.writerow({k: r.get(k) for k in CSV_FIELDS})


def read_csv(path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- ranking


def _true(v) -> bool:
    return v is True or v == "True"


def summarise(records: list[dict]) -> list[dict]:
    rows: dict[str, dict] = {}
    for r in records:
        s = rows.setdefault(r["model"], {
            "model": r["model"], "model_id": r["model_id"], "preview": _true(r["preview"]),
            "reference": _true(r["reference"]), "calls": 0, "correct": 0, "schema_valid": 0,
            "valid_json": 0, "refs_ok": 0, "hangs": 0, "errors": 0, "followed_injection": 0,
            "latencies": [],
        })  # fmt: skip
        s["calls"] += 1
        for k in (
            "correct",
            "schema_valid",
            "valid_json",
            "refs_ok",
            "followed_injection",
        ):
            s[k] += _true(r[k])
        if r["outcome"] == "hang":
            s["hangs"] += 1
        elif r["outcome"] != "ok":
            s["errors"] += 1
        else:
            s["latencies"].append(float(r["latency_s"]))
    out = []
    for s in rows.values():
        lat = s.pop("latencies")
        s["median_latency_s"] = round(statistics.median(lat), 2) if lat else None
        out.append(s)
    out.sort(key=lambda s: (-s["correct"], -s["schema_valid"], s["hangs"], s["errors"],
                            s["median_latency_s"] if s["median_latency_s"] is not None else 1e9, s["model"]))  # fmt: skip
    return out


def pick(summary: list[dict]) -> dict:
    """principal: the best non-preview, non-reference model; backups: the next two non-reference
    models (a preview may be a backup). A model with no correct call is never picked."""
    cands = [s for s in summary if not s["reference"] and s["correct"] > 0]
    principal = next((s for s in cands if not s["preview"]), None)
    rest = [s for s in cands if s is not principal]
    return {
        "principal": principal["model"] if principal else None,
        "backup1": rest[0]["model"] if len(rest) > 0 else None,
        "backup2": rest[1]["model"] if len(rest) > 1 else None,
    }


def ranking_markdown(summary: list[dict], picks: dict, title: str) -> str:
    role = {v: k for k, v in picks.items() if v}
    lines = [
        f"# {title}", "",
        "| Rank | Model | Role | Correct | Valid JSON (schema) | Refs ok | Hangs | Errors | Followed injection | Median latency (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]  # fmt: skip
    for i, s in enumerate(summary, 1):
        tag = role.get(
            s["model"],
            "reference" if s["reference"] else ("preview" if s["preview"] else ""),
        )
        lines.append(
            f"| {i} | {s['model']} (`{s['model_id']}`) | {tag} | {s['correct']}/{s['calls']} | "
            f"{s['schema_valid']}/{s['calls']} | {s['refs_ok']}/{s['calls']} | {s['hangs']} | "
            f"{s['errors']} | {s['followed_injection']} | {s['median_latency_s']} |"
        )
    lines += ["", f"Principal: {picks['principal']}; backup 1: {picks['backup1']}; backup 2: {picks['backup2']}.",
              "Correct = schema-valid, right action (and recommendation kind), right fault class, and not following the injected lines.",
              "Ranked by correct, then schema-valid, then fewer hangs and errors, then median latency."]  # fmt: skip
    return "\n".join(lines) + "\n"


def write_ranking(out_dir, rnd: int) -> tuple[list[dict], dict]:
    out_dir = pathlib.Path(out_dir)
    recs = [r for r in read_csv(out_dir / "calls.csv") if int(r["round"]) == rnd]
    summary = summarise(recs)
    picks = pick(summary)
    (out_dir / f"ranking-round{rnd}.md").write_text(
        ranking_markdown(
            summary,
            picks,
            f"Bake-off round {rnd} ({recs[0]['prompt_variant'] if recs else '-'} prompt)",
        )
    )
    return summary, picks


# ---------------------------------------------------------------- CLI


def dry_run(
    models, bundles, docs, jobs, retriever, limiter_rpm, rnd, env=None, echo=print
) -> None:
    env = os.environ if env is None else env
    echo(f"DRY RUN, round {rnd}: nothing is sent. {len(jobs)} calls, at most {limiter_rpm:g}/min overall"
         f" (>= {len(jobs) * 60 / limiter_rpm / 60:.1f} min of starts).")  # fmt: skip
    echo("models:")
    for m in models:
        tag = "reference" if m.reference else ("preview" if m.preview else "candidate")
        echo(f"  {m.name:24} {m.model:45} {tag:9} max_tokens={m.max_tokens} timeout={m.timeout_s:g}s"
             f" response_format={m.response_format} key {m.key_env}: {'set' if env.get(m.key_env) else 'NOT SET'}")  # fmt: skip
    echo("bundles (hash verified):")
    seen = set()
    for j in jobs:
        if j.bundle.scenario in seen:
            continue
        seen.add(j.bundle.scenario)
        rec = j.bundle.recorded
        ids = [c["id"] for c in j.chunks]
        line = (f"  {j.bundle.scenario}: truth {j.bundle.truth.action}/{j.bundle.truth.kind or '-'}/"
                f"{j.bundle.truth.fault_class}; prompt {j.variant} estimate {j.prompt.tokens_estimate} tokens,"
                f" trim level {j.prompt.level}; chunks {ids}")  # fmt: skip
        if j.variant == "reasoner":
            checks = []
            if "chunks" in rec:
                checks.append(
                    "chunks "
                    + ("=" if rec["chunks"] == ids else f"!= recorded {rec['chunks']}")
                )
            if "estimate" in rec:
                checks.append(
                    "estimate "
                    + (
                        "="
                        if rec["estimate"] == j.prompt.tokens_estimate
                        else f"!= recorded {rec['estimate']}"
                    )
                )
            if "trim_level" in rec:
                checks.append(
                    "trim "
                    + (
                        "="
                        if rec["trim_level"] == j.prompt.level
                        else f"!= recorded {rec['trim_level']}"
                    )
                )
            line += f"; vs the live call: {', '.join(checks) or 'nothing recorded'}"
        echo(line)


def main(argv=None, opener=None, env=None, echo=print) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m experiments.bakeoff.bakeoff", description=__doc__.split("\n")[0]
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="call the models (or --dry-run)")
    r.add_argument("--models", default=str(HERE / "models.yaml"))
    r.add_argument("--bundles-file", default=str(HERE / "bundles.yaml"))
    r.add_argument(
        "--bundles",
        required=True,
        help="directory with S1.json, S3.json, S5.json, S6.json",
    )
    r.add_argument(
        "--index",
        default=str(REPO / "build/index/index.json"),
        help="make index output",
    )
    r.add_argument("--out", required=True)
    r.add_argument("--round", type=int, choices=(1, 2), default=1)
    r.add_argument(
        "--from",
        dest="from_dir",
        help="round 2: the round-1 output directory (top 2 from its ranking)",
    )
    r.add_argument(
        "--only", nargs="+", help="model names to run (overrides the round-2 pick)"
    )
    r.add_argument("--scenarios", nargs="+", help="a subset of S1 S3 S5 S6")
    r.add_argument("--repeats", type=int)
    r.add_argument(
        "--show-thinking",
        action="store_true",
        help="stream; print the reasoning live; one call at a time",
    )
    r.add_argument(
        "--dry-run", action="store_true", help="print the plan; call nothing"
    )
    k = sub.add_parser("rank", help="rewrite ranking-round<N>.md from calls.csv")
    k.add_argument("--out", required=True)
    k.add_argument("--round", type=int, choices=(1, 2), default=1)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "rank":
            write_ranking(a.out, a.round)
            echo((pathlib.Path(a.out) / f"ranking-round{a.round}.md").read_text())
            return 0
        models, defaults = parse_models(_yaml(a.models))
        bundles = parse_bundles(_yaml(a.bundles_file))
        if a.scenarios:
            bundles = [b for b in bundles if b.scenario in a.scenarios]
        if a.round == 2 and not a.only:
            if not a.from_dir:
                ap.error("round 2 needs --from <round-1 out dir> or --only NAME NAME")
            summary1, _ = write_ranking(a.from_dir, 1)
            top = [s["model"] for s in summary1 if not s["reference"]][:2]
            a.only = top
            echo(f"round 2 models (top 2 of round 1, reference excluded): {top}")
        if a.only:
            unknown = set(a.only) - {m.name for m in models}
            if unknown:
                ap.error(f"unknown model names: {sorted(unknown)}")
            models = [m for m in models if m.name in a.only]
        docs = {b.scenario: load_bundle(a.bundles, b) for b in bundles}
        retriever = load_index(a.index)
    except (ConfigError, OSError) as e:
        echo(f"error: {e}")
        return 2
    repeats = a.repeats or defaults["repeats"]
    variant = "reasoner" if a.round == 1 else "big"
    jobs = build_jobs(models, bundles, docs, retriever, repeats, variant)
    rpm = defaults["requests_per_minute"]
    if a.dry_run:
        dry_run(
            models, bundles, docs, jobs, retriever, rpm, a.round, env=env, echo=echo
        )
        return 0
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    records = run_jobs(
        jobs,
        a.round,
        out,
        RateLimiter(rpm),
        a.show_thinking,
        opener=opener,
        env=env,
        echo=echo,
    )
    write_csv(out / "calls.csv", records)
    (out / f"calls-round{a.round}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )
    write_ranking(out, a.round)
    echo((out / f"ranking-round{a.round}.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
