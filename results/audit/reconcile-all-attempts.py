#!/usr/bin/env python3
"""reconcile-audit's reconcile() over EVERY attempt of the pilot (valid, void, discarded; the two earlier
discarded-only C7 queues too), with the Flight Recorder rows and the audit log of its window.
Usage: reconcile-all-attempts.py REPO TABLES AUDIT OUT.json   (REPO: a checkout with scripts/ and experiments/;
TABLES: one or more files joined by commas, merged table by table)"""
import importlib.util, json, pathlib, sys
repo, tables_f, audit_f, out_f = map(pathlib.Path, sys.argv[1:5])
sys.path.insert(0, str(repo))
def load(n):
    s = importlib.util.spec_from_file_location(n.replace("-", "_"), repo / "scripts" / f"{n}.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
aj, recon, ex = load("audit-join"), load("reconcile-audit"), load("export-results")
from experiments.harness import batch
tables = {}
for f in str(tables_f).split(","):
    for t, rows in json.loads(pathlib.Path(f).read_text()).items():
        tables.setdefault(t, []).extend(rows)
events_all = [e for e in aj.read_jsonl(str(audit_f)) if (e.get("user") or {}).get("username") == aj.OPERATOR]
res = []
home = pathlib.Path.home()
for q in ("nexus-batch-c7", "nexus-batch-c7b", "nexus-batch-c7c", "nexus-batch"):
    queue = batch.Queue.load(home / q)
    for rec in queue.runs:
        cell = queue.cell(rec["cell"]); run = ex.run_row(rec, cell, "-", "-")
        since, until = aj.ts(rec["started_at"]), aj.ts(rec["ended_at"])
        events = [e for e in events_all if since <= aj.ts(e["stageTimestamp"]) <= until]
        uids = {i["incident_uid"] for i in tables["incidents"] if since <= aj.ts(i["detected_at"]) <= until}
        actions = [a for a in tables["actions"] if a["incident_uid"] in uids]
        r = recon.reconcile(run, {"actions": actions}, events)
        res.append({"queue": q, "attempt": rec["run_id"], "status": rec["status"], "reason": rec.get("void_reason") or rec.get("discard_reason"),
                    "window": [rec["started_at"], rec["ended_at"]], "fr_rows_in_export": bool(uids) or q in ("nexus-batch-c7c", "nexus-batch"),
                    "operator_mutation_events": r["join"]["operator_mutation_events"], "actions": len(actions),
                    "join_counts": r["join"]["counts"], "completeness": r["join"]["completeness"], "unexplained": len(r["unexplained"]),
                    "envelope_checked": r["envelope"]["checked"], "violations": len(r["envelope"]["violations"]), "unverified": len(r["envelope"]["unverified"]), "counts": r["counts"]})
out_f.write_text(json.dumps(res, indent=1))
for x in res: print(x["queue"], x["attempt"], x["status"], "events", x["operator_mutation_events"], "actions", x["actions"], x["join_counts"], "unexplained", x["unexplained"], "checked", x["envelope_checked"], "viol", x["violations"], "unverif", x["unverified"])
