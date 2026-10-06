#!/usr/bin/env python3
"""R1 capacity ramp and knee detection (M1b-9; ADR-026). Standard library only.

  ramp.py run  --locust URL --out DIR [--prom URL] [--classes DevUser] [step options]
  ramp.py knee STEPS_CSV [threshold options]

run: steps the Locust master's user count (one user = one request/s, see the
locustfile) through --start, --start + --step, ... up to --max. Each step is
--settle seconds of settling (default 1 min), then Locust's statistics are
reset, then --measure seconds of measurement (default 2 min). The achieved
rate is the requests counted in that window divided by its measured seconds.
Locust's own total_rps is a short-window snapshot: it is recorded for display
only and never used. One row per step goes to DIR/steps.csv as soon as the step ends,
with the raw Locust and Prometheus answers in DIR/step-<target>.json. The ramp
stops at the first knee step (or --max), then posts /stop: Locust returns to
idle. The step after the knee is never run.

With --prom (a Prometheus base URL), each step also records, over its measure
window: sample-api CFS throttling and CPU in the target namespace,
dependency-db throttling, CPU and peak working set (recorded at every step;
the DB decision is the owner's, at the R1 gate), the Locust worker's CPU and
the node's CPU. An empty answer is recorded as empty, never as 0. It also
records namespace:nexus_sample_api_requests:rate2m at the window's end, a
server-side cross-check of the achieved rate (its 2 min window is the measure
window): a difference above --rate-diff-max (default 5 %), or no answer, is
flagged in the row and the verdict. A flag is not a knee; the owner reads it.

The master's reset does not reach the workers, which report every 3 s, and the
master's API caches answers for 2 s. So the window count can read a few
seconds' worth of requests low: about 10 % over a 20 s smoke window, a few
percent over 120 s. The cross-check is what bounds it.

knee: the first step with any of
  - failures above --fail-max of all requests (default 1 %);
  - any /items failure (a db_slots_exhausted or db_unavailable 503: the logs
    say which);
  - achieved rate below --achieved-min of the target (default 95 %);
  - p95 above --p95-factor times the first step's p95 (default 2);
  - sample-api throttled periods above --throttle-max (default 10 %);
  - with --prom data: an empty sample-api throttling answer.
Capacity C is the target of the last step before the knee. If the ramp
reached --max without a knee, C is the last target and the result says so.
Baseline B = floor(0.4 × C) per namespace (spec §25 "about 40 %").
"""

import argparse
import csv
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASELINE_SHARE = 0.4

FIELDS = [
    "target",
    "t_start",
    "t_end",
    "requests",
    "failures",
    "items_failures",
    "achieved_rps",
    "locust_total_rps",
    "prom_rps",
    "rate_diff",
    "rate_flag",
    "fail_ratio",
    "p95_ms",
    "app_throttle",
    "app_cpu",
    "db_throttle",
    "db_cpu",
    "db_ws_max_bytes",
    "worker_cpu",
    "node_cpu",
]

# PromQL per step; {ns} is the target namespace, {w} the measure window in seconds.
QUERIES = {
    "app_throttle": 'sum(increase(container_cpu_cfs_throttled_periods_total{{namespace="{ns}",container="sample-api"}}[{w}s])) / sum(increase(container_cpu_cfs_periods_total{{namespace="{ns}",container="sample-api"}}[{w}s]))',
    "app_cpu": 'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",container="sample-api"}}[{w}s]))',
    "db_throttle": 'sum(increase(container_cpu_cfs_throttled_periods_total{{namespace="nexus-data",container="dependency-db"}}[{w}s])) / sum(increase(container_cpu_cfs_periods_total{{namespace="nexus-data",container="dependency-db"}}[{w}s]))',
    "db_cpu": 'sum(rate(container_cpu_usage_seconds_total{{namespace="nexus-data",container="dependency-db"}}[{w}s]))',
    "db_ws_max_bytes": 'max(max_over_time(container_memory_working_set_bytes{{namespace="nexus-data",container="dependency-db"}}[{w}s]))',
    "worker_cpu": 'sum(rate(container_cpu_usage_seconds_total{{namespace="nexus-load",container="locust",pod=~"locust-worker-.*"}}[{w}s]))',
    "node_cpu": '1 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[{w}s]))',
}

# Server-side cross-check of the achieved rate: the recording rule's 2 min window ends at the
# step's end, like the measure window.
RATE_QUERY = 'namespace:nexus_sample_api_requests:rate2m{{namespace="{ns}"}}'


# ---------------------------------------------------------------- knee ----


def _num(value):
    """A CSV cell as a float; '' (no data) stays None."""
    if value is None or value == "":
        return None
    return float(value)


def knee(
    rows, p95_factor=2.0, fail_max=0.01, achieved_min=0.95, throttle_max=0.10, prom=None
):
    """Return {"capacity", "baseline", "knee_target", "reasons", "reached_max", "flags"}.

    rows: dicts with FIELDS (strings or numbers), in step order. prom: whether
    Prometheus columns are expected; None infers it from the first row.
    """
    if not rows:
        raise ValueError("no steps")
    if prom is None:
        prom = any(_num(rows[0].get(f)) is not None for f in QUERIES)
    ref_p95 = _num(rows[0]["p95_ms"])
    flags = [
        f"step {row['target']}: {row['rate_flag']}"
        for row in rows
        if row.get("rate_flag") not in (None, "", "ok")
    ]
    last_ok = None
    for i, row in enumerate(rows):
        target = int(_num(row["target"]))
        reasons = []
        requests = _num(row["requests"]) or 0
        failures = _num(row["failures"]) or 0
        if requests == 0:
            reasons.append("no requests")
        elif failures / requests > fail_max:
            reasons.append(f"failures {failures / requests:.2%} > {fail_max:.0%}")
        if (_num(row["items_failures"]) or 0) > 0:
            reasons.append(f"/items failures {int(_num(row['items_failures']))}")
        achieved = _num(row["achieved_rps"]) or 0.0
        if achieved < achieved_min * target:
            reasons.append(
                f"achieved {achieved:.1f} < {achieved_min:.0%} of {target:g}"
            )
        p95 = _num(row["p95_ms"])
        if (
            i > 0
            and p95 is not None
            and ref_p95 is not None
            and p95 > p95_factor * ref_p95
        ):
            reasons.append(f"p95 {p95:g} ms > {p95_factor:g} x {ref_p95:g} ms")
        if prom:
            throttle = _num(row.get("app_throttle"))
            if throttle is None:
                reasons.append("sample-api throttling: no data")
            elif throttle > throttle_max:
                reasons.append(
                    f"sample-api throttled {throttle:.2%} > {throttle_max:.0%}"
                )
        if reasons:
            capacity = last_ok
            return {
                "capacity": capacity,
                "baseline": baseline(capacity) if capacity is not None else None,
                "knee_target": target,
                "reasons": reasons,
                "reached_max": False,
                "flags": flags,
            }
        last_ok = target
    return {
        "capacity": last_ok,
        "baseline": baseline(last_ok),
        "knee_target": None,
        "reasons": [],
        "reached_max": True,
        "flags": flags,
    }


def baseline(capacity):
    """B = floor(0.4 x C) requests/s per namespace."""
    return math.floor(BASELINE_SHARE * capacity + 1e-9)


def read_steps(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------- run ----


class Http:
    """The HTTP calls run needs; tests replace it."""

    def get(self, url, timeout=30):
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()

    def get_json(self, url, timeout=30):
        return json.loads(self.get(url, timeout))

    def post_form(self, url, fields, timeout=30):
        data = urllib.parse.urlencode(fields, doseq=True).encode()
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data), timeout=timeout
        ) as resp:
            return json.loads(resp.read() or b"{}")


def locust_row(stats, target, t_start, t_end, items_name):
    """One step's Locust numbers from /stats/requests, reset at t_start: the achieved rate is
    the window's requests over its measured seconds (total_rps is kept for display only)."""
    entries = {e["name"]: e for e in stats["stats"]}
    total = entries.get("Aggregated", {})
    requests = total.get("num_requests", 0)
    failures = total.get("num_failures", 0)
    p95 = total.get("response_time_percentile_0.95")
    total_rps = total.get("total_rps")
    return {
        "target": target,
        "t_start": f"{t_start:.3f}",
        "t_end": f"{t_end:.3f}",
        "requests": requests,
        "failures": failures,
        "items_failures": entries.get(items_name, {}).get("num_failures", 0),
        "achieved_rps": f"{requests / (t_end - t_start):.3f}",
        "locust_total_rps": "" if total_rps is None else f"{total_rps:.3f}",
        "fail_ratio": f"{(failures / requests) if requests else 0:.6f}",
        "p95_ms": "" if p95 is None else p95,
    }


def prom_value(http, base, query, at):
    """A scalar from an instant query, or '' when the answer is empty or NaN."""
    url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode(
        {"query": query, "time": f"{at:.3f}"}
    )
    answer = http.get_json(url)
    result = answer.get("data", {}).get("result", [])
    if not result:
        return "", answer
    value = float(result[0]["value"][1])
    return ("" if math.isnan(value) else value), answer


def rate_check(achieved, prom_rps, max_diff):
    """(rate_diff, rate_flag) for the server-side cross-check."""
    if prom_rps == "":
        return "", "prometheus rate: no data"
    if achieved == 0:
        return "", "achieved 0"
    diff = abs(prom_rps - achieved) / achieved
    flag = "ok" if diff <= max_diff else f"rate diff {diff:.1%} > {max_diff:.0%}"
    return f"{diff:.4f}", flag


def run(args, http=None, sleep=time.sleep, now=time.time, log=print):
    http = http or Http()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    locust = args.locust.rstrip("/")
    env = "dev" if args.namespace == "nexus-dev" else "prod"
    items_name = f"{env}:/items"
    rows = []
    steps_csv = out / "steps.csv"
    with open(steps_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        target = args.start
        try:
            while target <= args.max:
                fields = {"user_count": target, "spawn_rate": args.spawn_rate}
                if args.classes:
                    fields["user_classes"] = args.classes
                http.post_form(f"{locust}/swarm", fields)
                log(f"step {target}: settling {args.settle} s")
                sleep(args.settle)
                http.get(f"{locust}/stats/reset")  # answers "ok", not JSON
                t_start = now()
                sleep(args.measure)
                stats = http.get_json(f"{locust}/stats/requests")
                t_end = now()
                row = {f: "" for f in FIELDS}
                row.update(locust_row(stats, target, t_start, t_end, items_name))
                raw = {"locust": stats, "prometheus": {}}
                if args.prom:
                    window = max(1, round(t_end - t_start))
                    for name, template in QUERIES.items():
                        query = template.format(ns=args.namespace, w=window)
                        row[name], raw["prometheus"][name] = prom_value(
                            http, args.prom, query, t_end
                        )
                    query = RATE_QUERY.format(ns=args.namespace)
                    row["prom_rps"], raw["prometheus"]["prom_rps"] = prom_value(
                        http, args.prom, query, t_end
                    )
                    row["rate_diff"], row["rate_flag"] = rate_check(
                        float(row["achieved_rps"]), row["prom_rps"], args.rate_diff_max
                    )
                writer.writerow(row)
                f.flush()
                (out / f"step-{target}.json").write_text(json.dumps(raw, indent=1))
                rows.append(row)
                verdict = knee(
                    rows,
                    args.p95_factor,
                    args.fail_max,
                    args.achieved_min,
                    args.throttle_max,
                    prom=bool(args.prom),
                )
                log(
                    f"step {target}: {row['achieved_rps']} req/s, failures {row['failures']}, "
                    f"p95 {row['p95_ms']} ms, app throttle {row['app_throttle']}, "
                    f"db throttle {row['db_throttle']}, rate check {row['rate_flag'] or '-'}"
                )
                if verdict["knee_target"] is not None:
                    break
                target += args.step
        finally:
            http.get_json(f"{locust}/stop")
    verdict = knee(
        rows,
        args.p95_factor,
        args.fail_max,
        args.achieved_min,
        args.throttle_max,
        prom=bool(args.prom),
    )
    (out / "verdict.json").write_text(json.dumps(verdict, indent=1))
    return verdict


# ----------------------------------------------------------------- cli ----


def parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    thresholds = argparse.ArgumentParser(add_help=False)
    thresholds.add_argument("--p95-factor", type=float, default=2.0)
    thresholds.add_argument("--fail-max", type=float, default=0.01)
    thresholds.add_argument("--achieved-min", type=float, default=0.95)
    thresholds.add_argument("--throttle-max", type=float, default=0.10)

    r = sub.add_parser("run", parents=[thresholds])
    r.add_argument(
        "--locust",
        required=True,
        help="Locust master base URL, e.g. http://127.0.0.1:8089",
    )
    r.add_argument("--out", required=True)
    r.add_argument(
        "--prom", help="Prometheus base URL; omit to skip the resource columns"
    )
    r.add_argument(
        "--namespace", default="nexus-dev", choices=["nexus-dev", "nexus-prod"]
    )
    r.add_argument("--classes", nargs="*", default=["DevUser"])
    r.add_argument("--start", type=int, default=10)
    r.add_argument("--step", type=int, default=10)
    r.add_argument("--max", type=int, default=200)
    r.add_argument("--settle", type=float, default=60)
    r.add_argument("--measure", type=float, default=120)
    r.add_argument("--spawn-rate", type=float, default=5)
    r.add_argument("--rate-diff-max", type=float, default=0.05)

    k = sub.add_parser("knee", parents=[thresholds])
    k.add_argument("steps_csv")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.cmd == "run":
        verdict = run(args)
    else:
        verdict = knee(
            read_steps(args.steps_csv),
            args.p95_factor,
            args.fail_max,
            args.achieved_min,
            args.throttle_max,
        )
    print(json.dumps(verdict, indent=1))
    return 0 if verdict["capacity"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
