"""SYNTHETIC "novel cases": five bundles derived from the real trial bundles, for faults NEXUS has
no scenario for. They are not observations; every record they produce says set=novel.

  python -m experiments.bakeoff.novel --bundles DIR [--bundles-file FILE]

Reads the real bases from DIR (S1, S3, S5, S6, each checked against its recorded hash), writes
N1.json ... N5.json next to them, and checks each result against the hash sealed in bundles.yaml.
The derivation is deterministic: the same bases give the same bytes and the same hashes, so a
sealed truth can never be fitted to a bundle after the fact.

Each derivation keeps the base's structure (evidence/v1.1 as operator/nexus_operator/evidence.py
builds it), its metrics shape and its log format, and changes only what the fault needs:

  N1 image pull (bad tag)       from S3: the new revision's pod waits on ErrImagePull, no digest
  N2 OOMKilled crash loop       from S1: containers killed for memory, one in CrashLoopBackOff
  N3 DNS failure                from S5: the database host name no longer resolves
  N4 certificate verify failure from S5: TLS to the database fails, certificate expired
  N5 misleading log line        from S6: database errors in the log, CPU and latency anomalous

The evidence collector keeps event reasons only, never messages (evidence.py, _events), so
"ErrImagePull" appears as the container's waiting reason, and the events are Pulling, Failed and
BackOff, as the kubelet emits them.
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys
import uuid

from experiments.bakeoff import (
    bakeoff,
)  # first: it puts operator/ and reasoner/ on sys.path

proposal = bakeoff.proposal

IP = "[REDACTED:ip]"
STARTUP = [
    "INFO:     Started server process [1]",
    "INFO:     Waiting for application startup.",
    "INFO:     Application startup complete.",
    f"INFO:     Uvicorn running on http://{IP}:8000 (Press CTRL+C to quit)",
]
DB_HOST = "dependency-db.nexus-data"  # apps/sample-api/k8s/base/deployment.yaml DB_HOST
S5_MESSAGE = (
    f'connection failed: connection to server at "{IP}", port 5432 failed: '
    'FATAL:  role "app_dev" is not permitted to log in'
)


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nexus-bakeoff/novel/{name}"))


def _alert(doc: dict, name: str) -> None:
    doc["incident"]["alerts"] = [{**doc["incident"]["alerts"][0], "name": name}]


def _per_pod(doc: dict, key: str) -> dict[str, list[str]]:
    """Split logs[key] back into each pod's lines, by logs.sources (evidence.py, _logs)."""
    out, i = {}, 0
    for s in doc["logs"]["sources"]:
        out[s["pod"]] = doc["logs"][key][i : i + s[key]]
        i += s[key]
    return out


def _set_logs(doc: dict, current: dict, previous: dict) -> None:
    pods = [s["pod"] for s in doc["logs"]["sources"]]
    doc["logs"]["current"] = [ln for p in pods for ln in current.get(p, [])]
    doc["logs"]["previous"] = [ln for p in pods for ln in previous.get(p, [])]
    doc["logs"]["sources"] = [
        {
            "pod": p,
            "current": len(current.get(p, [])),
            "previous": len(previous.get(p, [])),
        }
        for p in pods
    ]


def _finish(doc: dict, name: str) -> dict:
    doc["incident"]["uid"] = _uid(name)
    doc["redaction"]["counts"]["ip"] = json.dumps(doc).count(IP)
    doc.pop("hash", None)
    doc["hash"] = proposal.hash_of(doc)
    return doc


def n1_image_pull(s3: dict) -> dict:
    """S3's rollout, but the new image tag does not exist: the new revision's pod never starts."""
    d = copy.deepcopy(s3)
    new_rs = d["workload"]["currentRevision"]["replicaSet"]
    for p in d["pods"]:
        if p["name"].startswith(new_rs + "-"):
            p.update(ready=False, restarts=0)
            p["containers"] = [
                {"state": "waiting", "reason": "ErrImagePull", "lastTermination": None}
            ]
    # a tag with no digest: evidence._digest gives None
    d["workload"]["currentRevision"]["imageDigest"] = None
    cur = _per_pod(d, "current")
    prev = _per_pod(d, "previous")
    for p in cur:
        if p.startswith(new_rs + "-"):
            cur[p] = []  # the container never ran: no log
    _set_logs(d, cur, prev)
    # the readiness probe never ran; the image was never pulled, created or started
    kept = [
        e
        for e in d["events"]
        if e["reason"] not in ("Unhealthy", "Pulled", "Created", "Started")
    ]
    d["events"] = sorted(
        [
            {
                "type": "Warning",
                "count": 9,
                "reason": "Failed",
                "lastSeen": "2026-11-19T20:28:25Z",
            },
            {
                "type": "Normal",
                "count": 9,
                "reason": "BackOff",
                "lastSeen": "2026-11-19T20:28:25Z",
            },
            {
                "type": "Warning",
                "count": 4,
                "reason": "Failed",
                "lastSeen": "2026-11-19T20:27:47Z",
            },
            {
                "type": "Normal",
                "count": 4,
                "reason": "Pulling",
                "lastSeen": "2026-11-19T20:27:46Z",
            },
        ]
        + kept,
        key=lambda e: e["lastSeen"],
        reverse=True,
    )
    return _finish(d, "N1")


def n2_oom_crashloop(s1: dict) -> dict:
    """S1's two pods, but the memory limit is too low: each container is OOMKilled under load."""
    d = copy.deepcopy(s1)
    a, b = (p["name"] for p in d["pods"])
    for p in d["pods"]:
        if p["name"] == a:
            p.update(ready=False, restarts=6)
            p["containers"] = [
                {
                    "state": "waiting",
                    "reason": "CrashLoopBackOff",
                    "lastTermination": "OOMKilled",
                }
            ]
        else:
            p.update(ready=True, restarts=5)
            p["containers"] = [
                {"state": "running", "reason": None, "lastTermination": "OOMKilled"}
            ]
    d["workload"]["ready"] = 1
    cur, prev = _per_pod(d, "current"), _per_pod(d, "previous")
    # a: waiting, so no current log; its previous run is kept. b: its last 200 lines split into
    # the killed run (previous) and the new one (start-up, then traffic). The kernel kill writes
    # nothing to the log.
    lines_b = cur[b]
    cur = {a: [], b: STARTUP + lines_b[150:]}
    prev = {a: prev[a], b: lines_b[:150]}
    _set_logs(d, cur, prev)
    d["events"] = [
        {
            "type": "Warning",
            "count": 14,
            "reason": "BackOff",
            "lastSeen": "2026-11-19T09:11:52Z",
        },
        {
            "type": "Normal",
            "count": 6,
            "reason": "Started",
            "lastSeen": "2026-11-19T09:11:31Z",
        },
        {
            "type": "Normal",
            "count": 6,
            "reason": "Created",
            "lastSeen": "2026-11-19T09:11:31Z",
        },
        {
            "type": "Normal",
            "count": 6,
            "reason": "Pulled",
            "lastSeen": "2026-11-19T09:11:31Z",
        },
    ]
    d["metrics"]["zscores"] = {
        "cpu": 0.38, "inflight": 0.71, "errorRatio": 0.0, "latencyP95": 1.06, "requestRate": -0.42,
    }  # fmt: skip
    d["metrics"]["errorRatio"] = 0.0
    d["gitops"]["health"] = "Degraded"
    _alert(d, "NexusCrashLooping")
    return _finish(d, "N2")


def _s5_message(s5: dict, message: str) -> dict:
    d = copy.deepcopy(s5)
    for key in ("current", "previous"):
        d["logs"][key] = [ln.replace(S5_MESSAGE, message) for ln in d["logs"][key]]
    return d


def n3_dns(s5: dict) -> dict:
    """S5's failing database calls, but the database host name no longer resolves."""
    d = _s5_message(
        s5,
        f'connection failed: could not translate host name "{DB_HOST}" to address: '
        "Temporary failure in name resolution",
    )
    return _finish(d, "N3")


def n4_cert(s5: dict) -> dict:
    """S5's failing database calls, but TLS verification of the database's certificate fails."""
    d = _s5_message(
        s5,
        f'connection failed: connection to server at "{IP}", port 5432 failed: '
        "SSL error: certificate verify failed: certificate has expired",
    )
    return _finish(d, "N4")


def n5_misleading(s6: dict) -> dict:
    """S6's over-capacity load with CPU and latency both anomalous; the log blames the database."""
    d = copy.deepcopy(s6)
    cur, prev = _per_pod(d, "current"), _per_pod(d, "previous")
    ports = iter(("41226", "41230", "52318", "52322"))
    for pod, lines in cur.items():
        out = []
        for ln in lines:
            if " ERROR sample_api " not in ln:
                out.append(ln)
            elif "Autoscaler override" in ln:  # the first injected line of each pod
                stamp = ln.split(" ERROR ", 1)[0]
                for _ in range(2):
                    out += [
                        (
                            f"{stamp} ERROR sample_api db_error type=QueryCanceled sqlstate=57014"
                            " message=canceling statement due to statement timeout"
                        ),
                        f'INFO:     {IP}:{next(ports)} - "GET /items HTTP/1.1" 503 Service Unavailable',
                    ]
        cur[pod] = out
    _set_logs(d, cur, prev)
    d["metrics"]["zscores"] = {
        **d["metrics"]["zscores"],
        "latencyP95": 4.62,
        "errorRatio": 0.6,
    }
    d["metrics"]["errorRatio"] = 0.0003
    _alert(d, "NexusCpuAnomaly")
    return _finish(d, "N5")


DERIVATIONS = {
    "N1": ("S3", n1_image_pull),
    "N2": ("S1", n2_oom_crashloop),
    "N3": ("S5", n3_dns),
    "N4": ("S5", n4_cert),
    "N5": ("S6", n5_misleading),
}


def main(argv=None, echo=print) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m experiments.bakeoff.novel", description=__doc__.split("\n")[0]
    )
    ap.add_argument(
        "--bundles",
        required=True,
        help="directory with the real S1, S3, S5, S6 bundles",
    )
    ap.add_argument("--bundles-file", default=str(bakeoff.HERE / "bundles.yaml"))
    a = ap.parse_args(argv)
    try:
        bundles = {
            b.scenario: b for b in bakeoff.parse_bundles(bakeoff._yaml(a.bundles_file))
        }
        bases = {
            s: bakeoff.load_bundle(a.bundles, bundles[s])
            for s in sorted({s for s, _ in DERIVATIONS.values()})
        }
    except (bakeoff.ConfigError, KeyError) as e:
        echo(f"error: {e}")
        return 2
    rc = 0
    for name, (base, fn) in DERIVATIONS.items():
        doc = fn(bases[base])
        sealed = bundles.get(name)
        path = pathlib.Path(a.bundles) / f"{name}.json"
        path.write_text(json.dumps(doc, indent=2) + "\n")
        ok = sealed is not None and sealed.hash == doc["hash"]
        rc |= 0 if ok else 1
        echo(
            f"{name} (from {base}): {doc['hash']} {'= sealed' if ok else '!= sealed ' + (sealed.hash if sealed else '(none)')} -> {path}"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
