#!/usr/bin/env bash
# repo-checks.sh — the required check on every pull request to main and dev (ADR-012).
#
#   1. GitLeaks over the given commit range, and over the committed tree at HEAD.
#      Never the full history: dead credentials stay there by design (ADR-010).
#   2. kustomize build of every kustomization outside parked paths.
#   3. kubeconform -strict on that output, with pinned Kubernetes and CRD schemas.
#   4. Every ArgoCD Application rendered as ArgoCD would (helm template with the value files from
#      Git, kustomize build, directories), checked against its AppProject, then kubeconform -strict.
#   5. Offline fixture tests for scripts/lib/apps-stable.jq (TASKS.md M1-3 commit 1).
#   6. Offline tests for verify-state.sh's /items check (TASKS.md M1-5), isolated from any cluster
#      (KUBECONFIG=/nonexistent, a stub kubectl first on PATH).
#   7. Offline fixture tests for scripts/lib/detection-inputs.jq (M1b-6c; jq only).
#   8. promtool check and unit tests for the PrometheusRules in platform/observability/alerts
#      (TASKS.md M1b-7, ADR-024).
#   9. The S5 pin guard (M1b-8, ADR-025): the 5 rule-defining files equal #79's head d351d964.
#      Expires at the M1b exit: after S5 is graded, removed or re-pinned through an ADR.
#  10. Offline unit tests for the M1b-9 calibration ramp and knee script (experiments/calibration;
#      standard library only, no network, ADR-026).
#
# Usage: repo-checks.sh "<git log range>"   e.g. "abc123..def456" or "-1 def456"
# Needs gitleaks, kustomize, kubeconform, helm, yq, promtool, jq, curl and python3 on PATH (CI
# installs pinned, checksummed builds).
set -euo pipefail

RANGE=${1:?usage: repo-checks.sh "<git log range>"}
PARKED_RE='^platform/crossplane/'              # parked (ADR-011): GitLeaks only
K8S_VERSION=1.34.6                             # k3s v1.34.6+k3s1 on the pre-M0 cluster
K8S_SCHEMAS='https://raw.githubusercontent.com/yannh/kubernetes-json-schema/de494cc24a8c0b999c3240319d0dab08626d31da/{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json'
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/ad3b08c5045129d7bb1eeffd8e61719b2c8dd1e2/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

cd "$(git rev-parse --show-toplevel)"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

echo "::group::GitLeaks: commit range ($RANGE)"
gitleaks git --redact --no-banner --log-opts="$RANGE" .
echo "::endgroup::"

echo "::group::GitLeaks: committed tree at $(git rev-parse --short HEAD)"
mkdir "$work/tree"
git archive --format=tar HEAD | tar -x -C "$work/tree"
gitleaks dir --redact --no-banner "$work/tree"
echo "::endgroup::"

echo "::group::kustomize build (parked paths skipped)"
mkdir "$work/out"
mapfile -t dirs < <(git ls-files '*kustomization.yaml' '*kustomization.yml' | xargs -r -n1 dirname | grep -vE "$PARKED_RE" | sort -u)
for d in "${dirs[@]}"; do
  echo "kustomize build $d"
  kustomize build "$d" > "$work/out/${d//\//_}.yaml"
done
echo "built ${#dirs[@]} kustomizations"
echo "::endgroup::"

# CRD objects are skipped here too (no pinned top-level CustomResourceDefinition schema). The
# Incident CRD in platform/crds/ is validated by a real API server instead: incident-crd.sh on the
# envtest harness (M1b-6), run by hand.
echo "::group::kubeconform (Kubernetes $K8S_VERSION, pinned CRD catalog)"
kubeconform -strict -summary -output text \
  -kubernetes-version "$K8S_VERSION" \
  -schema-location "$K8S_SCHEMAS" \
  -schema-location "$CRD_SCHEMAS" \
  -skip CustomResourceDefinition \
  "$work/out"
echo "::endgroup::"

echo "::group::Render every Application and check its AppProject"
bash "$(dirname "$0")/render-apps.sh" "$work/render"
echo "::endgroup::"

# No pinned schema source has a top-level CustomResourceDefinition schema (yannh ships only its
# sub-schemas), so CRD objects are skipped; the custom resources themselves are validated (ADR-012).
echo "::group::kubeconform on rendered Applications (Kubernetes $K8S_VERSION)"
kubeconform -strict -summary -output text \
  -kubernetes-version "$K8S_VERSION" \
  -schema-location "$K8S_SCHEMAS" \
  -schema-location "$CRD_SCHEMAS" \
  -skip CustomResourceDefinition \
  "$work/render/apps"
echo "::endgroup::"

echo "::group::apps-stable.jq fixture tests"
bash scripts/tests/apps-stable.sh
echo "::endgroup::"

echo "::group::verify-state /items tests (offline, stub kubectl)"
KUBECONFIG=/nonexistent bash scripts/tests/verify-items.sh
echo "::endgroup::"

echo "::group::detection-inputs.jq fixture tests (M1b-6c)"
bash scripts/tests/detection-inputs.sh
echo "::endgroup::"

echo "::group::PrometheusRule check and unit tests (promtool)"
bash scripts/tests/promtool-rules.sh
echo "::endgroup::"

# The S5 exit demo runs the detection rules pinned at #79's head (TASKS.md M1b exit); a change to
# any of these 5 paths means an S5 rerun. sample-api-error-rate.yaml is absent at the pin, so its
# absence is pinned too. Expires at the M1b exit: after S5 is graded, this step is removed or
# re-pinned through an ADR (plan M1b-8 rev 3, ADR-025).
S5_PIN=d351d964f2a50ffd07916e69f30e6c067e567cb9
S5_PATHS=(
  platform/observability/alerts/kustomization.yaml
  platform/observability/alerts/nexus-detection.yaml
  platform/observability/alerts/sample-api-error-rate.yaml
  platform/observability/tests/nexus-detection.test.yaml
  scripts/tests/promtool-rules.sh
)
echo "::group::S5 pin guard (the 5 rule-defining files at ${S5_PIN:0:8})"
if ! git diff --quiet "$S5_PIN" HEAD -- "${S5_PATHS[@]}"; then
  git diff --stat "$S5_PIN" HEAD -- "${S5_PATHS[@]}"
  echo "::error::the S5-pinned rule files differ from ${S5_PIN}; a change here means an S5 rerun (TASKS.md M1b exit)"
  exit 1
fi
echo "the 5 pinned paths equal ${S5_PIN}"
echo "::endgroup::"

echo "::group::calibration ramp and knee tests (M1b-9, offline)"
python3 -m unittest discover -s experiments/calibration -p 'test_*.py' -v
echo "::endgroup::"
