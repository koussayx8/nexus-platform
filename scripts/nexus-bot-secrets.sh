#!/usr/bin/env bash
# nexus-bot-secrets.sh — create the Telegram bot's token Secret (ADR-040 decision 1, build step 8).
#
# A mutation script, like flight-recorder-secrets.sh: it does not source scripts/lib/readonly.sh.
# Run by the owner, after the platform Application has created namespace nexus-bot. No sudo.
#
#   ~/.nexus/telegram-bot.token  ->  nexus-bot/nexus-bot-telegram  key token  (the bot's mount)
#
# The token comes from BotFather and only the owner has it, so unlike the database scripts this one
# never generates the file: it stops if the file is missing, empty, or readable by group or others
# (bot/README.md owner step 3).
# The Secret is created only if absent (kubectl create --from-file, never apply), so the value is
# never in argv, in this script's output or in a diff. The output names the Secret, says "exists"
# or "created", and gives the token's length in characters, never any part of its value.
#
# It refuses, without waiting, if namespace nexus-bot does not exist yet: the namespace belongs to
# the platform Application (platform/namespaces), and this script must not create it.
# To rotate: /revoke in BotFather, delete the Secret, replace the file, run this again.
#
# Usage: scripts/nexus-bot-secrets.sh
# Exit codes: 0 success; 1 a step failed or refused to proceed; 2 argument error.

set -Eeuo pipefail

(( $# == 0 )) || { echo "nexus-bot-secrets: takes no arguments" >&2; exit 2; }

TOKEN_FILE=$HOME/.nexus/telegram-bot.token
NAMESPACE=nexus-bot
SECRET=nexus-bot-telegram

fatal() { echo "nexus-bot-secrets: FATAL — $*" >&2; exit 1; }
trap 'echo "nexus-bot-secrets: FATAL — unchecked command failed at line $LINENO (exit $?)" >&2; exit 1' ERR

k() { kubectl --context default "$@"; }

[[ -f $TOKEN_FILE ]] || fatal "the file ~/.nexus/telegram-bot.token is missing (bot/README.md owner step 3)"
mode=$(stat -c %a "$TOKEN_FILE") || fatal "reading the mode of ~/.nexus/telegram-bot.token failed"
(( (8#$mode & 8#077) == 0 )) || fatal "the file ~/.nexus/telegram-bot.token has mode $mode; make it 0600 (chmod 600)"
# Length without line endings (the bot strips them when it reads the file). The value goes through
# a pipe only, never into a variable, argv or the output.
length=$(tr -d '\r\n' < "$TOKEN_FILE" | wc -c)
length=${length//[[:space:]]/}
(( length > 0 )) || fatal "the file ~/.nexus/telegram-bot.token is empty"

ns=$(k get namespace "$NAMESPACE" --ignore-not-found -o name) \
  || fatal "checking whether namespace $NAMESPACE exists failed"
[[ -n $ns ]] || fatal "namespace $NAMESPACE does not exist yet: the platform Application creates it; sync that first"

out=$(k -n "$NAMESPACE" get secret "$SECRET" --ignore-not-found -o name) \
  || fatal "checking whether Secret $NAMESPACE/$SECRET exists failed"
if [[ -n $out ]]; then
  echo "exists: $NAMESPACE/$SECRET (left as it is)"
  exit 0
fi

k -n "$NAMESPACE" create secret generic "$SECRET" --from-file=token="$TOKEN_FILE" >/dev/null \
  || fatal "creating Secret $NAMESPACE/$SECRET failed"
echo "created: $NAMESPACE/$SECRET (key token, $length characters)"
