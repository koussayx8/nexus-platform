#!/usr/bin/env bash
# verify-m3.sh — offline tests for verify-state.sh's M3 checks (G7; --milestone m3): M16 (K1-K6
# present, Ready, failurePolicy Fail, Enforce), M17 (N1-N6 NetworkPolicies present), M18 (Reasoner
# Ready with the expected index hash), and the flag handling. Run by .github/scripts/repo-checks.sh.
# Needs bash, jq and coreutils.
#
# Isolation: KUBECONFIG=/nonexistent and a stub kubectl first on PATH, so nothing here can reach a
# real cluster. The stub answers from fixture files in $work/fx and logs every call; a call without
# `--context default`, or one it does not know, is logged as unexpected and fails the run. The check
# functions are extracted from verify-state.sh and run behind the real k() wrapper.
#
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$here/../..
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

export KUBECONFIG=/nonexistent
mkdir -p "$work/bin" "$work/fx"
cat > "$work/bin/kubectl" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$work/kubectl.log"
fx=$work/fx
case " \$* " in *" --context default "*) ;; *) echo "stub kubectl: no --context default: \$*" >> "$work/unexpected.log"; exit 1 ;; esac
case " \$* " in
  *" get clusterpolicy "*) all="\$*"; n=\${all#*get clusterpolicy }; n=\${n%% *}; [[ -e \$fx/cp-\$n.json ]] && cat "\$fx/cp-\$n.json" || exit 1 ;;
  *" get networkpolicy "*) all="\$*"; n=\${all#*get networkpolicy }; n=\${n%% *}; ns=\${all#* -n }; ns=\${ns%% *}
      [[ -e \$fx/np-\$ns-\$n ]] || exit 1 ;;
  *" get deployment nexus-reasoner "*) [[ -e \$fx/reasoner-deploy.json ]] && cat "\$fx/reasoner-deploy.json" || exit 1 ;;
  *" get --raw /api/v1/namespaces/nexus-reasoner/services/http:nexus-reasoner:8080/proxy/healthz "*) [[ -e \$fx/healthz.json ]] && cat "\$fx/healthz.json" || exit 1 ;;
  *) echo "stub kubectl: unexpected call: \$*" >> "$work/unexpected.log"; exit 1 ;;
esac
STUB
chmod +x "$work/bin/kubectl"
export PATH=$work/bin:$PATH

sed -n '/^K_POLICIES=/p; /^v_policies_enforce() {/,/^}/p; /^NETPOLS=(/,/^)/p; /^v_network_policies() {/,/^}/p; /^v_reasoner() {/,/^}/p' \
  "$root/scripts/verify-state.sh" > "$work/funcs.sh"
for f in v_policies_enforce v_network_policies v_reasoner; do
  grep -q "^$f() {" "$work/funcs.sh" || { echo "FAIL could not extract $f"; exit 1; }
done
grep -q '^NETPOLS=(' "$work/funcs.sh" || { echo "FAIL could not extract NETPOLS"; exit 1; }

K_NAMES=(nexus-autonomy-floor nexus-scale-bounds nexus-l2-approval nexus-killswitch nexus-incident-approval nexus-incident-source)
policy() {   # <name> <failurePolicy> <ready true|false> <action...: one per validate rule; "-" for a mutate-only rule>
  local name=$1 fp=$2 ready=$3 rules='' a; shift 3
  for a in "$@"; do
    if [[ $a == - ]]; then rules+='{"mutate":{}},'; else rules+="{\"validate\":{\"failureAction\":\"$a\"}},"; fi
  done
  printf '{"spec":{"failurePolicy":"%s","rules":[%s]},"status":{"conditions":[{"type":"Ready","status":"%s"}]}}' \
    "$fp" "${rules%,}" "$([[ $ready == true ]] && echo True || echo False)" > "$work/fx/cp-$name.json"
}
policies_ok() { local n; for n in "${K_NAMES[@]}"; do policy "$n" Fail true Enforce; done; policy nexus-incident-approval Fail true - Enforce Enforce; }
netpols_ok() { local pair; while read -r pair; do : > "$work/fx/np-${pair% *}-${pair#* }"; done < <(
  sed -n '/^NETPOLS=(/,/^)/p' "$work/funcs.sh" | grep -oE '"[a-z0-9-]+ [a-z0-9-]+"' | tr -d '"'); }
HASH=$(printf 'ab%.0s' {1..32})
reasoner() {   # <ready> <want> <status> <hash>
  printf '{"spec":{"replicas":%s},"status":{"readyReplicas":%s}}' "$2" "$1" > "$work/fx/reasoner-deploy.json"
  printf '{"status":"%s","corpusHash":"%s"}' "$3" "$4" > "$work/fx/healthz.json"
}

failed=0
run_case() {   # <name> <want rc> <must match> <must not match> <function>
  local name=$1 want=$2 match=$3 nomatch=$4 fn=$5 out rc
  set +e
  out=$(
    OUT=$work/out; mkdir -p "$OUT"
    export NEXUS_VERIFY_REASONER_INDEX_HASH=$HASH
    # shellcheck source=../lib/readonly.sh
    source "$root/scripts/lib/readonly.sh"
    # shellcheck disable=SC1091
    source "$work/funcs.sh"
    "$fn"
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
}

# M16
policies_ok; run_case "k-all-enforce" 0 'K6 nexus-incident-source: ready=true failurePolicy=Fail' 'NOT FOUND|ok=false' v_policies_enforce
policies_ok; policy nexus-killswitch Fail true Audit
run_case "k4-audit" 1 'K4 nexus-killswitch: .*enforce=0 ok=false' '' v_policies_enforce
policies_ok; policy nexus-scale-bounds Fail true Enforce Audit
run_case "k2-one-rule-audit" 1 'K2 nexus-scale-bounds: .*validateRules=2 enforce=1 ok=false' '' v_policies_enforce
policies_ok; policy nexus-autonomy-floor Ignore true Enforce
run_case "k1-failure-policy-ignore" 1 'K1 nexus-autonomy-floor: .*failurePolicy=Ignore' '' v_policies_enforce
policies_ok; policy nexus-l2-approval Fail false Enforce
run_case "k3-not-ready" 1 'K3 nexus-l2-approval: ready=false' '' v_policies_enforce
policies_ok; rm "$work/fx/cp-nexus-incident-source.json"
run_case "k6-missing" 1 'K6 nexus-incident-source: NOT FOUND' '' v_policies_enforce
policies_ok; policy nexus-incident-approval Fail true -
run_case "k5-mutate-only" 1 'K5 .*validateRules=0' '' v_policies_enforce
policies_ok; printf '{"spec":{"failurePolicy":"Fail","validationFailureAction":"Enforce","rules":[{"validate":{}}]},"status":{"ready":true}}' > "$work/fx/cp-nexus-scale-bounds.json"
run_case "k2-legacy-field-and-status-ready" 0 'K2 nexus-scale-bounds: ready=true .*enforce=1 ok=true' '' v_policies_enforce

# M17
netpols_ok; run_case "netpol-all" 0 'present: 17 of 17' 'missing' v_network_policies
netpols_ok; rm "$work/fx/np-nexus-reasoner-default-deny"
run_case "netpol-missing" 1 'present: 16 of 17|missing: nexus-reasoner/default-deny' '' v_network_policies
rm -f "$work"/fx/np-*; run_case "netpol-none" 1 'present: 0 of 17' '' v_network_policies

# M18
reasoner 1 1 ready "$HASH"; run_case "reasoner-ok" 0 'status=ready' 'differs' v_reasoner
reasoner 1 1 ready "sha256:$HASH"; run_case "reasoner-prefix-ok" 0 'status=ready' 'differs' v_reasoner
reasoner 1 1 ready "$(printf 'cd%.0s' {1..32})"; run_case "reasoner-wrong-hash" 1 'corpusHash differs' '' v_reasoner
reasoner 1 1 ready ""; run_case "reasoner-no-hash" 1 'corpusHash differs' '' v_reasoner
reasoner 1 1 loading "$HASH"; run_case "reasoner-not-ready-status" 1 'status=loading' '' v_reasoner
reasoner 0 1 ready "$HASH"; run_case "reasoner-deploy-not-ready" 1 'readyReplicas=0 replicas=1' '' v_reasoner
reasoner 1 1 ready "$HASH"; rm "$work/fx/healthz.json"; run_case "reasoner-healthz-silent" 1 'no answer' '' v_reasoner
rm "$work/fx/reasoner-deploy.json"; run_case "reasoner-missing" 1 'NOT FOUND' '' v_reasoner

# Flag handling: all of it happens before verify-state.sh touches git or a cluster.
flag_case() {   # <name> <want rc> <must match> <args...>
  local name=$1 want=$2 match=$3 out rc; shift 3
  set +e; out=$(bash "$root/scripts/verify-state.sh" --out "$work/report.md" "$@" 2>&1); rc=$?; set -e
  if [[ $rc == "$want" ]] && grep -qE "$match" <<<"$out"; then echo "PASS $name: rc=$rc"; else echo "FAIL $name: rc=$rc want=$want"; echo "$out" | sed 's/^/  | /'; failed=1; fi
}
unset NEXUS_VERIFY_REASONER_INDEX_HASH
flag_case "milestone-unknown" 2 "must be m2 or m3" --milestone m4
flag_case "milestone-missing-value" 2 "must be m2 or m3" --milestone
flag_case "m3-needs-index-hash" 2 "needs NEXUS_VERIFY_REASONER_INDEX_HASH" --milestone m3
NEXUS_VERIFY_REASONER_INDEX_HASH=short flag_case "m3-bad-index-hash" 2 "needs NEXUS_VERIFY_REASONER_INDEX_HASH" --milestone m3

# The default stays M2: the M3 checks are reachable only behind the flag, and M1-M15 are unchanged and M19 is the one default addition.
if [[ $(grep -cE '^verify M[0-9]+ ' "$root/scripts/verify-state.sh") == 16 ]]; then echo "PASS m2-default-has-16-checks"; else echo "FAIL m2-default-has-16-checks"; failed=1; fi
if grep -E '^[[:space:]]+verify M1[678] ' "$root/scripts/verify-state.sh" | wc -l | grep -q '^3$'; then echo "PASS m3-checks-gated"; else echo "FAIL m3-checks-gated"; failed=1; fi

if [[ -s $work/unexpected.log ]]; then echo "FAIL unexpected kubectl calls:"; cat "$work/unexpected.log"; failed=1; fi
exit $failed
