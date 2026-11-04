#!/usr/bin/env python3
"""G5: the minimal audit_id join for the M2 exit (spec §14 "Reconciliation", NF-05, I10).

Joins the Flight Recorder `actions` rows to the Kubernetes API Audit Log events by `audit_id` for
one run window and prints a verdict per row and event. Offline: two files in, a report out. The full
reconciliation (`reconcile-audit`, the `audit_links` table, the envelope re-check) stays in M4; this
script has the same verdict names so its output can seed that work.

Inputs
  --actions FILE   one JSON object per line, the `actions` columns. Export, on the host (the
                   password comes from the environment, never argv):
                     psql "$FR_URL" -At -c "SELECT row_to_json(a) FROM actions a
                       WHERE intent_at BETWEEN '<start>' AND '<end>'" > actions.jsonl
  --audit FILE     the API audit log, JSON lines (/var/log/nexus-audit/audit.log, ADR-019)
  --since/--until  the run window, RFC 3339; an audit event outside it is ignored
  --user           the operator identity (default system:serviceaccount:nexus-system:nexus-operator)
  --links FILE     optional: write the audit_links rows (audit_id, matched_action, verdict) as JSONL

Operator mutation events = ResponseComplete events by --user, verb patch/update/create, on
deployments/scale or pods/eviction, without dryRun in the request URI. Dry-run requests (Safety Gate
stage 7) carry an Audit-Id too but are not actions: they are counted and reported, not joined.

Verdicts
  matched          row and event share audit_id and the row's http_status equals the response code
  status_mismatch  same audit_id, different code: the recorded result is wrong
  unexplained      an operator mutation event with no row: the run is invalid (§14 step 3)
  missing_event    a row with an audit_id and no event in the window
  pending          a row without audit_id (intent only, or completion lost): flagged, not unexplained

Completeness = matched ÷ operator mutation events; it must be 1.0 for a run to count. Exit 0 only
when completeness is 1.0 and there is no status_mismatch, missing_event or pending row; 1 otherwise;
2 on unreadable input.
"""
import argparse
import datetime
import json
import sys

OPERATOR = "system:serviceaccount:nexus-system:nexus-operator"
MUTATING_VERBS = {"patch", "update", "create"}
SUBRESOURCES = {("deployments", "scale"), ("pods", "eviction")}


def ts(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"{path}:{n}: not JSON ({e})") from None
    return rows


def classify_events(events, user, since, until):
    """Return (operator mutation events by audit id, dry-run events)."""
    mutations: dict[str, dict] = {}
    dry_runs: list[dict] = []
    for e in events:
        if e.get("stage") != "ResponseComplete" or (e.get("user") or {}).get("username") != user:
            continue
        ref = e.get("objectRef") or {}
        if e.get("verb") not in MUTATING_VERBS or (ref.get("resource"), ref.get("subresource")) not in SUBRESOURCES:
            continue
        when = ts(e["stageTimestamp"])
        if (since and when < since) or (until and when > until):
            continue
        if "dryRun=" in (e.get("requestURI") or ""):
            dry_runs.append(e)
        else:
            mutations[e["auditID"]] = e
    return mutations, dry_runs


def join(actions: list[dict], events: list[dict], user=OPERATOR, since=None, until=None) -> dict:
    mutations, dry_runs = classify_events(events, user, since, until)
    verdicts: list[dict] = []
    seen: set[str] = set()
    for a in actions:
        aid = a.get("audit_id")
        key = a["idempotency_key"]
        if not aid:
            verdicts.append({"audit_id": None, "action": key, "verdict": "pending"})
            continue
        seen.add(aid)
        e = mutations.get(aid)
        if e is None:
            verdicts.append({"audit_id": aid, "action": key, "verdict": "missing_event"})
        elif a.get("http_status") != (e.get("responseStatus") or {}).get("code"):
            verdicts.append({"audit_id": aid, "action": key, "verdict": "status_mismatch",
                             "recorded": a.get("http_status"), "audit": (e.get("responseStatus") or {}).get("code")})
        else:
            verdicts.append({"audit_id": aid, "action": key, "verdict": "matched"})
    for aid, e in sorted(mutations.items()):
        if aid not in seen:
            ref = e["objectRef"]
            verdicts.append({"audit_id": aid, "action": None, "verdict": "unexplained",
                             "event": f'{e["verb"]} {ref.get("namespace")}/{ref.get("name")}/{ref.get("subresource")} '
                                      f'-> {(e.get("responseStatus") or {}).get("code")} at {e["stageTimestamp"]}'})
    matched = sum(v["verdict"] == "matched" for v in verdicts)
    completeness = matched / len(mutations) if mutations else None
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    ok = (completeness == 1.0 and set(counts) == {"matched"}) if mutations else (not verdicts)
    return {"operator_mutation_events": len(mutations), "dry_run_events_ignored": len(dry_runs),
            "actions": len(actions), "counts": counts, "completeness": completeness,
            "ok": ok, "verdicts": verdicts}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--actions", required=True)
    p.add_argument("--audit", required=True)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--user", default=OPERATOR)
    p.add_argument("--links")
    args = p.parse_args(argv)
    try:
        actions = read_jsonl(args.actions)
        events = read_jsonl(args.audit)
        report = join(actions, events, args.user,
                      ts(args.since) if args.since else None, ts(args.until) if args.until else None)
        if args.links:
            at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with open(args.links, "w", encoding="utf-8") as f:
                for v in report["verdicts"]:
                    if v["audit_id"]:
                        f.write(json.dumps({"audit_id": v["audit_id"], "matched_action": v["action"],
                                            "verdict": v["verdict"], "at": at}) + "\n")
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in report.items() if k != "verdicts"}))
    for v in report["verdicts"]:
        if v["verdict"] != "matched":
            print(json.dumps(v))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
