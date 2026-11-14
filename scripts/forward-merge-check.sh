#!/usr/bin/env bash
# forward-merge-check.sh — compares a forward-merge's tree with main's (CLAUDE.md rule 11, ADR-021).
#
# Usage: scripts/forward-merge-check.sh <merge> <main>   (two commits or trees of the current repo)
#
# PASS only if the two trees are identical: the local --no-ff merge commit into experiment/dev-state must
# carry exactly main's tree. Any difference fails and is listed (path, added, deleted or changed; a mode or
# type change shows as a changed path). Read-only: it runs `git rev-parse` and `git diff` only.
#
# Exit codes: 0 pass; 1 fail (the differences are printed); 2 usage or an unreadable ref.
set -uo pipefail

[[ $# == 2 ]] || { echo "usage: forward-merge-check.sh <merge> <main>" >&2; exit 2; }
a=$(git rev-parse --verify --quiet "$1^{tree}") || { echo "forward-merge-check: cannot read $1" >&2; exit 2; }
b=$(git rev-parse --verify --quiet "$2^{tree}") || { echo "forward-merge-check: cannot read $2" >&2; exit 2; }

if [[ $a == "$b" ]]; then
  echo "PASS trees are identical ($a)"
  exit 0
fi

changed=$(git diff --name-status --no-renames "$a" "$b") || { echo "forward-merge-check: git diff failed" >&2; exit 2; }
echo "FAIL the trees differ ($a vs $b):"
sed 's/^/  /' <<<"$changed"
exit 1
