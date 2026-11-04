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


class ValidatorTest(unittest.TestCase):
    """(b) proposal/v1 validation is the operator's proposal.py, not a second implementation."""

    def test_the_service_uses_the_operators_module(self):
        from nexus_operator import proposal
        from nexus_reasoner import service

        self.assertIs(service.proposal, proposal)
        self.assertTrue(
            proposal.__file__.endswith("operator/nexus_operator/proposal.py")
        )

    def test_no_second_validator_in_the_reasoner_package(self):
        for p in (REPO / "reasoner/nexus_reasoner").glob("*.py"):
            self.assertNotRegex(p.read_text(), r"def validate\w*\(", p.name)

    def test_the_image_copies_proposal_py_and_its_imports_at_build_time(self):
        final = (REPO / "reasoner/Dockerfile").read_text().split("\nFROM ")[-1]
        for f in ("catalogue.py", "proposal.py", "rules.py"):
            self.assertIn(f, final.split("COPY reasoner/nexus_reasoner")[0], f)
        self.assertFalse(list((REPO / "reasoner").rglob("proposal.py")))

    def test_provider_schema_cannot_drift_from_the_validator(self):
        """The JSON Schema sent to providers is a request, not a validator; it must still say what
        proposal.py accepts: same keys, same enums."""
        from nexus_operator import catalogue, proposal
        from nexus_reasoner import prompt

        sch = prompt.PROPOSAL_SCHEMA
        self.assertEqual(set(sch["properties"]), proposal._KEYS)
        self.assertEqual(set(sch["required"]), proposal._KEYS)
        self.assertEqual(
            set(sch["properties"]["hypothesis"]["properties"]["faultClass"]["enum"]),
            set(catalogue.FAULT_CLASSES),
        )
        self.assertEqual(
            set(sch["properties"]["action"]["properties"]["id"]["enum"]),
            set(catalogue.CATALOGUE),
        )
        esc = sch["properties"]["escalation"]["anyOf"][1]["properties"][
            "recommendation"
        ]["properties"]["kind"]["enum"]
        self.assertEqual(set(esc), set(catalogue.RECOMMENDATION_KINDS))
        self.assertEqual(
            set(sch["properties"]["action"]["required"]), {"id", "target", "parameters"}
        )
        self.assertEqual(
            set(sch["properties"]["action"]["properties"]["target"]["required"]),
            proposal._TARGET_KEYS,
        )


class DigestBumpTest(unittest.TestCase):
    """(c) the digest-bump job can rewrite the Deployment, and cannot loop."""

    def test_bump_rewrites_exactly_the_reasoner_image(self):
        import sys

        sys.path.insert(0, str(REPO / ".github/scripts"))
        import bump_digest

        text = (K8S / "deployment.yaml").read_text()
        new = "sha256:" + "b" * 64
        out = bump_digest.bump(
            text, "ghcr.io/koussayx8/nexus-platform/nexus-reasoner", new
        )
        self.assertEqual(out.count(new), 1)
        self.assertNotEqual(out, text)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))

    def test_workflow_bumps_the_manifest_and_does_not_retrigger_itself(self):
        wf = (REPO / ".github/workflows/reasoner.yml").read_text()
        self.assertIn("bump_digest.py reasoner/k8s/deployment.yaml", wf)
        self.assertIn("needs: [build, sign]", wf)
        self.assertEqual(wf.count("- '!reasoner/k8s/**'"), 2)  # push and pull_request


class ImageInputsTest(unittest.TestCase):
    """The first CI build broke when dev's operator_lib began importing nexus_operator.model: the
    index stage listed files by hand. Keep the image inputs and the workflow triggers in step."""

    def test_index_stage_takes_every_module_operator_lib_imports(self):
        stage = (REPO / "reasoner/Dockerfile").read_text().split("\nFROM ")[1]
        self.assertIn("COPY operator/nexus_operator operator/nexus_operator\n", stage)

    def test_workflow_triggers_on_each_module_operator_lib_imports(self):
        lib = (REPO / "experiments/harness/operator_lib.py").read_text()
        mods = (
            re.search(r"(?m)^from nexus_operator import (.+)$", lib)
            .group(1)
            .split(", ")
        )
        wf = (REPO / ".github/workflows/reasoner.yml").read_text()
        for m in mods:
            self.assertIn(f"'operator/nexus_operator/{m}.py'", wf, m)


if __name__ == "__main__":
    unittest.main()
