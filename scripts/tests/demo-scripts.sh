#!/usr/bin/env bash
# demo-scripts.sh — offline tests for scripts/demo/{s1,s3,s5,s6,l2,denied,prepare}.sh (Block 14 prep).
#
# Isolation: KUBECONFIG=/nonexistent and stubs for kubectl, curl, nexusctl and the runner first on
# PATH / in NEXUS_DEMO_RUNNER, so nothing here can reach a cluster, Locust, GitHub or Git's remote
# (the experiment worktree is a throwaway clone of a local bare repo). Each stub logs every call;
# a kubectl call without `--context default`, or any write other than the server-side dry-run
# scale and the Kill Switch halt / re-activation patches, fails the run. Safe to run anywhere.
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
    elif [[ -f "$work/halted-once" && -f "$work/during.json" ]]; then cat "$work/during.json"
    else cat "$work/before.json"; fi ;;
  *" get configmap nexus-killswitch "*) cat "$work/ks" ;;
  *" -n nexus-system patch configmap nexus-killswitch --type merge -p "*)
    st=\$(sed -nE 's/.*\{"data":\{"state":"([a-z]+)"\}\}.*/\1/p' <<< "\$*")
    [[ -n \$st ]] || { echo "stub kubectl: bad killswitch patch: \$*" >> "$work/unexpected.log"; exit 1; }
    echo "\$st" > "$work/ks"; [[ \$st != halted ]] || touch "$work/halted-once"
    echo "killswitch \$st" >> "$work/order.log" ;;
  *" get --raw /api/v1/namespaces/monitoring/services/http:observability-kube-prometh-prometheus:9090/proxy/api/v1/alerts "*)
    # prom-during.json while the Kill Switch is halted; with prom-during.calls = K, only for K reads
    f="$work/prom.json"
    if [[ -f "$work/halted-once" && -f "$work/prom-during.json" ]]; then
      f="$work/prom-during.json"
      if [[ -f "$work/prom-during.calls" ]]; then
        c=\$(( \$(cat "$work/prom-during.n" 2>/dev/null || echo 0) + 1 )); echo "\$c" > "$work/prom-during.n"
        (( c <= \$(cat "$work/prom-during.calls") )) || f="$work/prom.json"
      fi
    fi
    cat "\$f" ;;
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
[[ " \$* " != *" --execute "* ]] || echo "runner \$*" >> "$work/order.log"
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
export NEXUS_DEMO_SETTLE_QUIET=0 NEXUS_DEMO_SETTLE_MIN=0 NEXUS_DEMO_SETTLE_MAX=3

inc() {  # inc <name> <phase> <reason> <action> [alertname, default NexusLatencyAnomaly]
  jq -n --arg n "$1" --arg p "$2" --arg r "$3" --arg a "$4" --arg al "${5:-NexusLatencyAnomaly}" \
    '{metadata:{name:$n},spec:{source:{alertname:$al}},status:{phase:$p,reason:$r,proposal:{actionId:$a,faultClass:"x",confidence:0.9},gate:{verdict:"allowed"},timestamps:{($p):"2026-11-22T09:14:10Z"}}}'
}
old=$(inc old-1 Resolved remediation_verified restart_workload)
EXTRA='[]'  # Incidents present before the inject besides old-1 (e.g. raised during the settle)
seq_file() {  # seq_file <n> <incident json...>
  local n=$1; shift
  jq -n --argjson o "$old" --argjson x "$EXTRA" '{items: ([$o] + $x + [$ARGS.positional[] | fromjson])}' --args "$@" > "$work/seq.$n.json"
}
prom_alerts() {  # prom_alerts <file> [<alertname> <nexus_target> <pending|firing>]...: a Prometheus /api/v1/alerts body
  local f=$1 a=(); shift
  while (($#)); do
    a+=("$(jq -cn --arg a "$1" --arg t "$2" --arg s "$3" '{labels:{alertname:$a,nexus_target:$t},state:$s,activeAt:"2026-11-22T08:06:09Z"}')")
    shift 3
  done
  jq -n '{status:"success",data:{alerts:[$ARGS.positional[] | fromjson]}}' --args "${a[@]}" > "$f"
}
reset_case() {  # reset_case <level> [spec ready total]
  local s r t
  rm -f "${work:?}"/seq.*.json "${work:?}/n" "${work:?}/injected" "${work:?}/runner.log" \
    "${work:?}/kubectl.log" "${work:?}/unexpected.log" "${work:?}/nexusctl.log" "${work:?}/order.log" \
    "${work:?}/halted-once" "${work:?}/during.json" "${work:?}"/prom-during.*
  jq -n --argjson o "$old" '{items:[$o]}' > "$work/before.json"
  echo active > "$work/ks"; prom_alerts "$work/prom.json"; EXTRA='[]'
  echo "$1" > "$work/level"
  read -r s r t <<< "${2:-2 2 2}"
  jq -n --argjson s "$s" --argjson r "$r" --argjson t "$t" '{spec:{replicas:$s},status:{readyReplicas:$r,replicas:$t}}' > "$work/deploy.json"
}
run() {  # run <script> <stdin>  -> $out, $rc
  out=$(printf '%b' "$2" | bash "$root/scripts/demo/$1" 2>&1); rc=$?
}
KS_PATCH='--context default -n nexus-system patch configmap nexus-killswitch --type merge -p {"data":{"state":"'
no_stray() {  # every kubectl call named the cluster; no write except the dry-run scale and the Kill Switch
  [[ ! -s $work/unexpected.log ]] &&
    ! grep -vxF -e "${KS_PATCH}halted\"}}" -e "${KS_PATCH}active\"}}" "$work/kubectl.log" |
      grep -Eq ' (apply|patch|delete|exec|rollout|label|annotate|create|replace|edit) ' &&
    ! { grep -E ' scale ' "$work/kubectl.log" | grep -vq -- '--dry-run=server'; }
}
line_of() { grep -n -- "$1" "$work/runner.log" | head -1 | cut -d: -f1; }
yn() { if "$@"; then echo 1; fi; }

# ---- the happy paths: reset first, inject second, phases shown, reset hint printed
for sc in s1:S1:3:Resolved:NexusLatencyAnomaly s3:S3:3:Escalated:NexusRolloutStuck s5:S5:3:Escalated:NexusErrorRateAnomaly \
          s6:S6:3:Dismissed:NexusTrafficAnomaly l2:S1:2:Resolved:NexusLatencyAnomaly; do
  IFS=: read -r script id lvl term al <<< "$sc"
  reset_case 3
  seq_file 1 "$(inc new-1 Detected "" "" "$al")"
  seq_file 2 "$(inc new-1 Diagnosing "" "" "$al")"
  if [[ $script == l2 ]]; then
    seq_file 3 "$(inc new-1 AwaitingApproval "" restart_workload "$al")"
    seq_file 4 "$(inc new-1 AwaitingApproval "" restart_workload "$al")"
    seq_file 5 "$(inc new-1 Executing "" restart_workload "$al")"
  fi
  seq_file 9 "$(inc new-1 "$term" remediation_verified restart_workload "$al")"
  # typed yes: reset (with the halt), re-activation, inject, then S3's refresh / S6's denied / L2's approval
  case $script in s3|s6|l2) stdin='yes\nyes\nyes\nyes\n' ;; *) stdin='yes\nyes\nyes\n' ;; esac
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
  check "$script: shows the alert name and waits for $al" \
    "$(yn bash -c 'grep -q "  alert    $2\$" <<< "$1" && grep -q "Waiting for: .*$2" <<< "$1"' _ "$out" "$al")" "$out"
  check "$script: order is halt, reset, re-activate, inject" \
    "$(yn bash -c 'sed -n "1p;2s/ --level.*//p;3p;4s/ --context.*//p" "$1/order.log" | tr "\n" "|" | grep -qx "killswitch halted|runner run reset|killswitch active|runner run inject $2|"' _ "$work" "$id")" \
    "$(cat "$work/order.log" 2> /dev/null)"
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
run l2.sh 'yes\nyes\nyes\nphone\n'
check "l2 phone: exits 0, no nexusctl call" "$(yn bash -c '[[ $1 == 0 && ! -s $2/nexusctl.log ]]' _ "$rc" "$work")" "rc=$rc: $out"
check "l2 phone: Telegram text, and phone is listed before yes" \
  "$(yn bash -c 'grep -q "Approve on Telegram now (@nexus_pfe_bot): the push has Approve / Reject / Explain" <<< "$1" && [[ $(grep -n "  phone  " <<< "$1" | head -1 | cut -d: -f1) -lt $(grep -n "  yes    " <<< "$1" | head -1 | cut -d: -f1) ]]' _ "$out")" "$out"
check "l2 phone: keeps watching to Resolved" \
  "$(yn bash -c 'grep -q "Executing -> Resolved" <<< "$1" && grep -q "HOW TO RESET" <<< "$1"' _ "$out")" "$out"
for answer in 'no\n' 'Phone\n' 'y\n' ''; do
  reset_case 3
  seq_file 3 "$(inc new-1 AwaitingApproval "" restart_workload)"
  run l2.sh "yes\nyes\nyes\n$answer"
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
run s1.sh 'yes\nyes\nno\n'
check "s1: yes to the reset, no to the inject: nothing injected" \
  "$(yn bash -c '[[ $1 != 0 && ! -f $2/injected ]] && grep -q "run reset" "$2/runner.log" && ! grep -q "run inject" "$2/runner.log"' _ "$rc" "$work")"
reset_case 3
run s1.sh 'yes\nno\n'
check "s1: no to the re-activation: Kill Switch left halted, the command printed, nothing injected" \
  "$(yn bash -c '[[ $1 != 0 && ! -f $2/injected && $(cat "$2/ks") == halted ]] && grep -q "Kill Switch is still halted" <<< "$3" && grep -q "patch configmap nexus-killswitch .*active" <<< "$3"' _ "$rc" "$work" "$out")" "$out"


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


# ---- the reset step: one "yes" for the halt and the reset; the settle ends with the re-activation
reset_case 3; seq_file 9 "$(inc new-1 Resolved remediation_verified restart_workload)"
run s1.sh 'yes\nyes\nyes\n'
check "s1: the reset step lists the Kill Switch halt, then the reset, under one NEXT STEP" \
  "$(yn bash -c 'step=$(grep -A3 "NEXT STEP.*halt the Kill Switch" <<< "$1"); grep -q "^  command 1: kubectl --context default -n nexus-system patch configmap nexus-killswitch --type merge -p .*halted" <<< "$step" && grep -q "^  command 2: .*run reset --level 3 " <<< "$step"' _ "$out")" "$out"
check "s1: the halt and re-activation patches are the ones in manual-scenarios.md" \
  "$(yn bash -c 'grep -qxF -e "$2halted\"}}" "$1/kubectl.log" && grep -qxF -e "$2active\"}}" "$1/kubectl.log"' _ "$work" "$KS_PATCH")"
check "s1: settle, settle check, then the re-activation step, then the inject step" \
  "$(yn bash -c 'a=$(grep -n "^Settling from" <<< "$1" | cut -d: -f1); b=$(grep -n "^Settle check" <<< "$1" | cut -d: -f1); c=$(grep -n "NEXT STEP.*re-activate the Kill Switch" <<< "$1" | cut -d: -f1); d=$(grep -n "NEXT STEP.*inject S1" <<< "$1" | cut -d: -f1); [[ -n $a && -n $d && $a -lt $b && $b -lt $c && $c -lt $d ]]' _ "$out")" "$out"

# ---- Incidents raised during the settle (Kill Switch halted) never show in the live view
reset_case 3
EXTRA="[$(inc warm-1 Blocked killswitch_halted "" NexusLatencyAnomaly)]"
jq -n --argjson o "$old" --argjson x "$EXTRA" '{items: ([$o] + $x)}' > "$work/during.json"
seq_file 1 "$(inc new-1 Detected "" "")"
seq_file 9 "$(inc new-1 Resolved remediation_verified restart_workload)"
run s1.sh 'yes\nyes\nyes\n'
check "s1: an Incident raised during the settle is not in the live view or the result" \
  "$(yn bash -c '[[ $1 == 0 ]] && ! grep -q "warm-1" <<< "$2" && grep -q "new-1 \[NexusLatencyAnomaly\]: Resolved" <<< "$2"' _ "$rc" "$out")" "rc=$rc: $out"

# ---- the expected alert: an early terminal Incident with another alert does not settle the view
reset_case 3
w=$(inc warm-1 Dismissed no_action_required "" NexusLatencyAnomaly)
seq_file 1 "$w"; seq_file 2 "$w"; seq_file 3 "$w"
seq_file 4 "$w" "$(inc new-1 Detected "" "" NexusRolloutStuck)"
seq_file 9 "$w" "$(inc new-1 Escalated proposed_escalation escalate NexusRolloutStuck)"
run s3.sh 'yes\nyes\nyes\nyes\n'
check "s3: a terminal Incident from another alert does not settle; NexusRolloutStuck's does" \
  "$(yn bash -c '[[ $1 == 0 ]] && grep -q "new-1: (appeared) -> Detected" <<< "$2" && grep -q "new-1: Detected -> Escalated" <<< "$2" && grep -q "new-1 \[NexusRolloutStuck\]: Escalated" <<< "$2"' _ "$rc" "$out")" "rc=$rc: $out"
check "s3: the other Incident is shown, marked as not the expected alert" \
  "$(yn bash -c 'grep -q "  alert    NexusLatencyAnomaly   (not the expected alert)" <<< "$1" && grep -q "warm-1 \[NexusLatencyAnomaly, not the expected alert\]: Dismissed" <<< "$1"' _ "$out")" "$out"
check "s3: while waiting, says which alert and why it takes minutes" \
  "$(yn bash -c 'grep -q "Waiting for: NexusRolloutStuck or NexusCrashLooping. This takes minutes: Argo CD polls Git every 3 min" <<< "$1" && grep -q "NexusRolloutStuck needs its for: 3m" <<< "$1"' _ "$out")" "$out"
reset_case 3
seq_file 1 "$w"
export NEXUS_DEMO_TIMEOUT=1
run s3.sh 'yes\nyes\nyes\nyes\n'
export NEXUS_DEMO_TIMEOUT=60
check "s3: only another alert's Incident, terminal: the view times out (exit 3), it does not settle" \
  "$(yn bash -c '[[ $1 == 3 ]] && grep -q "stopped after 1s with no terminal Incident for NexusRolloutStuck NexusCrashLooping" <<< "$2"' _ "$rc" "$out")" "rc=$rc: $out"

# ---- NEXUS alerts (Prometheus, pending or firing): the pre-check
reset_case 3
prom_alerts "$work/prom.json" NexusLatencyAnomaly nexus-dev/sample-api firing
run s1.sh 'yes\nyes\nyes\n'
check "s1: a NEXUS alert firing for nexus-dev fails the pre-check, no runner call" \
  "$(yn bash -c '[[ $1 != 0 ]] && grep -Eq "FAIL +NEXUS alerts +for nexus-dev: NexusLatencyAnomaly \(firing\)" <<< "$2" && [[ ! -s $3/runner.log ]]' _ "$rc" "$out" "$work")" "$out"
reset_case 3
prom_alerts "$work/prom.json" NexusTrafficAnomaly nexus-dev/sample-api pending
run s1.sh 'yes\nyes\nyes\n'
check "s1: a NEXUS alert pending for nexus-dev fails the pre-check, with its state" \
  "$(yn bash -c '[[ $1 != 0 ]] && grep -Eq "FAIL +NEXUS alerts +for nexus-dev: NexusTrafficAnomaly \(pending\)" <<< "$2" && [[ ! -s $3/runner.log ]]' _ "$rc" "$out" "$work")" "$out"
reset_case 3; seq_file 9 "$(inc new-1 Resolved remediation_verified restart_workload)"
prom_alerts "$work/prom.json" NexusLatencyAnomaly nexus-prod/sample-api firing Watchdog '' firing
run s1.sh 'yes\nyes\nyes\n'
check "s1: an alert for nexus-prod, or a non-NEXUS alert, does not fail the pre-check" \
  "$(yn bash -c '[[ $1 == 0 ]] && grep -Eq "ok +NEXUS alerts +none pending or firing for nexus-dev" <<< "$2"' _ "$rc" "$out")" "rc=$rc: $out"

# ---- the settle: a quiet window, polled
reset_case 3
prom_alerts "$work/prom-during.json" NexusCpuAnomaly nexus-dev/sample-api pending
run s1.sh 'yes\nyes\nyes\n'
check "s1: a pending alert keeps the quiet window from passing; at the max: look commands and re-activation command" \
  "$(yn bash -c '[[ $1 != 0 && ! -f $3/injected && $(cat "$3/ks") == halted ]] && grep -q "did not settle in 3s (NEXUS_DEMO_SETTLE_MAX): now NexusCpuAnomaly (pending)" <<< "$2" && grep -q "Look: kubectl --context default get --raw /api/v1/namespaces/monitoring/services/http:observability-kube-prometh-prometheus:9090/proxy/api/v1/alerts" <<< "$2" && grep -q "Look: kubectl --context default -n nexus-system get incidents" <<< "$2" && grep -q "Kill Switch is still halted" <<< "$2" && grep -q "patch configmap nexus-killswitch .*active" <<< "$2" && ! grep -q "NEXT STEP.*re-activate the Kill Switch" <<< "$2"' _ "$rc" "$out" "$work")" "$out"
check "s1: the settle that stopped made no write after the halt and reset" \
  "$([[ $(grep -c . "$work/order.log") == 2 ]] && no_stray && echo 1)" "$(cat "$work/order.log" 2> /dev/null)"
check "s1: the settle shows a live line: elapsed, quiet for, the alerts seen" \
  "$(yn bash -c 'grep -Eq "^settle  elapsed [0-9]+s  quiet for 0s  now: NexusCpuAnomaly \(pending\)  open Incidents: none  alerts seen: NexusCpuAnomaly" <<< "$1"' _ "$out")" "$out"
reset_case 3; seq_file 9 "$(inc new-1 Resolved remediation_verified restart_workload)"
prom_alerts "$work/prom-during.json" NexusLatencyAnomaly nexus-dev/sample-api firing
echo 3 > "$work/prom-during.calls"
export NEXUS_DEMO_SETTLE_QUIET=1 NEXUS_DEMO_SETTLE_MAX=20
run s1.sh 'yes\nyes\nyes\n'
export NEXUS_DEMO_SETTLE_QUIET=0 NEXUS_DEMO_SETTLE_MAX=3
check "s1: an alert that resolves passes after the quiet window, then the re-activation and the inject" \
  "$(yn bash -c '[[ $1 == 0 && -f $3/injected ]] && a=$(grep -n "now: NexusLatencyAnomaly (firing)" <<< "$2" | tail -1 | cut -d: -f1) && b=$(grep -n "^Settle check" <<< "$2" | cut -d: -f1) && [[ -n $a && -n $b && $a -lt $b ]] && grep -Eq "ok +NEXUS alerts +none pending or firing for [1-9][0-9]*s" <<< "$2" && grep -q "alerts seen: NexusLatencyAnomaly" <<< "$2" && grep -q "NEXT STEP.*re-activate the Kill Switch" <<< "$2"' _ "$rc" "$out" "$work")" "rc=$rc: $out"
reset_case 3
jq -n --argjson o "$old" '{items: [$o, {metadata:{name:"warm-2"},status:{phase:"Diagnosing"}}]}' > "$work/during.json"
run s1.sh 'yes\nyes\nyes\n'
check "s1: an Incident still open stops the settle at the max, before the re-activation" \
  "$(yn bash -c '[[ $1 != 0 && ! -f $3/injected && $(cat "$3/ks") == halted ]] && grep -q "did not settle in 3s.*open Incidents: warm-2" <<< "$2" && grep -q "Look: kubectl --context default -n nexus-system get incidents" <<< "$2"' _ "$rc" "$out" "$work")" "$out"

# ---- prepare.sh, then the scenario with NEXUS_DEMO_SKIP_RESET=1
reset_case 2
out=$(printf 'yes\nyes\n' | bash "$root/scripts/demo/prepare.sh" 2 2>&1); rc=$?
check "prepare: halt, reset, settle, re-activate, then stops: ready for the inject, no inject" \
  "$(yn bash -c '[[ $1 == 0 && ! -f $3/injected ]] && [[ $(sed "2s/ --level.*//" "$3/order.log" | tr "\n" "|") == "killswitch halted|runner run reset|killswitch active|" ]] && grep -q "run reset --level 2 " "$3/runner.log" && grep -q "^Ready for the inject" <<< "$2" && grep -q "^Settle check" <<< "$2"' _ "$rc" "$out" "$work")" "rc=$rc: $out"
check "prepare: only the allowed kubectl calls" "$(yn no_stray)"
reset_case 3
out=$(bash "$root/scripts/demo/prepare.sh" < /dev/null 2>&1); rc=$?
check "prepare: no level -> usage, no runner call" \
  "$(yn bash -c '[[ $1 != 0 && ! -s $3/runner.log ]] && grep -q "usage: scripts/demo/prepare.sh <level 0-3>" <<< "$2"' _ "$rc" "$out" "$work")" "$out"
reset_case 3; seq_file 1 "$(inc new-1 Detected "" "")"; seq_file 9 "$(inc new-1 Resolved remediation_verified restart_workload)"
export NEXUS_DEMO_SKIP_RESET=1
run s1.sh 'yes\n'
check "s1 skip-reset: no runner reset, no Kill Switch patch; the inject runs with the current head as baseline" \
  "$(yn bash -c '[[ $1 == 0 ]] && ! grep -q "run reset" "$3/runner.log" && grep -q "run inject S1" "$3/runner.log" && ! grep -q "patch configmap" "$3/kubectl.log" && grep -q "Baseline recorded: experiment/dev-state = $4 (the current head" <<< "$2" && grep -Eq "run reset --level 3 .*--baseline-ref $4" <<< "$2"' _ "$rc" "$out" "$work" "$base")" "rc=$rc: $out"
reset_case 3
prom_alerts "$work/prom.json" NexusLatencyAnomaly nexus-dev/sample-api pending
run s1.sh 'yes\n'
check "s1 skip-reset: a pending alert fails the strict pre-check, no runner call" \
  "$(yn bash -c '[[ $1 != 0 && ! -s $3/runner.log ]] && grep -Eq "FAIL +NEXUS alerts +for nexus-dev: NexusLatencyAnomaly \(pending\)" <<< "$2" && grep -q "run scripts/demo/prepare.sh 3 first" <<< "$2"' _ "$rc" "$out" "$work")" "$out"
reset_case 2
run s1.sh 'yes\n'
check "s1 skip-reset: a level that differs fails the strict pre-check" \
  "$(yn bash -c '[[ $1 != 0 && ! -s $3/runner.log ]] && grep -Eq "FAIL +autonomy level +label is .2., want 3" <<< "$2"' _ "$rc" "$out" "$work")" "$out"
unset NEXUS_DEMO_SKIP_RESET

exit "$failed"
