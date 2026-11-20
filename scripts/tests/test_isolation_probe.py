"""scripts/isolation-probe.sh against a stub kubectl (ADR-047): offline, no cluster.

The probe used to pass the `* *` question unquoted, so the shell globbed it into file names and the
line read FAIL. Run from a directory full of files, the stub must still receive the literal `*`."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROBE = Path(__file__).resolve().parents[1] / "isolation-probe.sh"

STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
if "auth" in args and "can-i" in args:
    print("selfsubjectreviews.authorization.k8s.io  [create]" if "--list" in args else "no")
elif "exec" in args:
    specs = args[args.index("-c", args.index("--")) + 2:]
    print("T1 absent")
    print("T2 none")
    for spec in specs:
        name, host, port = spec.split()
        r = "connected" if name.startswith("CONTROL:") else "refused/unreachable (ConnectionRefusedError)"
        print("N", name, host + ":" + port, r)
elif "node" in args:
    print("10.0.0.5", end="")
elif "svc" in args:
    print("10.43.0.1", end="")
elif "jsonpath={.spec.automountServiceAccountToken}|{.spec.volumes[*].name}" in args:
    print("false|config", end="")
elif "jsonpath={.items[0].metadata.name}" in args:
    print("nexus-reasoner-abc", end="")
else:
    print("10.42.0.9", end="")  # ip_of: a pod IP
'''


def verb_of(args):
    """kubectl's verb: the first argument that is not a flag or a flag's value."""
    it = iter(args)
    for a in it:
        if a in ("--context", "-n", "--namespace"):
            next(it, None)
        elif not a.startswith("-"):
            return a
    return None


class IsolationProbeTest(unittest.TestCase):
    def run_probe(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "bin").mkdir()
            stub = d / "bin" / "kubectl"
            stub.write_text(STUB.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
            stub.chmod(0o755)
            work = d / "work"  # files a glob of `*` would expand to
            work.mkdir()
            for name in ("alpha.txt", "beta.txt"):
                (work / name).write_text("x")
            log = d / "calls.jsonl"
            env = {"PATH": f"{d / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(log),
                   "KUBECONFIG": "/nonexistent", "HOME": str(d)}  # fmt: skip
            out = subprocess.run(["bash", str(PROBE)], cwd=work, env=env, capture_output=True,
                                 text=True, timeout=60, check=False)  # fmt: skip
            calls = [json.loads(ln) for ln in log.read_text().splitlines()]
        return out, calls

    def test_every_check_holds_and_the_wildcard_reaches_kubectl_literally(self):
        out, calls = self.run_probe()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotIn("FAIL", out.stdout)
        self.assertIn("ok   R1 can-i * * -A: no", out.stdout)
        can_i = [c for c in calls if "can-i" in c and "--list" not in c]
        self.assertEqual(len(can_i), 10)
        star = [c for c in can_i if c[c.index("can-i") + 1] == "*"]
        self.assertEqual(len(star), 1)
        self.assertEqual(star[0][star[0].index("can-i") + 1 : star[0].index("can-i") + 3], ["*", "*"])
        flat = [a for c in calls for a in c]
        self.assertFalse({"alpha.txt", "beta.txt"} & set(flat))  # nothing was globbed

    def test_the_control_is_the_nim_endpoint_and_only_one_exec_is_made(self):
        out, calls = self.run_probe()
        self.assertIn("ok   N  CONTROL:nim integrate.api.nvidia.com:443 reachable (control)", out.stdout)
        execs = [c for c in calls if "exec" in c]
        self.assertEqual(len(execs), 1)
        writes = {"apply", "patch", "delete", "create", "scale", "rollout", "annotate", "label", "edit"}
        for c in calls:  # every other call is a read: get, auth can-i, or the one exec
            self.assertIn(verb_of(c), {"get", "auth", "exec"}, c)
            self.assertNotIn(verb_of(c), writes, c)


if __name__ == "__main__":
    unittest.main()
