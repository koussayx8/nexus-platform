"""scripts/fr-export.sh against a stub kubectl (offline, no cluster): the one exec it makes, its
queries, and its refusals. The queries also run against a real Flight Recorder schema in CI
(operator/tests/unit/test_fr_export_pg.py)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "fr-export.sh"

STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
print("col\nrow")
'''

SINCE, UNTIL = "2026-11-20T06:25:39Z", "2026-11-20T06:31:32Z"


class FrExportStubTest(unittest.TestCase):
    def run_script(self, *args):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "bin").mkdir()
            stub = d / "bin" / "kubectl"
            stub.write_text(STUB.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
            stub.chmod(0o755)
            log = d / "calls.jsonl"
            env = {"PATH": f"{d / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(log),
                   "KUBECONFIG": "/nonexistent", "HOME": str(d)}  # fmt: skip
            out = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True,
                                 text=True, timeout=30, check=False)  # fmt: skip
            calls = [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []
        return out, calls

    def sql_of(self, call):
        return call[call.index("-c", call.index("psql")) + 1]

    def test_one_read_only_exec_as_the_reader(self):
        out, [call] = self.run_script("actions", SINCE, UNTIL)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "col\nrow\n")
        head = call[: call.index("--")]
        self.assertEqual(head, ["--context", "default", "-n", "nexus-system", "exec", "flight-recorder-db-0",
                                "-c", "flight-recorder-db"])  # fmt: skip
        psql = call[call.index("--") + 1 :]
        self.assertEqual(psql[0], "psql")
        self.assertIn("nexus_reader", psql)
        self.assertEqual(psql[psql.index("-d") + 1], "nexus_recorder")
        self.assertIn("ON_ERROR_STOP=1", psql)
        self.assertNotIn("PASSWORD", " ".join(call).upper())

    def test_each_table_selects_by_its_own_time_column_as_csv(self):
        for table, column in (("actions", "a.intent_at"), ("gate_decisions", "g.at"), ("proposals", "i.detected_at")):
            _, [call] = self.run_script(table, SINCE, UNTIL)
            sql = self.sql_of(call)
            self.assertTrue(sql.startswith("COPY (SELECT "), sql)
            self.assertIn(f"{column} >= '{SINCE}'::timestamptz AND {column} < '{UNTIL}'::timestamptz", sql)
            self.assertTrue(sql.endswith("TO STDOUT WITH (FORMAT csv, HEADER)"), sql)
            self.assertNotRegex(sql, r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|GRANT|TRUNCATE)\b")
        _, [call] = self.run_script("proposals", SINCE, UNTIL)
        self.assertIn("JOIN incidents i USING (incident_uid)", self.sql_of(call))

    def test_jsonl_is_row_to_json_unaligned_for_audit_join(self):
        _, [call] = self.run_script("actions", SINCE, UNTIL, "--jsonl")
        sql = self.sql_of(call)
        self.assertTrue(sql.startswith("SELECT row_to_json(r) FROM (SELECT a.* FROM actions a"), sql)
        self.assertIn("-A", call)
        self.assertIn("-t", call)
        self.assertNotIn("COPY", sql)

    def test_bad_input_never_reaches_kubectl(self):
        for args in (
            ("incidents", SINCE, UNTIL),
            ("actions; DROP TABLE actions", SINCE, UNTIL),
            ("actions", "yesterday", UNTIL),
            ("actions", "2026-11-20T06:25:39Z' OR '1'='1", UNTIL),
            ("actions", SINCE, "2026-11-20 06:31:32"),
            ("actions", SINCE),
            ("actions", SINCE, UNTIL, "--json"),
        ):
            out, calls = self.run_script(*args)
            self.assertEqual(out.returncode, 2, args)
            self.assertEqual(calls, [], args)

    def test_offsets_and_fractions_are_accepted(self):
        out, [call] = self.run_script("gate_decisions", "2026-11-20T07:25:39.123+01:00", "2026-11-20T08:00:00+01:00")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("'2026-11-20T07:25:39.123+01:00'::timestamptz", self.sql_of(call))


if __name__ == "__main__":
    unittest.main()
