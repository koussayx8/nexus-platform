#!/usr/bin/env bash
# nexus-bot-secrets.sh — create the bot's token Secrets (ADR-040 decision 1, build step 8; ADR-041
# build step 7).
#
# A mutation script, like flight-recorder-secrets.sh: it does not source scripts/lib/readonly.sh.
# Run by the owner, after the platform Application has created namespace nexus-bot. No sudo.
#
#   ~/.nexus/telegram-bot.token    ->  nexus-bot/nexus-bot-telegram  key token  (required)
#   ~/.nexus/github-machine.token  ->  nexus-bot/nexus-bot-github    key token  (C8; only if the file exists)
#
# The tokens come from BotFather and from the machine account (ADR-041 token test, outcome B: the
# classic public_repo token), and only the owner has them, so unlike the database scripts this one
# never generates a file. It stops if the Telegram file is missing; for either file, if it is empty
# or readable by group or others (bot/README.md owner step 3). Without the GitHub file it creates
# no nexus-bot-github and says so: the bot then runs with C8 off.
# Both files are checked before any kubectl call.
# Each Secret is created only if absent (kubectl create --from-file, never apply), so a value is
# never in argv, in this script's output or in a diff. The output names the Secret, says "exists",
# "created" or "skipped", and gives the token's length in characters, never any part of its value.
#
# It refuses, without waiting, if namespace nexus-bot does not exist yet: the namespace belongs to
# the platform Application (platform/namespaces), and this script must not create it.
# To rotate: revoke the token (BotFather /revoke, or GitHub), delete the Secret, replace the file,
# run this again.
#
# Usage: scripts/nexus-bot-secrets.sh
# Exit codes: 0 success; 1 a step failed or refused to proceed; 2 argument error.

set -Eeuo pipefail

(( $# == 0 )) || { echo "nexus-bot-secrets: takes no arguments" >&2; exit 2; }

TELEGRAM_FILE=$HOME/.nexus/telegram-bot.token
GITHUB_FILE=$HOME/.nexus/github-machine.token
NAMESPACE=nexus-bot

fatal() { echo "nexus-bot-secrets: FATAL — $*" >&2; exit 1; }
trap 'echo "nexus-bot-secrets: FATAL — unchecked command failed at line $LINENO (exit $?)" >&2; exit 1' ERR

k() { kubectl --context default "$@"; }

# token_length <file> <its name under ~/>: the mode and emptiness checks; prints the length without
# line endings (the bot strips them when it reads the file). The value goes through a pipe only,
# never into a variable, argv or the output.
token_length() {
  local file=$1 name=$2 mode length
  mode=$(stat -c %a "$file") || fatal "reading the mode of ~/$name failed"
  (( (8#$mode & 8#077) == 0 )) || fatal "the file ~/$name has mode $mode; make it 0600 (chmod 600)"
  length=$(tr -d '\r\n' < "$file" | wc -c)
  length=${length//[[:space:]]/}
  (( length > 0 )) || fatal "the file ~/$name is empty"
  echo "$length"
}

# ensure_secret <secret> <file> <length>: create it from the file if absent; never replace it.
ensure_secret() {
  local secret=$1 file=$2 length=$3 out
  out=$(k -n "$NAMESPACE" get secret "$secret" --ignore-not-found -o name) \
    || fatal "checking whether Secret $NAMESPACE/$secret exists failed"
  if [[ -n $out ]]; then
    echo "exists: $NAMESPACE/$secret (left as it is)"
    return 0
  fi
  k -n "$NAMESPACE" create secret generic "$secret" --from-file=token="$file" >/dev/null \
    || fatal "creating Secret $NAMESPACE/$secret failed"
  echo "created: $NAMESPACE/$secret (key token, $length characters)"
}

[[ -f $TELEGRAM_FILE ]] || fatal "the file ~/.nexus/telegram-bot.token is missing (bot/README.md owner step 3)"
telegram_length=$(token_length "$TELEGRAM_FILE" .nexus/telegram-bot.token) || exit 1  # fatal has said why
github_length=
if [[ -e $GITHUB_FILE ]]; then
  [[ -f $GITHUB_FILE ]] || fatal "the path ~/.nexus/github-machine.token is not a regular file"
  github_length=$(token_length "$GITHUB_FILE" .nexus/github-machine.token) || exit 1
fi

ns=$(k get namespace "$NAMESPACE" --ignore-not-found -o name) \
  || fatal "checking whether namespace $NAMESPACE exists failed"
[[ -n $ns ]] || fatal "namespace $NAMESPACE does not exist yet: the platform Application creates it; sync that first"

ensure_secret nexus-bot-telegram "$TELEGRAM_FILE" "$telegram_length"
if [[ -n $github_length ]]; then
  ensure_secret nexus-bot-github "$GITHUB_FILE" "$github_length"
else
  echo "skipped: $NAMESPACE/nexus-bot-github (no ~/.nexus/github-machine.token; the bot runs with C8 off)"
fi
