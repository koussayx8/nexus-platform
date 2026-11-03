#!/usr/bin/env bash
# configmap-refs.sh — offline tests for .github/scripts/configmap-refs.py (promotion 1 fix).
# Run by .github/scripts/repo-checks.sh. Needs bash and python3 (standard library); no cluster.
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
checker=$here/../../.github/scripts/configmap-refs.py
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

cm()  { printf '{"kind":"ConfigMap","metadata":{"name":"%s"}}\n' "$1"; }
sts() { printf '{"kind":"StatefulSet","metadata":{"name":"db"},"spec":{"template":{"spec":{%s}}}}\n' "$1"; }
VOL() { printf '"volumes":[{"name":"v","configMap":{"name":"%s"%s}}]' "$1" "${2:-}"; }
ENVFROM() { printf '"containers":[{"name":"c","envFrom":[{"configMapRef":{"name":"%s"}}]}]' "$1"; }
KEYREF() { printf '"containers":[{"name":"c","env":[{"name":"E","valueFrom":{"configMapKeyRef":{"name":"%s","key":"k"}}}]}]' "$1"; }
PROJ() { printf '"volumes":[{"name":"p","projected":{"sources":[{"configMap":{"name":"%s"}}]}}]' "$1"; }

failed=0
# case <name> <want rc> <must match (ERE, empty: none)>; the objects come from stdin
runcase() {
  local name=$1 want=$2 match=$3 d=$work/$1 out rc
  mkdir -p "$d"; cat > "$d/app.objects.jsonl"
  set +e; out=$(python3 "$checker" "$d" 2>&1); rc=$?; set -e
  if [[ $rc == "$want" ]] && { [[ -z $match ]] || grep -qE "$match" <<<"$out"; }; then
    echo "PASS $name: rc=$rc"
  else
    echo "FAIL $name: rc=$rc want=$want"; sed 's/^/  | /' <<<"$out"; failed=1
  fi
}

{ cm flight-recorder-db-init-g4t2kk9gm5; sts "$(VOL flight-recorder-db-init)"; } \
  | runcase generated-name-not-rewritten 1 "references ConfigMap 'flight-recorder-db-init'.*did you mean 'flight-recorder-db-init-g4t2kk9gm5'"
{ cm flight-recorder-db-init-g4t2kk9gm5; sts "$(VOL flight-recorder-db-init-g4t2kk9gm5)"; } | runcase rewritten 0 "0 unresolved"
sts "$(VOL nexus-operator-config)" | runcase external-allow-listed 0 "0 unresolved"
sts "$(VOL some-other-config)" | runcase not-allow-listed 1 "'some-other-config', not in the render"
sts "$(VOL missing ',"optional":true')" | runcase optional-skipped 0 "0 unresolved"
{ cm a; sts "$(ENVFROM b)"; } | runcase envfrom-missing 1 "configMapRef references ConfigMap 'b'"
{ cm a; sts "$(ENVFROM a)"; } | runcase envfrom-ok 0 "0 unresolved"
{ cm a; sts "$(KEYREF b)"; } | runcase keyref-missing 1 "configMapKeyRef references ConfigMap 'b'"
{ cm a; sts "$(PROJ b)"; } | runcase projected-missing 1 "projected.*references ConfigMap 'b'"
{ cm a; sts "$(PROJ a)"; } | runcase projected-ok 0 "0 unresolved"
: | runcase empty-app 0 "0 objects"

# No rendered Applications at all is a usage error, not a pass.
mkdir "$work/none"
set +e; python3 "$checker" "$work/none" >/dev/null 2>&1; rc=$?; set -e
[[ $rc == 2 ]] && echo "PASS empty-dir: rc=2" || { echo "FAIL empty-dir: rc=$rc want=2"; failed=1; }
exit $failed
