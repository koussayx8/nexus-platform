#!/usr/bin/env bash
# bump-digest.sh — rewrite the pinned image digest of one NEXUS image in Git (spec §19: a digest
# bump is a commit). Used by the digest-bump job in ci.yml and operator.yml; CI holds no cluster
# credential, so the bump reaches the cluster only when its pull request is merged.
#
#   bump-digest.sh sample-api|nexus-operator sha256:<64 hex>
#
# sample-api     -> images[].digest in overlays/dev and overlays/prod kustomization.yaml
# nexus-operator -> the image: line in operator/k8s/deployment.yaml
# Each target must contain exactly one match, or the script fails without writing anything.
# ROOT (default: the repository root) is for the offline test.
set -euo pipefail

image=${1:?usage: bump-digest.sh sample-api|nexus-operator sha256:<digest>}
digest=${2:?usage: bump-digest.sh sample-api|nexus-operator sha256:<digest>}
root=${ROOT:-$(git rev-parse --show-toplevel)}
repo=ghcr.io/koussayx8/nexus-platform

[[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "not a sha256 digest: $digest" >&2; exit 2; }

case $image in
  sample-api)
    files=("$root/overlays/dev/kustomization.yaml" "$root/overlays/prod/kustomization.yaml")
    pattern='^(    digest: )sha256:[0-9a-f]{64}$'
    ;;
  nexus-operator)
    files=("$root/operator/k8s/deployment.yaml")
    pattern="^(          image: ${repo//./\\.}/nexus-operator@)sha256:[0-9a-f]{64}\$"
    ;;
  *) echo "unknown image: $image" >&2; exit 2 ;;
esac

# Check every file first, so a failure leaves all of them unchanged.
for f in "${files[@]}"; do
  n=$(grep -cE "$pattern" "$f" || true)
  [ "$n" = 1 ] || { echo "$f: expected exactly 1 digest line, found $n" >&2; exit 1; }
done
for f in "${files[@]}"; do
  sed -E -i "s#${pattern}#\\1${digest}#" "$f"
  echo "bumped ${f#"$root"/} -> $digest"
done
