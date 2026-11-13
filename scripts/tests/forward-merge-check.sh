#!/usr/bin/env bash
# forward-merge-check.sh — offline tests for scripts/forward-merge-check.sh, on fixture trees built in
# a throwaway git repository. Run by .github/scripts/repo-checks.sh. Needs bash and git; no cluster.
#
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script=$here/../forward-merge-check.sh
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cd "$work"
git init -q -b main
git config user.email t@example.invalid
git config user.name t
git config commit.gpgsign false

ns() {   # <level> [extra label line]
  printf 'apiVersion: v1\nkind: Namespace\nmetadata:\n  name: nexus-dev\n  labels:\n    app.kubernetes.io/part-of: nexus-platform\n    nexus.io/autonomy-level: "%s"\n%s    pod-security.kubernetes.io/enforce: restricted\n' "$1" "${2:+    $2$'\n'}"
}
fresh() {   # reset the working tree to the base content
  mkdir -p overlays/dev apps
  ns 0 > overlays/dev/namespace.yaml
  printf 'a: 1\n' > apps/x.yaml
  chmod 644 overlays/dev/namespace.yaml
}
failed=0
snap() { git add -A && git commit -q --allow-empty -m "$1" && git rev-parse HEAD; }
fresh; MAIN=$(snap main)

case_() {   # <name> <want rc> <must match> <merge sha>
  local name=$1 want=$2 match=$3 out rc
  set +e; out=$(bash "$script" "$4" "$MAIN" 2>&1); rc=$?; set -e
  if [[ $rc == "$want" ]] && grep -qE "$match" <<<"$out"; then echo "PASS $name: rc=$rc"
  else echo "FAIL $name: rc=$rc want=$want"; sed 's/^/  | /' <<<"$out"; failed=1; fi
}
mk() { git checkout -q -B t "$MAIN"; "$@"; snap t; }

case_ identical 0 'trees are identical' "$(mk true)"
case_ level-0-to-3 1 'M.overlays/dev/namespace.yaml' "$(mk bash -c 'sed -i "s/\"0\"/\"3\"/" overlays/dev/namespace.yaml')"
case_ other-file-only 1 'M.apps/x.yaml' "$(mk bash -c 'echo b: 2 > apps/x.yaml')"
case_ level-and-other-file 1 'M.apps/x.yaml' "$(mk bash -c 'sed -i "s/\"0\"/\"3\"/" overlays/dev/namespace.yaml; echo b: 2 > apps/x.yaml')"
case_ added-file 1 '[AD].new.yaml' "$(mk bash -c 'echo n: 1 > new.yaml')"
case_ deleted-file 1 '[AD].apps/x.yaml' "$(mk git rm -q apps/x.yaml)"
case_ mode-change 1 'M.overlays/dev/namespace.yaml' "$(mk bash -c 'chmod 755 overlays/dev/namespace.yaml; git update-index --chmod=+x overlays/dev/namespace.yaml 2>/dev/null; true')"
case_ tree-ids 0 'trees are identical' "$(git rev-parse "$MAIN^{tree}")"
case_ bad-ref 2 'cannot read' deadbeefdeadbeef
if bash "$script" 2>/dev/null; then echo "FAIL usage: no arguments passed"; failed=1; else echo "PASS usage: rc=$?"; fi

exit $failed
