"""scripts/fr-export.sh's queries against a real Flight Recorder schema (05-schema.sql and
10-roles.sql, as the pod's init applies them), as nexus_reader. The stub kubectl hands the psql part
of the exec to the throwaway server. Skips without server binaries, except in CI (conftest.pg)."""

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pgfixture

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fr-export.sh"

STUB = r"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
psql = args[args.index("--") + 1:]
assert psql[0] == "psql"
os.execv(os.path.join(os.environ["PG_BINDIR"], "psql"), psql)
"""

SEED = """
INSERT INTO incidents (incident_uid, target, source, detected_at) VALUES
  ('u-in', 'nexus-dev/sample-api', 'detector', '2026-11-20T06:28:32Z'),
  ('u-out', 'nexus-dev/sample-api', 'detector', '2026-11-20T05:00:00Z');
INSERT INTO proposals (proposal_hash, incident_uid, reasoner_mode, schema_valid, model_id, reasoning) VALUES
  ('sha256:in', 'u-in', 'llm', true, 'nvidia/nemotron-3-ultra-550b-a55b', 'a "quoted" trace, with a comma'),
  ('sha256:out', 'u-out', 'rules', true, NULL, NULL);
INSERT INTO gate_decisions (incident_uid, stage, verdict, detail, at) VALUES
  ('u-in', 6, 'fail', '{"detail": "a rollout is in progress"}', '2026-11-20T06:28:50Z'),
  ('u-out', 1, 'pass', NULL, '2026-11-20T05:00:10Z');
INSERT INTO actions (idempotency_key, incident_uid, action_id, parameters, intent_at, action_class) VALUES
  ('u-in-1', 'u-in', 'scale_deployment', '{"replicas": 4}', '2026-11-20T06:28:52Z', 'scale'),
  ('u-out-1', 'u-out', 'restart_workload', '{}', '2026-11-20T05:01:00Z', 'restart');
"""

SINCE, UNTIL = "2026-11-20T06:25:39Z", "2026-11-20T06:31:32Z"


def export(pg, tmp_path, *args):
    stub = tmp_path / "kubectl"
    stub.write_text(STUB.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
    stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "PG_BINDIR": pg.bindir,
        "PGHOST": "127.0.0.1",
        "PGPORT": str(pg.port),
        "PGPASSWORD": pgfixture.PASSWORDS["READER"],
        "KUBECONFIG": "/nonexistent",
    }
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True,
                          timeout=60, check=False)  # fmt: skip


def seeded(pg):
    r = pg.psql("postgres", SEED)
    assert r.returncode == 0, r.stderr
    return pg


def test_each_table_exports_only_its_window_as_csv(pg, tmp_path):
    seeded(pg)
    for table, key, want in (
        ("actions", "idempotency_key", ["u-in-1"]),
        ("gate_decisions", "incident_uid", ["u-in"]),
        ("proposals", "proposal_hash", ["sha256:in"]),
    ):
        out = export(pg, tmp_path, table, SINCE, UNTIL)
        assert out.returncode == 0, (table, out.stderr)
        rows = list(csv.DictReader(io.StringIO(out.stdout)))
        assert [r[key] for r in rows] == want, (table, out.stdout)
    rows = list(
        csv.DictReader(
            io.StringIO(export(pg, tmp_path, "proposals", SINCE, UNTIL).stdout)
        )
    )
    assert rows[0]["reasoning"] == 'a "quoted" trace, with a comma'  # CSV quoting holds
    assert rows[0]["target"] == "nexus-dev/sample-api" and rows[0]["detected_at"]


def test_jsonl_feeds_audit_join(pg, tmp_path):
    seeded(pg)
    out = export(pg, tmp_path, "actions", SINCE, UNTIL, "--jsonl")
    assert out.returncode == 0, out.stderr
    [row] = [json.loads(ln) for ln in out.stdout.splitlines() if ln.strip()]
    assert (row["idempotency_key"], row["parameters"]) == ("u-in-1", {"replicas": 4})


def test_the_reader_cannot_write(pg):
    r = pg.psql(
        "nexus_reader", "DELETE FROM actions", password=pgfixture.PASSWORDS["READER"]
    )
    assert r.returncode != 0 and "permission denied" in r.stderr
