#!/usr/bin/env bash
# alertmanager-telegram-secret.sh — offline tests for the Alertmanager Telegram Secret (ADR-040
# decision 12): bootstrap.sh's step_c_alertmanager_telegram (create or update from two owner files,
# refuse if either is missing, --plan runs nothing) and verify-state.sh's M20 (2 keys, Alertmanager
# Ready). Run by .github/scripts/repo-checks.sh. Needs bash, jq, base64 and coreutils.
#
# Isolation: KUBECONFIG=/nonexistent, a throwaway HOME, and a stub kubectl first on PATH, so nothing
# here can reach a real cluster or read the owner's files. The stub logs its argv and, for `apply`,
# its stdin. The functions are extracted from the two scripts and run as they are.
#
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$here/../..
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

export KUBECONFIG=/nonexistent
TOKEN=offline-test-value-not-a-bot-token-0123456789   # shaped like no real credential
CHAT=4242424242
mkdir -p "$work/bin"
cat > "$work/bin/kubectl" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$work/kubectl.log"
fx=$work/fx
case " \$* " in
  *" create secret generic "*"--dry-run=client"*)
    # as kubectl does: each --from-file=<key>=<path> becomes data.<key>, base64 of the file
    [[ -n \${STUB_FAIL_CREATE:-} ]] && { echo "error: stub create failure" >&2; exit 1; }
    printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: alertmanager-telegram\n  namespace: monitoring\ndata:\n'
    for a in "\$@"; do
      case \$a in --from-file=*) kv=\${a#--from-file=}; [[ -f \${kv#*=} ]] || { echo "error: no file \${kv#*=}" >&2; exit 1; }
        printf '  %s: %s\n' "\${kv%%=*}" "\$(base64 -w0 < "\${kv#*=}")" ;; esac
    done ;;
  *" apply "*)
    cat >> "$work/apply.stdin"
    [[ -n \${STUB_FAIL_APPLY:-} ]] && { echo "Error from server: stub apply failure" >&2; exit 1; }
    echo "secret/alertmanager-telegram serverside-applied" ;;
  *" get secrets -n monitoring alertmanager-telegram --no-headers "*) [[ -e \$fx/secret.txt ]] && cat "\$fx/secret.txt" ;;
  *" get pods -n monitoring -l app.kubernetes.io/name=alertmanager -o json "*) cat "\$fx/pods.json" ;;
  *) echo "stub kubectl: unexpected call: \$*" >> "$work/unexpected.log"; exit 1 ;;
esac
STUB
chmod +x "$work/bin/kubectl"
export PATH=$work/bin:$PATH

sed -n '/^step() {/,/^}/p; /^fatal() {/p; /^owner_file_ok() {/,/^}/p; /^step_c_alertmanager_telegram() {/,/^}/p' \
  "$root/scripts/bootstrap.sh" > "$work/boot.sh"
sed -n '/^AM_TELEGRAM_SECRET=/p; /^v_alertmanager_telegram() {/,/^}/p' "$root/scripts/verify-state.sh" > "$work/verify.sh"
for f in step fatal owner_file_ok step_c_alertmanager_telegram; do
  grep -q "^$f() {" "$work/boot.sh" || { echo "FAIL could not extract $f"; exit 1; }
done
grep -q '^v_alertmanager_telegram() {' "$work/verify.sh" || { echo "FAIL could not extract v_alertmanager_telegram"; exit 1; }

failed=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; failed=1; }

# boot <plan 0|1> [token|-] [chat|-] [mode]: a fresh HOME with the files given ("-" = absent); runs the step.
boot() {
  local plan=$1 tok=${2--} chat=${3--} mode=${4:-600} home=$work/home
  rm -rf "$home" "$work/kubectl.log" "$work/apply.stdin"; mkdir -p "$home/.nexus"
  [[ $tok != - ]] && { printf '%s' "$tok" > "$home/.nexus/telegram-bot.token"; chmod "$mode" "$home/.nexus/telegram-bot.token"; }
  [[ $chat != - ]] && { printf '%s\n' "$chat" > "$home/.nexus/telegram-chat-id"; chmod "$mode" "$home/.nexus/telegram-chat-id"; }
  set +e
  # shellcheck disable=SC2034  # read by the extracted functions
  ( set -Eeuo pipefail; PLAN=$plan; TELEGRAM_TOKEN_FILE=$home/.nexus/telegram-bot.token
    TELEGRAM_CHAT_ID_FILE=$home/.nexus/telegram-chat-id
    # shellcheck source=/dev/null
    source "$work/boot.sh"; step_c_alertmanager_telegram ) > "$work/out" 2>&1
  rc=$?
  set -e
}
calls() { [[ -e $work/kubectl.log ]] && wc -l < "$work/kubectl.log" || echo 0; }
no_value_in_output_or_argv() {
  ! grep -qF -e "$TOKEN" -e "$CHAT" "$work/out" && { [[ ! -e $work/kubectl.log ]] || ! grep -qF -e "$TOKEN" -e "$CHAT" "$work/kubectl.log"; }
}

boot 0 "$TOKEN" "$CHAT"
if (( rc == 0 )) && [[ $(calls) == 2 ]] \
  && grep -q '^create secret generic alertmanager-telegram -n monitoring --from-file=token=.*/telegram-bot.token --from-file=chat_id=.*/telegram-chat-id --dry-run=client -o yaml$' "$work/kubectl.log" \
  && grep -q '^apply --server-side --force-conflicts --field-manager=nexus-bootstrap -f -$' "$work/kubectl.log" \
  && grep -qF "token: $(printf '%s' "$TOKEN" | base64 -w0)" "$work/apply.stdin" \
  && grep -qF "chat_id: $(printf '%s\n' "$CHAT" | base64 -w0)" "$work/apply.stdin" \
  && no_value_in_output_or_argv && grep -q 'applied (keys token, chat_id)' "$work/out"; then
  pass "both-files: create --dry-run=client piped into a server-side apply, values only on the pipe"
else fail "both-files (rc=$rc)"; sed 's/^/  | /' "$work/out" "$work/kubectl.log" 2>/dev/null; fi

boot 0 "$TOKEN" "$CHAT"; first=$(cat "$work/apply.stdin"); boot 0 "$TOKEN" "$CHAT"
if (( rc == 0 )) && [[ $first == "$(cat "$work/apply.stdin")" ]]; then pass "re-run: the same manifest again"; else fail "re-run"; fi

boot 0 - "$CHAT"
if (( rc == 1 )) && [[ $(calls) == 0 ]] && grep -q 'telegram-bot.token is missing; the alertmanager-telegram Secret is not created' "$work/out"; then
  pass "token-missing: stops before any kubectl call"; else fail "token-missing (rc=$rc)"; sed 's/^/  | /' "$work/out"; fi

boot 0 "$TOKEN" -
if (( rc == 1 )) && [[ $(calls) == 0 ]] && grep -q 'telegram-chat-id is missing' "$work/out" && no_value_in_output_or_argv; then
  pass "chat-id-missing: stops before any kubectl call"; else fail "chat-id-missing (rc=$rc)"; sed 's/^/  | /' "$work/out"; fi

boot 0 "$TOKEN" "$CHAT" 644
if (( rc == 1 )) && [[ $(calls) == 0 ]] && grep -q 'has mode 644' "$work/out"; then pass "mode-644: refused"; else fail "mode-644 (rc=$rc)"; fi

boot 0 "" "$CHAT"
if (( rc == 1 )) && [[ $(calls) == 0 ]] && grep -q 'telegram-bot.token is empty' "$work/out"; then pass "empty-token: refused"; else fail "empty-token (rc=$rc)"; fi

boot 0 "$TOKEN" "@someone"
if (( rc == 1 )) && [[ $(calls) == 0 ]] && grep -q 'not a numeric Telegram user ID' "$work/out" && ! grep -qF '@someone' "$work/out"; then
  pass "chat-id-not-numeric: refused, value not echoed"; else fail "chat-id-not-numeric (rc=$rc)"; sed 's/^/  | /' "$work/out"; fi

export STUB_FAIL_APPLY=1; boot 0 "$TOKEN" "$CHAT"; unset STUB_FAIL_APPLY
if (( rc == 1 )) && grep -q 'creating or updating the alertmanager-telegram Secret failed' "$work/out" && no_value_in_output_or_argv; then
  pass "apply-fails: fatal, no value printed"; else fail "apply-fails (rc=$rc)"; sed 's/^/  | /' "$work/out"; fi

export STUB_FAIL_CREATE=1; boot 0 "$TOKEN" "$CHAT"; unset STUB_FAIL_CREATE
if (( rc == 1 )) && grep -q 'creating or updating the alertmanager-telegram Secret failed' "$work/out"; then
  pass "create-fails: pipefail makes it fatal"; else fail "create-fails (rc=$rc)"; sed 's/^/  | /' "$work/out"; fi

boot 1 - -
if (( rc == 0 )) && [[ $(calls) == 0 ]] \
  && grep -qF '$ test -f ' "$work/out" \
  && grep -qF 'kubectl create secret generic alertmanager-telegram -n monitoring --from-file=token=' "$work/out" \
  && grep -qF '| kubectl apply --server-side --force-conflicts --field-manager=nexus-bootstrap -f - >/dev/null' "$work/out"; then
  pass "plan: prints the check and the pipeline, runs nothing, needs no file"; else fail "plan (rc=$rc)"; sed 's/^/  | /' "$work/out"; fi

# bootstrap.sh calls the step inside step c, after grafana-admin.
if sed -n '/^step_c_monitoring() {/,/^}/p' "$root/scripts/bootstrap.sh" | tail -3 | grep -q '^  step_c_alertmanager_telegram$'; then
  pass "wired: step c ends with the Telegram Secret"; else fail "wired"; fi

# ---- M20 ----------------------------------------------------------------------------------------
mkdir -p "$work/fx"
pods() {   # pods <ready...>: one Alertmanager pod per argument
  local items='' r i=0
  for r in "$@"; do items+="{\"metadata\":{\"name\":\"am-$i\"},\"status\":{\"phase\":\"Running\",\"containerStatuses\":[{\"ready\":$r},{\"ready\":true}]}},"; i=$((i+1)); done
  printf '{"items":[%s]}' "${items%,}" > "$work/fx/pods.json"
}
m20() {   # m20 <want rc> <label>
  local got
  : > "$work/kubectl.log"
  set +e
  ( OUT=$work/out-lib; mkdir -p "$OUT"
    # shellcheck source=../lib/readonly.sh
    source "$root/scripts/lib/readonly.sh"
    # shellcheck source=/dev/null
    source "$work/verify.sh"; v_alertmanager_telegram ) > "$work/m20" 2>&1
  got=$?
  set -e
  if (( got == $1 )) && ! grep -q -- ' -o \(yaml\|json\|jsonpath\)' <(grep 'secrets' "$work/kubectl.log"); then pass "M20 $2"; else
    fail "M20 $2 (rc=$got, want $1)"; sed 's/^/  | /' "$work/m20" "$work/kubectl.log"; fi
}
echo 'alertmanager-telegram   Opaque   2      5d' > "$work/fx/secret.txt"; pods true;  m20 0 "pass: 2 keys, Ready"
echo 'alertmanager-telegram   Opaque   1      5d' > "$work/fx/secret.txt"; pods true;  m20 1 "fail: 1 key (token only)"
rm -f "$work/fx/secret.txt";                                                pods true;  m20 1 "fail: no Secret"
echo 'alertmanager-telegram   Opaque   2      5d' > "$work/fx/secret.txt"; pods false; m20 1 "fail: Alertmanager not Ready"
printf '{"items":[]}' > "$work/fx/pods.json";                                            m20 1 "fail: no Alertmanager pod"
grep -q 'get secrets -n monitoring alertmanager-telegram --no-headers' "$work/kubectl.log" \
  && pass "M20 lists the Secret through secret_names (default columns)" || fail "M20 Secret listing"

if [[ -s $work/unexpected.log ]]; then echo "FAIL unexpected kubectl calls:"; cat "$work/unexpected.log"; failed=1; fi
exit $failed
