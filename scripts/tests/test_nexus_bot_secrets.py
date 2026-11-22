"""scripts/nexus-bot-secrets.sh against a stub kubectl (offline, no cluster, a throwaway HOME): the
calls it makes, that the token never reaches argv or the output, and its refusals."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "nexus-bot-secrets.sh"
TOKEN = "offline-test-value-not-a-bot-token-0123456789"  # shaped like no real credential
GH_TOKEN = "offline-test-value-not-a-github-token-abcdefghijklmnop"  # neither is this

# The stub logs its argv; STUB_NS says whether the namespace exists and STUB_SECRETS lists the
# Secrets that exist (comma-separated); STUB_FAIL makes the call whose argv contains that word fail
# as an API error would.
STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
fail = os.environ.get("STUB_FAIL")
if fail and fail in args:
    print("Error from server: stub failure", file=sys.stderr)
    sys.exit(1)
if "get" in args and "namespace" in args:
    print("namespace/nexus-bot" if os.environ.get("STUB_NS") == "1" else "")
elif "get" in args and "secret" in args:
    name = args[args.index("secret") + 1]
    print(f"secret/{name}" if name in os.environ.get("STUB_SECRETS", "").split(",") else "")
elif "create" in args:
    print(f"secret/{args[args.index('generic') + 1]} created")
else:
    print("stub kubectl: unexpected call", file=sys.stderr)
    sys.exit(3)
'''


class NexusBotSecretsStubTest(unittest.TestCase):
    def run_script(self, *args, ns="1", secrets="", fail="", token=TOKEN + "\n", mode=0o600,
                   github=None, github_mode=0o600):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "bin").mkdir()
            stub = d / "bin" / "kubectl"
            stub.write_text(STUB.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
            stub.chmod(0o755)
            if token is not None:
                (d / ".nexus").mkdir(mode=0o700)
                f = d / ".nexus" / "telegram-bot.token"
                f.write_text(token)
                f.chmod(mode)
            if github is not None:
                (d / ".nexus").mkdir(mode=0o700, exist_ok=True)
                g = d / ".nexus" / "github-machine.token"
                g.write_text(github)
                g.chmod(github_mode)
            log = d / "calls.jsonl"
            env = {"PATH": f"{d / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(log), "STUB_NS": ns,
                   "STUB_SECRETS": secrets, "STUB_FAIL": fail, "KUBECONFIG": "/nonexistent", "HOME": str(d)}  # fmt: skip
            out = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True,
                                 text=True, timeout=30, check=False)  # fmt: skip
            calls = [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []
            self.home = d
        return out, calls

    def assert_token_never_leaks(self, out, calls):
        for part in (TOKEN, TOKEN[:12], TOKEN[-12:], GH_TOKEN, GH_TOKEN[:12], GH_TOKEN[-12:]):
            self.assertNotIn(part, out.stdout + out.stderr)
            self.assertFalse(any(part in a for c in calls for a in c), calls)

    def test_creates_the_secret_from_the_file_when_absent(self):
        out, calls = self.run_script()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(calls), 3, calls)
        for c in calls:
            self.assertEqual(c[:2], ["--context", "default"], c)
        self.assertEqual(calls[0][2:], ["get", "namespace", "nexus-bot", "--ignore-not-found", "-o", "name"])
        self.assertEqual(calls[1][2:], ["-n", "nexus-bot", "get", "secret", "nexus-bot-telegram",
                                        "--ignore-not-found", "-o", "name"])  # fmt: skip
        self.assertEqual(calls[2][2:], ["-n", "nexus-bot", "create", "secret", "generic", "nexus-bot-telegram",
                                        f"--from-file=token={self.home}/.nexus/telegram-bot.token"])  # fmt: skip
        self.assertEqual(out.stdout, f"created: nexus-bot/nexus-bot-telegram (key token, {len(TOKEN)} characters)\n"
                                     "skipped: nexus-bot/nexus-bot-github (no ~/.nexus/github-machine.token;"
                                     " the bot runs with C8 off)\n")  # fmt: skip
        self.assert_token_never_leaks(out, calls)

    def test_an_existing_secret_is_left_alone(self):
        out, calls = self.run_script(secrets="nexus-bot-telegram")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(any("create" in c for c in calls), calls)
        self.assertTrue(out.stdout.startswith("exists: nexus-bot/nexus-bot-telegram (left as it is)\n"))
        self.assert_token_never_leaks(out, calls)

    def test_refuses_without_the_namespace_and_never_creates_it(self):
        out, calls = self.run_script(ns="0")
        self.assertEqual(out.returncode, 1)
        self.assertIn("namespace nexus-bot does not exist yet", out.stderr)
        self.assertEqual(len(calls), 1, calls)
        self.assert_token_never_leaks(out, calls)

    def test_an_api_error_is_not_taken_for_absence(self):
        for word in ("namespace", "secret"):
            out, calls = self.run_script(fail=word if word == "namespace" else "nexus-bot-telegram")
            self.assertEqual(out.returncode, 1, word)
            self.assertIn("failed", out.stderr)
            self.assertFalse(any("create" in c for c in calls), calls)

    def test_a_failed_create_is_fatal(self):
        out, calls = self.run_script(fail="create")
        self.assertEqual(out.returncode, 1)
        self.assertIn("creating Secret nexus-bot/nexus-bot-telegram failed", out.stderr)
        self.assert_token_never_leaks(out, calls)

    def test_file_refusals_come_before_any_kubectl_call(self):
        for kwargs, msg in (({"token": None}, "is missing"), ({"token": ""}, "is empty"),
                            ({"token": "\n"}, "is empty"), ({"mode": 0o644}, "has mode 644"),
                            ({"mode": 0o640}, "has mode 640")):  # fmt: skip
            out, calls = self.run_script(**kwargs)
            self.assertEqual(out.returncode, 1, kwargs)
            self.assertIn(msg, out.stderr, kwargs)
            self.assertEqual(calls, [], kwargs)

    def test_length_ignores_the_line_ending(self):
        out, _ = self.run_script(token=TOKEN)
        self.assertIn(f"{len(TOKEN)} characters", out.stdout)

    def test_takes_no_arguments(self):
        out, calls = self.run_script("--force")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(calls, [])

    # C8 (ADR-041): ~/.nexus/github-machine.token -> nexus-bot/nexus-bot-github, only when the file exists

    def test_creates_the_github_secret_from_its_file_when_present(self):
        out, calls = self.run_script(github=GH_TOKEN + "\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(calls), 5, calls)
        for c in calls:
            self.assertEqual(c[:2], ["--context", "default"], c)
        self.assertEqual(calls[3][2:], ["-n", "nexus-bot", "get", "secret", "nexus-bot-github",
                                        "--ignore-not-found", "-o", "name"])  # fmt: skip
        self.assertEqual(calls[4][2:], ["-n", "nexus-bot", "create", "secret", "generic", "nexus-bot-github",
                                        f"--from-file=token={self.home}/.nexus/github-machine.token"])  # fmt: skip
        self.assertEqual(out.stdout, f"created: nexus-bot/nexus-bot-telegram (key token, {len(TOKEN)} characters)\n"
                                     f"created: nexus-bot/nexus-bot-github (key token, {len(GH_TOKEN)} characters)\n")  # fmt: skip
        self.assert_token_never_leaks(out, calls)

    def test_an_existing_github_secret_is_left_alone(self):
        out, calls = self.run_script(github=GH_TOKEN, secrets="nexus-bot-telegram,nexus-bot-github")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(any("create" in c for c in calls), calls)
        self.assertEqual(out.stdout, "exists: nexus-bot/nexus-bot-telegram (left as it is)\n"
                                     "exists: nexus-bot/nexus-bot-github (left as it is)\n")  # fmt: skip
        self.assert_token_never_leaks(out, calls)

    def test_github_file_refusals_come_before_any_kubectl_call(self):
        for kwargs, msg in (({"github": ""}, "github-machine.token is empty"),
                            ({"github": "\n"}, "github-machine.token is empty"),
                            ({"github": GH_TOKEN, "github_mode": 0o644}, "github-machine.token has mode 644"),
                            ({"github": GH_TOKEN, "github_mode": 0o604}, "github-machine.token has mode 604")):  # fmt: skip
            out, calls = self.run_script(**kwargs)
            self.assertEqual(out.returncode, 1, kwargs)
            self.assertIn(msg, out.stderr, kwargs)
            self.assertEqual(out.stderr.count("FATAL"), 1, out.stderr)
            self.assertEqual(calls, [], kwargs)
            self.assert_token_never_leaks(out, calls)

    def test_the_telegram_token_stays_required_with_a_github_file(self):
        out, calls = self.run_script(token=None, github=GH_TOKEN)
        self.assertEqual(out.returncode, 1)
        self.assertIn("telegram-bot.token is missing", out.stderr)
        self.assertEqual(calls, [])

    def test_refuses_without_the_namespace_with_both_files(self):
        out, calls = self.run_script(ns="0", github=GH_TOKEN)
        self.assertEqual(out.returncode, 1)
        self.assertIn("namespace nexus-bot does not exist yet", out.stderr)
        self.assertEqual(len(calls), 1, calls)
        self.assert_token_never_leaks(out, calls)

    def test_an_api_error_on_the_github_secret_is_fatal_and_creates_nothing(self):
        out, calls = self.run_script(github=GH_TOKEN, fail="nexus-bot-github")
        self.assertEqual(out.returncode, 1)
        self.assertIn("checking whether Secret nexus-bot/nexus-bot-github exists failed", out.stderr)
        self.assertFalse(any("nexus-bot-github" in c and "create" in c for c in calls), calls)
        self.assert_token_never_leaks(out, calls)


if __name__ == "__main__":
    unittest.main()
