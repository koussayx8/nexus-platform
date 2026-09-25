#!/usr/bin/env bash
# render-apps.sh — render every ArgoCD Application offline, as ArgoCD would, then validate it (ADR-012).
#
#   * chart sources:  helm template <release> <chart@version> with value files from Git ($values/...)
#                     or inline values, --include-crds, --kube-version pinned;
#   * path sources:   kustomize build, or the directory's manifests (ArgoCD directory mode);
#   * every rendered object must be admitted by the Application's AppProject (destination,
#     source repository, cluster and namespace resource lists): project-check.py;
#   * the output is validated with kubeconform -strict by repo-checks.sh.
#
# Usage: render-apps.sh <output dir>
# Needs helm, kustomize, yq and python3 on PATH. Git path sources are rendered from the working
# tree, whatever their targetRevision: Git convergence validates the tree that will be merged.
set -euo pipefail

OUT=${1:?usage: render-apps.sh <output dir>}
KUBE_VERSION=${KUBE_VERSION:-1.34.6}
PARKED_RE='^platform/crossplane/'
THIS_REPO='https://github.com/koussayx8/nexus-platform.git'

cd "$(git rev-parse --show-toplevel)"
mkdir -p "$OUT/apps" "$OUT/charts" "$OUT/meta"

mapfile -t app_files < <(ls platform/argocd/root.yaml platform/argocd/applications/*.yaml 2>/dev/null)
[[ ${#app_files[@]} -gt 0 ]] || { echo "no Application manifests found" >&2; exit 1; }

for f in "${app_files[@]}"; do
  [[ $(yq '.kind' "$f") == Application ]] || { echo "$f: not an Application" >&2; exit 1; }
  app=$(yq '.metadata.name' "$f")
  ns=$(yq '.spec.destination.namespace // ""' "$f")
  out="$OUT/apps/$app.yaml"
  : > "$out"
  yq -o=json -I=0 '.' "$f" > "$OUT/meta/$app.app.json"
  if [[ $(yq '.spec.sources | length' "$f") -gt 0 ]]; then base='.spec.sources'; else base='[.spec.source]'; fi
  n=$(yq "$base | length" "$f")
  echo "== $app ($f): $n source(s), destination namespace ${ns:-<none>}"
  for ((i = 0; i < n; i++)); do
    s="$base[$i]"
    repo=$(yq "$s.repoURL" "$f"); chart=$(yq "$s.chart // \"\"" "$f"); path=$(yq "$s.path // \"\"" "$f")
    rev=$(yq "$s.targetRevision // \"\"" "$f"); ref=$(yq "$s.ref // \"\"" "$f")
    if [[ -n $chart ]]; then
      dir="$OUT/charts/$chart-$rev"
      if [[ ! -d $dir ]]; then
        mkdir -p "$dir"
        helm pull "$chart" --repo "$repo" --version "$rev" --untar --untardir "$dir" >/dev/null
      fi
      release=$(yq "$s.helm.releaseName // \"$app\"" "$f")
      vals=()
      while IFS= read -r vf; do
        [[ -z $vf ]] && continue
        [[ $vf == \$values/* ]] || { echo "$app: value file $vf is not a \$values/ reference" >&2; exit 1; }
        [[ -f ${vf#\$values/} ]] || { echo "$app: value file ${vf#\$values/} not found" >&2; exit 1; }
        vals+=(-f "${vf#\$values/}")
      done < <(yq "$s.helm.valueFiles // [] | .[]" "$f")
      if [[ $(yq "$s.helm.values // \"\"" "$f") != "" ]]; then
        yq "$s.helm.values" "$f" > "$OUT/meta/$app.$i.inline-values.yaml"
        vals+=(-f "$OUT/meta/$app.$i.inline-values.yaml")
        echo "   WARNING: inline helm values (ADR-012 expects value files from Git)"
      fi
      echo "   helm template $release $chart@$rev ${vals[*]}"
      helm template "$release" "$dir/$chart" --namespace "$ns" --kube-version "$KUBE_VERSION" \
        --include-crds "${vals[@]}" >> "$out"
      printf '\n---\n' >> "$out"
    elif [[ -n $path ]]; then
      [[ $repo == "$THIS_REPO" ]] || { echo "$app: path source from another repository: $repo" >&2; exit 1; }
      if [[ $path/ =~ $PARKED_RE ]]; then echo "   skip parked path $path"; continue; fi
      if [[ -f $path/kustomization.yaml || -f $path/kustomization.yml ]]; then
        echo "   kustomize build $path   (targetRevision $rev)"
        kustomize build "$path" >> "$out"
      else
        echo "   directory $path   (targetRevision $rev)"
        for m in "$path"/*.yaml "$path"/*.yml; do
          [[ -f $m ]] || continue
          cat "$m" >> "$out"; printf '\n---\n' >> "$out"
        done
      fi
      printf '\n---\n' >> "$out"
    elif [[ -n $ref ]]; then
      echo "   ref '$ref' ($repo) provides value files"
    else
      echo "$app: source $i has neither chart, path nor ref" >&2; exit 1
    fi
  done
  # One JSON document per line, for the AppProject check.
  yq -o=json -I=0 'select(. != null)' "$out" > "$OUT/meta/$app.objects.jsonl"
done

for p in platform/argocd/projects/*.yaml; do
  yq -o=json -I=0 '.' "$p" > "$OUT/meta/project.$(yq '.metadata.name' "$p").json"
done

python3 "$(dirname "$0")/project-check.py" "$OUT/meta"
