"""Tests for scripts/check-ignore-differences.py (G4, I12): the real tree passes, and each rule
fails when its own condition is broken in a copy of the tree. No cluster, no network."""
import importlib.util
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cid", ROOT / "scripts/check-ignore-differences.py")
cid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cid)

COPIED = ["platform/argocd/applications", "platform/rbac", "apps/sample-api/k8s", "overlays", "operator/k8s"]


class Boundary(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for p in COPIED:
            shutil.copytree(ROOT / p, self.tmp / p)

    def edit(self, rel, old, new):
        f = self.tmp / rel
        text = f.read_text()
        self.assertIn(old, text)
        f.write_text(text.replace(old, new, 1))

    def rules(self):
        return sorted({e.split()[0] for e in cid.check(self.tmp)})

    def test_real_tree_is_clean(self):
        self.assertEqual(cid.check(ROOT), [])

    def test_b1_extra_pointer(self):
        self.edit("platform/argocd/applications/sample-api-dev.yaml",
                  "- /spec/replicas", "- /spec/replicas\n        - /spec/template")
        # An over-broad entry is not a delegation either, so B4 fires with it.
        self.assertEqual(self.rules(), ["B1", "B4"])

    def test_b1_jq_expression(self):
        self.edit("platform/argocd/applications/sample-api-prod.yaml",
                  "      jsonPointers:\n        - /spec/replicas",
                  "      jqPathExpressions: ['.spec']\n      jsonPointers:\n        - /spec/replicas")
        self.assertEqual(self.rules(), ["B1"])

    def test_b2_respect_missing(self):
        self.edit("platform/argocd/applications/sample-api-dev.yaml",
                  "      - RespectIgnoreDifferences=true", "      - CreateNamespace=false")
        self.assertEqual(self.rules(), ["B2"])

    def test_b3_extra_write_verb(self):
        # The read-only evidence ClusterRole (#144) gains a write on Deployments themselves.
        self.edit("platform/rbac/nexus-operator-dev.yaml",
                  "resources: [deployments, replicasets]\n    verbs: [get, list, watch]",
                  "resources: [deployments, replicasets]\n    verbs: [get, list, watch, patch]")
        self.assertEqual(self.rules(), ["B3"])

    def test_b3_cluster_wide_scale(self):
        self.edit("platform/rbac/nexus-operator-dev.yaml",
                  "resources: [deployments, replicasets]\n    verbs: [get, list, watch]",
                  "resources: [deployments/scale]\n    verbs: [get, patch, update]")
        self.assertEqual(self.rules(), ["B3"])

    def test_b3_wildcard(self):
        self.edit("platform/rbac/nexus-operator-dev.yaml",
                  "resources: [pods/eviction]\n    verbs: [create]",
                  "resources: ['*']\n    verbs: [create]")
        self.assertEqual(self.rules(), ["B3"])

    def test_b4_scale_without_delegation(self):
        self.edit("platform/argocd/applications/sample-api-dev.yaml",
                  "      name: sample-api\n", "      name: other\n")
        self.assertEqual(self.rules(), ["B4"])

    def test_b4_new_namespace_grant_requires_delegation(self):
        # The M3 prod grant: scale in nexus-prod while sample-api-prod delegates nothing.
        grant = (self.tmp / "platform/rbac/nexus-operator-dev.yaml").read_text().replace("nexus-dev", "nexus-prod")
        (self.tmp / "platform/rbac/nexus-operator-prod.yaml").write_text(grant)
        self.edit("platform/rbac/kustomization.yaml", "  - nexus-operator-dev.yaml",
                  "  - nexus-operator-dev.yaml\n  - nexus-operator-prod.yaml")
        self.assertEqual(cid.check(self.tmp), [])  # prod delegates sample-api: still clean
        self.edit("platform/argocd/applications/sample-api-prod.yaml",
                  "      name: sample-api\n", "      name: other\n")
        self.assertEqual(self.rules(), ["B4"])

    def test_b5_bare_pod(self):
        pod = "apiVersion: v1\nkind: Pod\nmetadata:\n  name: loose\nspec:\n  containers: []\n"
        (self.tmp / "apps/sample-api/k8s/base/pod.yaml").write_text(pod)
        self.edit("apps/sample-api/k8s/base/kustomization.yaml", "  - pdb.yaml", "  - pdb.yaml\n  - pod.yaml")
        self.assertEqual(self.rules(), ["B5"])


if __name__ == "__main__":
    unittest.main()
