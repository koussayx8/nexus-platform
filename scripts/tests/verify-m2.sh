#!/usr/bin/env bash
# verify-m2.sh — offline tests for verify-state.sh's M2 checks (G7; docs/plans/m2-live-plan.md A3):
# M5's nexus-dev level read from Git, M13 (Flight Recorder DB), M14 (staged RBAC) and M15 (rollout
# strategy). Run by .github/scripts/repo-checks.sh. Needs bash, jq, git and coreutils.
#
# Isolation: KUBECONFIG=/nonexistent and a stub kubectl first on PATH, so nothing here can reach a
# real cluster. The stub answers from fixture files in $work/fx and logs every call; a call without
# `--context default`, or one it does not know, is logged as unexpected and fails the run. Git comes
# from a throwaway repository with origin/main and origin/experiment/dev-state refs, never the
# real one. The functions are extracted from verify-state.sh and run behind the real k() wrapper.
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
ns=
for ((i = 1; i <= \$#; i++)); do
  a=\${!i}
  [[ \$a == -n ]] && { j=\$((i + 1)); ns=\${!j}; }
done
# Every call names the cluster: k() adds --context default itself (lib/readonly.sh).
case " \$* " in *" --context default "*) ;; *) echo "stub kubectl: no --context default: \$*" >> "$work/unexpected.log"; exit 1 ;; esac
case " \$* " in
  *" get ns "*)
    all="\$*"; name=\${all#*get ns }; name=\${name%% *}
    case " \$* " in *" -o "*) cat "\$fx/level-\$name.txt" ;; *) [[ -e \$fx/level-\$name.txt ]] ;; esac ;;
  *)
    case " \$* " in
      *" get --raw /api/v1/namespaces/monitoring/services/http:observability-kube-prometh-prometheus:9090/proxy/api/v1/query?query=nexus_operator_recorder_up "*) [[ -e \$fx/recorder.json ]] && cat "\$fx/recorder.json" || exit 1 ;;
      *" get statefulset flight-recorder-db "*) cat "\$fx/sts.json" ;;
      *" get deployment sample-api "*) cat "\$fx/deploy-\$ns.json" ;;
      *" auth can-i --list "*)
        case " \$* " in *" --as=system:serviceaccount:nexus-system:nexus-operator "*) ;; *) echo "stub kubectl: wrong identity: \$*" >> "$work/unexpected.log"; exit 1 ;; esac
        cat "\$fx/canilist-\$ns.txt" ;;
      *) echo "stub kubectl: unexpected call: \$*" >> "$work/unexpected.log"; exit 1 ;;
    esac ;;
esac
STUB
chmod +x "$work/bin/kubectl"
export PATH=$work/bin:$PATH

sed -n '/^v_namespace_levels() {/,/^}/p; /^v_fr_db() {/,/^}/p; /^OPERATOR_SA=/p; /^v_staged_rbac() {/,/^}/p; /^git_strategy() {/,/^}/p; /^v_rollout_strategy() {/,/^}/p; /^v_recorder_up() {/,/^}/p' \
  "$root/scripts/verify-state.sh" > "$work/funcs.sh"
for f in v_namespace_levels v_fr_db v_staged_rbac git_strategy v_rollout_strategy v_recorder_up; do
  grep -q "^$f() {" "$work/funcs.sh" || { echo "FAIL could not extract $f"; exit 1; }
done

# A throwaway repository standing in for the fetched remote-tracking refs.
repo=$work/repo
mkdir -p "$repo/overlays/dev" "$repo/apps/sample-api/k8s/base"
git -C "$repo" init -q -b main
git -C "$repo" config user.email t@example.invalid
git -C "$repo" config user.name t
strategy() {   # <maxUnavailable>
  printf 'spec:\n  replicas: 2\n  strategy:\n    type: RollingUpdate\n    rollingUpdate:\n      maxSurge: 1\n      maxUnavailable: %s\n  selector:\n    x: y\n' "$1"
}
ns_yaml() { printf 'metadata:\n  name: nexus-dev\n  labels:\n    nexus.io/autonomy-level: "%s"\n' "$1" > "$repo/overlays/dev/namespace.yaml"; }
strategy 0 > "$repo/apps/sample-api/k8s/base/deployment.yaml"
ns_yaml 3
git -C "$repo" add -A && git -C "$repo" commit -q -m base
git -C "$repo" update-ref refs/remotes/origin/main HEAD
git -C "$repo" update-ref refs/remotes/origin/experiment/dev-state HEAD
cd "$repo"

fixture_sts() { printf '{"spec":{"replicas":%s},"status":{"readyReplicas":%s}}' "$1" "$2" > "$work/fx/sts.json"; }
fixture_deploy() {   # <ns> <maxUnavailable>
  printf '{"spec":{"strategy":{"type":"RollingUpdate","rollingUpdate":{"maxSurge":1,"maxUnavailable":%s}}}}' "$2" > "$work/fx/deploy-$1.json"
}
HEADER=$'Resources   Non-Resource URLs   Resource Names   Verbs'
canilist() {   # <ns> <rows...>
  local ns=$1; shift
  { printf '%s\n' "$HEADER"; printf '%s\n' "$@"; } > "$work/fx/canilist-$ns.txt"
}
BASE_ROWS=('selfsubjectreviews.authentication.k8s.io   []   []   [create]' '                                      [/healthz]   []   [get]')
DEV_ROWS=('deployments.apps/scale   []   []   [get patch update]' 'pods/eviction   []   []   [create]')
rbac_ok() { canilist nexus-dev "${BASE_ROWS[@]}" "${DEV_ROWS[@]}"; canilist nexus-prod "${BASE_ROWS[@]}"; canilist nexus-data "${BASE_ROWS[@]}"; }
levels() {   # <prod> <data> <dev> <system> <reasoner> <load>
  printf '%s' "$1" > "$work/fx/level-nexus-prod.txt"; printf '%s' "$2" > "$work/fx/level-nexus-data.txt"
  printf '%s' "$3" > "$work/fx/level-nexus-dev.txt"; printf '%s' "$4" > "$work/fx/level-nexus-system.txt"
  printf '%s' "$5" > "$work/fx/level-nexus-reasoner.txt"; printf '%s' "$6" > "$work/fx/level-nexus-load.txt"
}

failed=0
run_case() {   # <name> <want rc> <must match> <must not match> <function>
  local name=$1 want=$2 match=$3 nomatch=$4 fn=$5 out rc
  set +e
  out=$(
    OUT=$work/out; mkdir -p "$OUT"
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

# M13
fixture_sts 1 1; run_case "fr-db-ready" 0 'readyReplicas=1 replicas=1' '' v_fr_db
fixture_sts 1 0; run_case "fr-db-not-ready" 1 'readyReplicas=0 replicas=1' '' v_fr_db
printf '{"spec":{"replicas":1},"status":{}}' > "$work/fx/sts.json"; run_case "fr-db-no-status" 1 'readyReplicas=0' '' v_fr_db
fixture_sts 0 0; run_case "fr-db-zero-replicas" 1 'replicas=0' '' v_fr_db

# M14
rbac_ok; run_case "rbac-staged" 0 'nexus-dev: scale verbs=\[get patch update\] eviction verbs=\[create\]' 'nexus-prod: scale verbs=\[[a-z]' v_staged_rbac
rbac_ok; canilist nexus-prod "${BASE_ROWS[@]}" 'deployments.apps/scale   []   []   [get patch update]'
run_case "rbac-prod-scale" 1 'nexus-prod: scale verbs=\[get patch update\]' '' v_staged_rbac
rbac_ok; canilist nexus-data "${BASE_ROWS[@]}" 'pods/eviction   []   []   [create]'
run_case "rbac-data-eviction" 1 'nexus-data: .*eviction verbs=\[create\]' '' v_staged_rbac
rbac_ok; canilist nexus-prod "${BASE_ROWS[@]}" '*.*   []   []   [*]'
run_case "rbac-prod-wildcard" 1 'nexus-prod: .*wildcard resources=\[\*\.\*\]' '' v_staged_rbac
rbac_ok; canilist nexus-dev "${BASE_ROWS[@]}" 'pods/eviction   []   []   [create]'
run_case "rbac-dev-no-scale" 1 'nexus-dev: scale verbs=\[-\]' '' v_staged_rbac
rbac_ok; canilist nexus-dev "${BASE_ROWS[@]}" 'deployments.apps/scale   []   []   [get]' 'pods/eviction   []   []   [create]'
run_case "rbac-dev-read-only-scale" 1 'nexus-dev: scale verbs=\[get\]' '' v_staged_rbac
rbac_ok; rm "$work/fx/canilist-nexus-data.txt"
run_case "rbac-list-fails" 1 'nexus-data: auth can-i --list failed' '' v_staged_rbac

# M15
fixture_deploy nexus-dev 0; fixture_deploy nexus-prod 0
run_case "strategy-equal" 0 'nexus-dev: Git .* = RollingUpdate 1 0; live = RollingUpdate 1 0' '' v_rollout_strategy
fixture_deploy nexus-prod 1
run_case "strategy-drift" 1 'nexus-prod: Git .* = RollingUpdate 1 0; live = RollingUpdate 1 1' '' v_rollout_strategy
printf '{"spec":{"strategy":{"type":"Recreate"}}}' > "$work/fx/deploy-nexus-dev.json"; fixture_deploy nexus-prod 0
run_case "strategy-recreate" 1 'nexus-dev: .*live = Recreate - -' '' v_rollout_strategy
fixture_deploy nexus-dev 0; rm "$work/fx/deploy-nexus-prod.json"
run_case "strategy-live-missing" 1 'nexus-prod: .*live = NOT FOUND' '' v_rollout_strategy

# M19
rec() { printf '{"status":"success","data":{"result":[%s]}}' "$1" > "$work/fx/recorder.json"; }
rec '{"metric":{},"value":[1,"1"]}'; run_case "recorder-up" 0 'value=1' '' v_recorder_up
rec '{"metric":{},"value":[1,"0"]}'; run_case "recorder-zero" 1 'value=0' '' v_recorder_up
rec '{"metric":{},"value":[1,"NaN"]}'; run_case "recorder-nan" 1 'value=NaN' '' v_recorder_up
rec ''; run_case "recorder-no-series" 1 '0 series' '' v_recorder_up
rec '{"value":[1,"1"]},{"value":[1,"1"]}'; run_case "recorder-two-series" 1 '2 series' '' v_recorder_up
printf '{"status":"error"}' > "$work/fx/recorder.json"; run_case "recorder-error-status" 1 ': - series' '' v_recorder_up
rm "$work/fx/recorder.json"; run_case "recorder-no-answer" 1 'no answer' '' v_recorder_up

# M5: the nexus-dev level comes from Git, not a constant.
levels 1 0 3 "" "" ""; run_case "level-from-git-3" 0 'nexus-dev: expected level 3 from origin/experiment/dev-state' 'FAIL|UNKNOWN' v_namespace_levels
levels 1 0 0 "" "" ""; run_case "level-live-lags-git" 1 'nexus-dev: label=0 want=3' '' v_namespace_levels
ns_yaml 0; git commit -q -am l0; git update-ref refs/remotes/origin/experiment/dev-state HEAD
levels 1 0 0 "" "" ""; run_case "level-from-git-0" 0 'nexus-dev: label=0 want=0' '' v_namespace_levels
levels 1 0 3 "" "" ""; run_case "level-live-ahead-of-git" 1 'nexus-dev: label=3 want=0' '' v_namespace_levels
git update-ref -d refs/remotes/origin/experiment/dev-state
levels 1 0 0 "" "" ""; run_case "level-git-unreadable" 1 'nexus-dev: no autonomy-level in origin/experiment/dev-state' '' v_namespace_levels

# The audit probe must carry --context (static: the probe itself needs the audit log and a cluster).
if grep -qE '^[[:space:]]*timeout 15 kubectl --context default .*create configmap' "$root/scripts/verify-state.sh"; then
  echo "PASS audit-probe-context"
else
  echo "FAIL audit-probe-context: the probe's kubectl call has no --context default"; failed=1
fi

# Every kubectl call of verify-state.sh and of the shared library names the cluster. Static, because
# the port-forwards and the probe need a cluster: every line that invokes kubectl (comments and the
# guard's messages aside) must read `kubectl --context default`.
for f in scripts/verify-state.sh scripts/lib/readonly.sh; do
  bare=$(grep -nE '(^|[[:space:];&|(])kubectl[[:space:]]' "$root/$f" \
    | grep -vE '^[0-9]+:[[:space:]]*#|guard_fail|disp' | grep -v 'kubectl --context default ' || true)
  if [[ -z $bare ]]; then
    echo "PASS every-kubectl-call-names-the-context: $f"
  else
    echo "FAIL every-kubectl-call-names-the-context: $f"; while IFS= read -r line; do echo "  | $line"; done <<<"$bare"; failed=1
  fi
done
calls=$(grep -cE 'kubectl --context default ' "$root/scripts/verify-state.sh")
if [[ $calls == 3 ]]; then echo "PASS verify-state-direct-calls: the two port-forwards and the audit probe"
else echo "FAIL verify-state-direct-calls: $calls direct kubectl calls, expected 3"; failed=1; fi

# k() is the one place that names it: a call site that passes --context again is a second copy.
if grep -nE '(^|[[:space:](])k (get|describe|logs|auth|version|api-resources)[^#]*--context' "$root/scripts/verify-state.sh" "$root/scripts/capture-state.sh"; then
  echo "FAIL k-names-the-context-once: a k() call passes --context itself"; failed=1
else
  echo "PASS k-names-the-context-once"
fi

# Behind the real wrappers, the stub sees the context first, on reads and on the Secret listing.
: > "$work/kubectl.log"
(
  OUT=$work/out; mkdir -p "$OUT"
  # shellcheck source=../lib/readonly.sh
  source "$root/scripts/lib/readonly.sh"
  k get ns nexus-prod >/dev/null 2>&1 || true
  k get --raw /metrics >/dev/null 2>&1 || true
  secret_names -n nexus-system >/dev/null 2>&1 || true
)
sed -i '/unexpected call/d' "$work/unexpected.log" 2>/dev/null || true   # these three are not fixture calls
want='--context default --request-timeout=20s get ns nexus-prod
--context default --request-timeout=20s get --raw /metrics
--context default --request-timeout=20s get secrets -n nexus-system'
if [[ $(cat "$work/kubectl.log") == "$want" ]]; then
  echo "PASS wrappers-put-the-context-first"
else
  echo "FAIL wrappers-put-the-context-first:"; sed 's/^/  | /' "$work/kubectl.log"; failed=1
fi

if [[ -s $work/unexpected.log ]]; then echo "FAIL unexpected kubectl calls:"; cat "$work/unexpected.log"; failed=1; fi
exit $failed
