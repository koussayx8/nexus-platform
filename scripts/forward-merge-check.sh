#!/usr/bin/env bash
# forward-merge-check.sh — compares a forward-merge's tree with main's (CLAUDE.md rule 11, ADR-021).
#
# Usage: scripts/forward-merge-check.sh <merge> <main>   (two commits or trees of the current repo)
#
# PASS only if the sole difference between the two trees is the autonomy-level value of
# overlays/dev/namespace.yaml (the Experiment Runner owns that line on experiment/dev-state). Identical
# trees also pass. Any other difference fails and is listed: another path, a mode or type change, or
# any other line of that file. Read-only: it runs `git rev-parse`, `ls-tree`, `diff` and `show` only.
#
# Exit codes: 0 pass; 1 fail (the differences are printed); 2 usage or an unreadable ref.
set -uo pipefail

FILE=overlays/dev/namespace.yaml
LEVEL_RE='^([[:space:]]*nexus\.io/autonomy-level:[[:space:]]*)"?[0-9]+"?[[:space:]]*$'

[[ $# == 2 ]] || { echo "usage: forward-merge-check.sh <merge> <main>" >&2; exit 2; }
a=$(git rev-parse --verify --quiet "$1^{tree}") || { echo "forward-merge-check: cannot read $1" >&2; exit 2; }
b=$(git rev-parse --verify --quiet "$2^{tree}") || { echo "forward-merge-check: cannot read $2" >&2; exit 2; }

changed=$(git diff --name-status --no-renames "$a" "$b") || { echo "forward-merge-check: git diff failed" >&2; exit 2; }
if [[ -z $changed ]]; then
  echo "PASS trees are identical"
  exit 0
fi

others=$(grep -vP "^M\t\Q$FILE\E$" <<<"$changed" || true)
if [[ -n $others ]]; then
  echo "FAIL differences other than the autonomy level of $FILE:"
  sed 's/^/  /' <<<"$others"
  exit 1
fi

# Only $FILE differs in content: its mode must match, and its text must match once the level is masked.
ma=$(git ls-tree "$a" -- "$FILE" | cut -d' ' -f1); mb=$(git ls-tree "$b" -- "$FILE" | cut -d' ' -f1)
if [[ $ma != "$mb" ]]; then
  echo "FAIL $FILE changed mode ($ma vs $mb)"
  exit 1
fi
mask() { git show "$1:$FILE" | sed -E "s#$LEVEL_RE#\\1<LEVEL>#"; }   # '#': the key holds a '/'
count() { git show "$1:$FILE" | grep -cE "$LEVEL_RE"; }
if [[ $(count "$a") != 1 || $(count "$b") != 1 ]]; then
  echo "FAIL $FILE must hold exactly one autonomy-level line on each side"
  exit 1
fi
ta=$(mask "$a") && tb=$(mask "$b") || { echo "forward-merge-check: cannot read $FILE" >&2; exit 2; }
if [[ -z $ta || -z $tb ]]; then
  echo "forward-merge-check: $FILE is empty on one side" >&2; exit 2
fi
rest=$(diff <(printf '%s\n' "$ta") <(printf '%s\n' "$tb")) && rest=
if [[ -n $rest ]]; then
  echo "FAIL $FILE differs in more than the autonomy level:"
  sed 's/^/  /' <<<"$rest"
  exit 1
fi
echo "PASS the only difference is the autonomy level in $FILE"
