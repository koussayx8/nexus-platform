#!/usr/bin/env bash
# repo-checks.sh — the required check on every pull request to main and dev (ADR-012).
#
#   1. GitLeaks over the given commit range, and over the committed tree at HEAD.
#      Never the full history: dead credentials stay there by design (ADR-010).
#   2. kustomize build of every kustomization outside parked paths.
#   3. kubeconform -strict on that output, with pinned Kubernetes and CRD schemas.
#   4. Every ArgoCD Application rendered as ArgoCD would (helm template with the value files from
#      Git, kustomize build, directories), checked against its AppProject, then kubeconform -strict.
#
# Usage: repo-checks.sh "<git log range>"   e.g. "abc123..def456" or "-1 def456"
# Needs gitleaks, kustomize, kubeconform, helm, yq and python3 on PATH (CI installs pinned,
# checksummed builds).
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

echo "::group::kubeconform (Kubernetes $K8S_VERSION, pinned CRD catalog)"
kubeconform -strict -summary -output text \
  -kubernetes-version "$K8S_VERSION" \
  -schema-location "$K8S_SCHEMAS" \
  -schema-location "$CRD_SCHEMAS" \
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
