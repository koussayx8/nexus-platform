#!/usr/bin/env bash
# Offline test for .github/scripts/bump-digest.sh, run against a copy of the real files so that a
# change to their layout fails here, not in the first digest-bump job on main.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
work=$(mktemp -d); trap 'rm -rf -- "$work"' EXIT
new=sha256:$(printf 'b%.0s' {1..64})
fail() { echo "FAIL: $*" >&2; exit 1; }

seed() {
  for f in overlays/dev/kustomization.yaml overlays/prod/kustomization.yaml operator/k8s/deployment.yaml; do
    mkdir -p "$work/$(dirname "$f")"; cp "$f" "$work/$f"
  done
}
seed

ROOT=$work bash .github/scripts/bump-digest.sh sample-api "$new" >/dev/null
for env in dev prod; do
  [ "$(grep -c "digest: $new" "$work/overlays/$env/kustomization.yaml")" = 1 ] || fail "sample-api $env not bumped"
done
grep -q "nexus-operator@sha256:" "$work/operator/k8s/deployment.yaml" && ! grep -q "$new" "$work/operator/k8s/deployment.yaml" \
  || fail "sample-api bump touched the operator"
diff <(git diff --no-index --numstat overlays/dev/kustomization.yaml "$work/overlays/dev/kustomization.yaml" | cut -f1,2) <(printf '1\t1\n') \
  || fail "sample-api bump changed more than one line in overlays/dev"

ROOT=$work bash .github/scripts/bump-digest.sh nexus-operator "$new" >/dev/null
grep -q "nexus-operator@$new" "$work/operator/k8s/deployment.yaml" || fail "operator not bumped"

# Negative controls: a malformed digest, an unknown image and an ambiguous target all fail.
ROOT=$work bash .github/scripts/bump-digest.sh sample-api sha256:abc 2>/dev/null && fail "short digest accepted"
ROOT=$work bash .github/scripts/bump-digest.sh other "$new" 2>/dev/null && fail "unknown image accepted"
printf '    digest: %s\n' "$new" >> "$work/overlays/dev/kustomization.yaml"
ROOT=$work bash .github/scripts/bump-digest.sh sample-api "sha256:$(printf 'c%.0s' {1..64})" 2>/dev/null && fail "ambiguous target accepted"
grep -q "digest: sha256:c" "$work/overlays/prod/kustomization.yaml" && fail "partial write on failure"
echo "bump-digest tests passed"
