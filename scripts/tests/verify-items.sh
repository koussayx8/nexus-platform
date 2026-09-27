#!/usr/bin/env bash
# verify-items.sh — offline tests for verify-state.sh's M10 /items check (TASKS.md M1-5).
# Run by .github/scripts/repo-checks.sh. Needs bash, jq, curl, python3 (standard library) and
# coreutils; no cluster, no network beyond 127.0.0.1.
#
# Isolation: KUBECONFIG=/nonexistent and a stub kubectl first on PATH, so nothing here can reach a
# real cluster. The stub answers only `get svc sample-api` (port 80) and `port-forward`, which it
# replaces with scripts/tests/fixtures/items/server.py on the local port; any other call fails and
# is logged. v_items and v_items_probe are extracted from verify-state.sh itself and run behind
# the real k() wrapper from scripts/lib/readonly.sh.
#
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$here/../..
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

export KUBECONFIG=/nonexistent
mkdir -p "$work/bin"
cat > "$work/bin/kubectl" <<EOF
#!/usr/bin/env bash
# Stub: every call is logged; only the two calls v_items makes are answered.
printf '%s\n' "\$*" >> "$work/kubectl.log"
ns= lport=
for ((i = 1; i <= \$#; i++)); do
  a=\${!i}
  [[ \$a == -n ]] && { j=\$((i + 1)); ns=\${!j}; }
  [[ \$a =~ ^([0-9]+):80\$ ]] && lport=\${BASH_REMATCH[1]}
done
case " \$* " in
  *" get svc sample-api "*) echo 80 ;;
  *" port-forward "*)
    mode_var=MODE_\${ns//-/_}
    exec python3 "$here/fixtures/items/server.py" "\$lport" "\${!mode_var}" ;;
  *) echo "stub kubectl: unexpected call: \$*" >> "$work/unexpected.log"; exit 1 ;;
esac
EOF
chmod +x "$work/bin/kubectl"
export PATH=$work/bin:$PATH

# The functions under test, extracted verbatim from verify-state.sh.
sed -n '/^v_items() {/,/^}/p; /^v_items_probe() {/,/^}/p' "$root/scripts/verify-state.sh" > "$work/funcs.sh"
grep -q '^v_items_probe() {' "$work/funcs.sh" || { echo "FAIL could not extract v_items_probe"; exit 1; }

# case name | NEXUS_VERIFY_ITEMS_NAMESPACES | dev mode | prod mode | want rc | output must match |
# output must not match (empty: no such check)
cases=(
  "both-ok|nexus-dev nexus-prod|ok|ok|0|nexus-prod: /items HTTP 200, rows=20|skipped"
  "run1-prod-only|nexus-prod|notfound|ok|0|nexus-dev: skipped|nexus-dev: /items"
  "dev-old-image|nexus-dev nexus-prod|notfound|ok|1|nexus-dev: /items HTTP 404$|error="
  "empty-list|nexus-dev nexus-prod|empty|empty|1|rows=0|"
  "db-unavailable|nexus-dev nexus-prod|db|ok|1|nexus-dev: /items HTTP 503 error=db_unavailable|"
  "unexpected-error-text|nexus-dev nexus-prod|junk|ok|1|nexus-dev: /items HTTP 503$|SHOULD-NOT-APPEAR"
)

failed=0
for c in "${cases[@]}"; do
  IFS='|' read -r name nss dev prod want match nomatch <<<"$c"
  set +e
  out=$(
    export MODE_nexus_dev=$dev MODE_nexus_prod=$prod
    OUT=$work/out; mkdir -p "$OUT"
    # shellcheck source=../lib/readonly.sh
    source "$root/scripts/lib/readonly.sh"
    # shellcheck disable=SC1091
    source "$work/funcs.sh"
    # shellcheck disable=SC2034  # read by v_items, sourced above
    ITEMS_NAMESPACES=$nss
    v_items
  )
  rc=$?
  set -e
  if [[ $rc == "$want" ]] && grep -qE "$match" <<<"$out" && { [[ -z $nomatch ]] || ! grep -qE "$nomatch" <<<"$out"; }; then
    echo "PASS $name: rc=$rc"
  else
    echo "FAIL $name: rc=$rc want=$want"
    while IFS= read -r line; do echo "  | $line"; done <<<"$out"
    failed=1
  fi
done

# Argument validation, against the real script: an invalid value exits 2 before any git or kubectl
# call. A valid value is never run here: it would run the whole script.
for v in "nexus-staging" "   " "nexus-prod nexus-data"; do
  set +e
  err=$(NEXUS_VERIFY_ITEMS_NAMESPACES=$v "$root/scripts/verify-state.sh" --out "$work/report.md" 2>&1 >/dev/null)
  rc=$?
  set -e
  if [[ $rc == 2 && $err == *NEXUS_VERIFY_ITEMS_NAMESPACES* ]]; then
    echo "PASS invalid-namespaces '$v': exit 2"
  else
    echo "FAIL invalid-namespaces '$v': exit $rc ($err)"; failed=1
  fi
done

if [[ -s $work/unexpected.log ]]; then echo "FAIL unexpected kubectl calls:"; cat "$work/unexpected.log"; failed=1; fi
if pgrep -f "$here/fixtures/items/server.py" >/dev/null; then echo "FAIL a stub server is still running"; failed=1; fi

exit $failed
