"""I1/N6 and 'merged is not deployed': manifest tests (re-plan O7). Text-level, standard library only."""

import json
import re
import textwrap
import unittest

from nexus_reasoner import config

from .helpers import REPO

K8S = REPO / "reasoner/k8s"
NETPOL = (REPO / "platform/network/reasoner.yaml").read_text()


class ManifestTest(unittest.TestCase):
    def test_no_token_anywhere(self):
        dep = (K8S / "deployment.yaml").read_text()
        sa = (K8S / "serviceaccount.yaml").read_text()
        self.assertRegex(dep, r"(?m)^      automountServiceAccountToken: false$")
        self.assertRegex(sa, r"(?m)^automountServiceAccountToken: false$")
        self.assertNotIn("automountServiceAccountToken: true", dep + sa)
        self.assertNotRegex(dep, r"serviceAccountToken|projected")

    def test_no_binding_for_its_service_account(self):
        for p in (
            list((REPO / "platform").rglob("*.yaml"))
            + list((REPO / "reasoner").rglob("*.yaml"))
            + list((REPO / "operator/k8s").rglob("*.yaml"))
        ):
            text = p.read_text()
            if re.search(r"kind: (Cluster)?RoleBinding", text):
                self.assertNotRegex(
                    text,
                    r"kind: ServiceAccount[^\n]*\n?[^\n]*name: nexus-reasoner",
                    str(p),
                )

    def test_labels_match_the_network_policies(self):
        sel = "app.kubernetes.io/name: nexus-reasoner"
        self.assertIn(sel, NETPOL)
        dep = (K8S / "deployment.yaml").read_text()
        self.assertEqual(
            len(re.findall(r"(?m)^\s+" + re.escape(sel) + "$", dep)), 3
        )  # metadata, selector, pod template
        self.assertIn("namespace: nexus-reasoner", dep)

    def test_restricted_pod_security(self):
        dep = (K8S / "deployment.yaml").read_text()
        for needle in (
            "runAsNonRoot: true",
            "allowPrivilegeEscalation: false",
            "readOnlyRootFilesystem: true",
            "drop: [ALL]",
            "type: RuntimeDefault",
        ):
            self.assertIn(needle, dep)

    def test_keys_come_from_an_optional_secret_and_not_from_the_repo(self):
        dep = (K8S / "deployment.yaml").read_text()
        self.assertEqual(
            len(
                re.findall(
                    r"secretKeyRef: \{name: nexus-reasoner-keys, key: [a-z-]+, optional: true\}",
                    dep,
                )
            ),
            2,
        )
        self.assertNotRegex(
            dep + (K8S / "configmap.yaml").read_text(), r"(gsk_|sk-ant|Bearer )"
        )
        self.assertFalse(list((REPO / "reasoner").rglob("*secret*.yaml")))

    def test_configmap_parses_and_matches_the_spec_arms(self):
        text = (K8S / "configmap.yaml").read_text()
        block = text.split("reasoner.json: |\n", 1)[1]
        cfg = config.parse(json.loads(textwrap.dedent(block)))
        self.assertEqual(cfg.models["R2"].model, "openai/gpt-oss-20b")
        self.assertEqual(cfg.models["R3"].model, "openai/gpt-oss-120b")
        self.assertEqual(cfg.models["H1"].provider, "anthropic")
        self.assertEqual(cfg.models["demo"].provider, "ollama")
        self.assertEqual((cfg.timeout_s, cfg.max_output_tokens), (30.0, 1024))
        results = json.loads((REPO / "experiments/retrieval/results.json").read_text())
        self.assertEqual(cfg.score_floor, results["metrics"]["proposed_floor"])

    def test_merged_is_not_deployed(self):
        for p in (REPO / "platform" / "argocd").rglob("*.yaml"):
            self.assertNotIn("reasoner/k8s", p.read_text(), p)
        for p in (REPO / "operator/k8s").rglob("kustomization.yaml"):
            self.assertNotRegex(p.read_text(), r"reasoner")


if __name__ == "__main__":
    unittest.main()
