#!/usr/bin/env bash
# demo-scripts.sh — offline tests for scripts/demo/{s1,s3,s5,s6,l2,denied}.sh (Block 14 prep).
#
# Isolation: KUBECONFIG=/nonexistent and stubs for kubectl, curl, nexusctl and the runner first on
# PATH / in NEXUS_DEMO_RUNNER, so nothing here can reach a cluster, Locust, GitHub or Git's remote
# (the experiment worktree is a throwaway clone of a local bare repo). Each stub logs every call;
# a kubectl call without `--context default`, or any write other than the server-side dry-run
# scale, fails the run. Safe to run anywhere.
#
# Usage: scripts/tests/demo-scripts.sh      Exit: 0 all cases pass, 1 otherwise.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd "$here/../.." && pwd -P)
work=$(mktemp -d)
trap 'rm -rf "${work:?}"' EXIT
export KUBECONFIG=/nonexistent
failed=0
ok()   { echo "PASS $1"; }
bad()  { echo "FAIL $1: $2"; failed=1; }
check() { if [[ $2 == 1 ]]; then ok "$1"; else bad "$1" "${3:-}"; fi; }

# --- the experiment worktree: a clone of a local bare repo with experiment/dev-state
git init -q --bare "$work/origin.git"
git clone -q "$work/origin.git" "$work/exp" 2> /dev/null
( cd "$work/exp" && git checkout -q -b experiment/dev-state &&
  git -c user.name=t -c user.email=t@t commit -q --allow-empty -m base &&
  git push -q origin experiment/dev-state ) || { echo "FAIL could not build the temp repo"; exit 1; }
base=$(cd "$work/exp" && git rev-parse HEAD)

# --- stubs
mkdir -p "$work/bin"
cat > "$work/bin/kubectl" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$work/kubectl.log"
case " \$* " in *" --context default "*) ;; *) echo "stub kubectl: no --context default: \$*" >> "$work/unexpected.log"; exit 1 ;; esac
case " \$* " in
  *" get incidents -o json "*)
    n=\$(( \$(cat "$work/n" 2>/dev/null || echo 0) + 1 )); inj=\$(cat "$work/injected" 2>/dev/null || echo 0)
    if [[ \$inj == 1 ]]; then
      echo "\$n" > "$work/n"; f="$work/seq.\$n.json"
      [[ -f \$f ]] || f=\$(ls "$work"/seq.*.json | sort -V | tail -1)
      cat "\$f"
    else cat "$work/before.json"; fi ;;
  *" get configmap nexus-killswitch "*) echo active ;;
  *" get deployment sample-api -o json "*) cat "$work/deploy.json" ;;
  *" get namespace nexus-dev "*) cat "$work/level" ;;
  *" scale deployment/sample-api --replicas=40 --dry-run=server --as=system:serviceaccount:nexus-system:nexus-operator "*)
    case "\${SCALE_MODE:-k2}" in
      k2) echo 'Error from server: admission webhook "validate.kyverno.svc-fail" denied the request: nexus-scale-bounds: K2 nexus-scale-bounds: replicas 2 -> 40 is outside [1, 5]' >&2; exit 1 ;;
      rbac) echo 'Error from server (Forbidden): deployments.apps "sample-api" is forbidden' >&2; exit 1 ;;
      admit) echo 'deployment.apps/sample-api scaled (server dry run)' ;;
    esac ;;
  *) echo "stub kubectl: unexpected call: \$*" >> "$work/unexpected.log"; exit 1 ;;
esac
STUB
cat > "$work/bin/curl" <<STUB
#!/usr/bin/env bash
printf 'curl %s\n' "\$*" >> "$work/curl.log"
echo '{"state":"running","user_count":160}'
STUB
cat > "$work/bin/nexusctl" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$work/nexusctl.log"
STUB
cat > "$work/runner" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$work/runner.log"
[[ " \$* " == *" --execute "* ]] || { echo '[]'; exit 0; }
case "\$*" in
  *"run inject "*) echo 1 > "$work/injected" ;;
  *"run reset "*) lv=\$(sed -E 's/.*--level ([0-9]).*/\1/' <<< "\$*"); echo "\$lv" > "$work/level" ;;
esac
echo '[{"ok":true}]'
STUB
chmod +x "$work/bin/"* "$work/runner"
export PATH=$work/bin:$PATH
export NEXUS_DEMO_RUNNER=$work/runner NEXUS_DEMO_EXP_DIR=$work/exp NEXUS_DEMO_INTERVAL=0
export NEXUS_DEMO_TIMEOUT=60 NEXUS_DEMO_READY_TIMEOUT=5

inc() {  # inc <name> <phase> <reason> <action>
  jq -n --arg n "$1" --arg p "$2" --arg r "$3" --arg a "$4" \
    '{metadata:{name:$n},status:{phase:$p,reason:$r,proposal:{actionId:$a,faultClass:"x",confidence:0.9},gate:{verdict:"allowed"},timestamps:{($p):"2026-11-22T09:14:10Z"}}}'
}
old=$(inc old-1 Resolved remediation_verified restart_workload)
seq_file() {  # seq_file <n> <incident json...>
  local n=$1; shift
  jq -n --argjson o "$old" '{items: ([$o] + [$ARGS.positional[] | fromjson])}' --args "$@" > "$work/seq.$n.json"
}
reset_case() {  # reset_case <level> [spec ready total]
  local s r t
  rm -f "${work:?}"/seq.*.json "${work:?}/n" "${work:?}/injected" "${work:?}/runner.log" \
    "${work:?}/kubectl.log" "${work:?}/unexpected.log" "${work:?}/nexusctl.log"
  jq -n --argjson o "$old" '{items:[$o]}' > "$work/before.json"
  echo "$1" > "$work/level"
  read -r s r t <<< "${2:-2 2 2}"
  jq -n --argjson s "$s" --argjson r "$r" --argjson t "$t" '{spec:{replicas:$s},status:{readyReplicas:$r,replicas:$t}}' > "$work/deploy.json"
}
run() {  # run <script> <stdin>  -> $out, $rc
  out=$(printf '%b' "$2" | bash "$root/scripts/demo/$1" 2>&1); rc=$?
}
no_stray() {  # every kubectl call named the cluster; no write except the dry-run scale
  [[ ! -s $work/unexpected.log ]] &&
    ! grep -Eq ' (apply|patch|delete|exec|rollout|label|annotate|create|replace|edit) ' "$work/kubectl.log" &&
    ! { grep -E ' scale ' "$work/kubectl.log" | grep -vq -- '--dry-run=server'; }
}
line_of() { grep -n -- "$1" "$work/runner.log" | head -1 | cut -d: -f1; }
yn() { if "$@"; then echo 1; fi; }

# ---- the happy paths: reset first, inject second, phases shown, reset hint printed
for sc in s1:S1:3:Resolved s3:S3:3:Escalated s5:S5:3:Escalated s6:S6:3:Dismissed l2:S1:2:Resolved; do
  IFS=: read -r script id lvl term <<< "$sc"
  reset_case 3
  seq_file 1 "$(inc new-1 Detected "" "")"
  seq_file 2 "$(inc new-1 Diagnosing "" "")"
  if [[ $script == l2 ]]; then
    seq_file 3 "$(inc new-1 AwaitingApproval "" restart_workload)"
    seq_file 4 "$(inc new-1 AwaitingApproval "" restart_workload)"
    seq_file 5 "$(inc new-1 Executing "" restart_workload)"
  fi
  seq_file 9 "$(inc new-1 "$term" remediation_verified restart_workload)"
  case $script in s3|s6|l2) stdin='yes\nyes\nyes\n' ;; *) stdin='yes\nyes\n' ;; esac
  run "$script.sh" "$stdin"
  check "$script: exits 0" "$(yn test "$rc" = 0)" "rc=$rc: $out"
  r=$(line_of "run reset --level $lvl "); i=$(line_of "run inject $id --context default --level $lvl ")
  check "$script: reset (level $lvl, baseline ref, git dir) comes before inject" \
    "$(yn bash -c '[[ -n $1 && -n $2 && $1 -lt $2 ]] && grep -q -- "--git-dir $3/exp --baseline-ref $4" "$3/runner.log"' _ "$r" "$i" "$work" "$base")" \
    "$(cat "$work/runner.log" 2> /dev/null)"
  check "$script: reset and inject ran with --execute" "$(yn test "$(grep -c -- '--execute' "$work/runner.log")" -ge 2)"
  check "$script: live view shows the phase changes with times" \
    "$(yn bash -c 'grep -Eq "[0-9]{2}:[0-9]{2}:[0-9]{2}  new-1: \(appeared\) -> Detected" <<< "$1" && grep -q "Detected -> Diagnosing" <<< "$1" && grep -q -- "-> $2" <<< "$1"' _ "$out" "$term")" "$out"
  check "$script: prints how to reset (level 3, same baseline)" \
    "$(yn bash -c 'grep -q "HOW TO RESET" <<< "$1" && grep -Eq "run reset --level 3 .*--baseline-ref $2" <<< "$1"' _ "$out" "$base")"
  check "$script: only the allowed kubectl calls" "$(yn no_stray)" "$(cat "$work/unexpected.log" 2> /dev/null)"
  case $script in
    s3) check "s3: patch and git dir passed, then the Argo refresh" \
          "$(yn bash -c 'grep -q -- "--s3-patch experiments/faults/s3-bad-rollout.patch" "$1/runner.log" && grep -q "run refresh --context default --execute" "$1/runner.log"' _ "$work")" ;;
    s6) check "s6: swarm resize 170 and the baseline swarm restarted by the reset" \
          "$(yn bash -c 'grep -q -- "--users 170 --spawn-rate 4" "$1/runner.log" && grep "run reset" "$1/runner.log" | grep -q -- "--locust"' _ "$work")"
        check "s6: ends with the denied moment (Kyverno K2)" \
          "$(yn bash -c 'grep -q "REFUSED BY: Kyverno admission, policy K2 nexus-scale-bounds" <<< "$1"' _ "$out")" "$out" ;;
    l2) check "l2: approval offered once at AwaitingApproval, as the approver" \
          "$(yn bash -c '[[ $(grep -c . "$1/nexusctl.log") == 1 ]] && grep -q -- "--as koussay-approver --as-group nexus-approvers approve new-1" "$1/nexusctl.log"' _ "$work")" \
          "$(cat "$work/nexusctl.log" 2> /dev/null)" ;;
    *) check "$script: no nexusctl call" "$(yn test ! -s "$work/nexusctl.log")" ;;
  esac
done

# ---- l2: approve from the phone (no nexusctl, keeps watching to terminal), or other input
reset_case 3
seq_file 1 "$(inc new-1 Detected "" "")"
seq_file 3 "$(inc new-1 AwaitingApproval "" restart_workload)"
seq_file 4 "$(inc new-1 AwaitingApproval "" restart_workload)"
seq_file 5 "$(inc new-1 Executing "" restart_workload)"
seq_file 9 "$(inc new-1 Resolved remediation_verified restart_workload)"
run l2.sh 'yes\nyes\nphone\n'
check "l2 phone: exits 0, no nexusctl call" "$(yn bash -c '[[ $1 == 0 && ! -s $2/nexusctl.log ]]' _ "$rc" "$work")" "rc=$rc: $out"
check "l2 phone: Telegram text, and phone is listed before yes" \
  "$(yn bash -c 'grep -q "Approve on Telegram now (@nexus_pfe_bot): the push has Approve / Reject / Explain" <<< "$1" && [[ $(grep -n "  phone  " <<< "$1" | head -1 | cut -d: -f1) -lt $(grep -n "  yes    " <<< "$1" | head -1 | cut -d: -f1) ]]' _ "$out")" "$out"
check "l2 phone: keeps watching to Resolved" \
  "$(yn bash -c 'grep -q "Executing -> Resolved" <<< "$1" && grep -q "HOW TO RESET" <<< "$1"' _ "$out")" "$out"
for answer in 'no\n' 'Phone\n' 'y\n' ''; do
  reset_case 3
  seq_file 3 "$(inc new-1 AwaitingApproval "" restart_workload)"
  run l2.sh "yes\nyes\n$answer"
  check "l2: approval answer '${answer%\\n}' stops, no nexusctl call" \
    "$(yn bash -c '[[ $1 != 0 && ! -s $2/nexusctl.log ]]' _ "$rc" "$work")" "rc=$rc"
done

# ---- confirmation is strict: only the typed word yes
for answer in 'no\n' 'y\n' 'YES\n' 'yes please\n' ''; do
  reset_case 3
  run s1.sh "$answer"
  check "s1: answer '${answer%\\n}' stops before any change" \
    "$(yn bash -c '[[ $1 != 0 ]] && ! grep -q -- "--execute" "$2/runner.log" 2> /dev/null' _ "$rc" "$work")" "rc=$rc"
done
reset_case 3
run s1.sh 'yes\nno\n'
check "s1: yes to the reset, no to the inject: nothing injected" \
  "$(yn bash -c '[[ $1 != 0 && ! -f $2/injected ]] && grep -q "run reset" "$2/runner.log" && ! grep -q "run inject" "$2/runner.log"' _ "$rc" "$work")"

# ---- the pre-check refuses before any runner call
reset_case 3 "1 2 2"; run s1.sh 'yes\nyes\n'
check "s1: sample-api not 2/2 -> refused, no runner call" \
  "$(yn bash -c '[[ $1 != 0 ]] && grep -q "FAIL  sample-api" <<< "$2" && [[ ! -s $3/runner.log ]]' _ "$rc" "$out" "$work")" "$out"
reset_case 3
jq -n --argjson o "$old" '{items:[$o, {metadata:{name:"open-1"},status:{phase:"Proposed"}}]}' > "$work/before.json"
run s1.sh 'yes\nyes\n'
check "s1: an open Incident -> refused, no runner call" \
  "$(yn bash -c '[[ $1 != 0 ]] && grep -q "FAIL  open Incidents" <<< "$2" && [[ ! -s $3/runner.log ]]' _ "$rc" "$out" "$work")" "$out"
reset_case 2; seq_file 9 "$(inc new-1 Resolved remediation_verified restart_workload)"
run s1.sh 'yes\nyes\n'
check "s1: a level label that differs is reported, the reset commits the level" \
  "$(yn bash -c 'grep -q "label is .2.; the reset below commits level 3" <<< "$1"' _ "$out")" "$out"

# ---- the denied moment: three outcomes
for mode in 'k2|0|REFUSED BY: Kyverno admission, policy K2 nexus-scale-bounds' 'rbac|0|REFUSED BY: RBAC' 'admit|1|NOT REFUSED'; do
  IFS='|' read -r m want text <<< "$mode"
  reset_case 3; export SCALE_MODE=$m
  run denied.sh 'yes\n'
  check "denied ($m): exit $want and prints '$text'" \
    "$(yn bash -c '[[ $1 == "$2" ]] && grep -q -- "$3" <<< "$4"' _ "$rc" "$want" "$text" "$out")" "rc=$rc $out"
  check "denied ($m): the Safety Gate rule is shown from the source" \
    "$(yn bash -c 'grep -q "outside \[1, 5\]" <<< "$1"' _ "$out")"
done
unset SCALE_MODE
reset_case 3; run denied.sh 'no\n'
check "denied: 'no' runs no scale" "$(yn bash -c '[[ $1 != 0 ]] && ! grep -q " scale " "$2/kubectl.log" 2> /dev/null' _ "$rc" "$work")"

exit "$failed"
