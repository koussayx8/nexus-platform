#!/usr/bin/env bash
# s5-pin-guard.sh — the S5 pin guard (repo-checks step 9, ADR-025). Run from inside the repository.
#
#   s5-pin-guard.sh <pin SHA> <path>...
#
# Fails when a listed path is absent at the pin (a guard on a missing path pins nothing), or when
# any listed path differs between the pin and HEAD (changed, deleted or added).
#
# Exit codes: 0 every path exists at the pin and equals it at HEAD; 1 otherwise; 2 usage.
set -euo pipefail

pin=${1:?usage: s5-pin-guard.sh <pin SHA> <path>...}
shift
(( $# > 0 )) || { echo "usage: s5-pin-guard.sh <pin SHA> <path>..." >&2; exit 2; }

missing=0
for p in "$@"; do
  if ! git cat-file -e "$pin:$p" 2>/dev/null; then
    echo "::error::S5 pin guard: $p is absent at ${pin:0:8}; a listed path must exist at the pin"
    missing=1
  fi
done
(( missing == 0 )) || exit 1

if ! git diff --quiet "$pin" HEAD -- "$@"; then
  git diff --stat "$pin" HEAD -- "$@"
  echo "::error::the S5-pinned rule files differ from ${pin}; a change here means an S5 rerun (TASKS.md M1b exit)"
  exit 1
fi
echo "the $# pinned paths equal ${pin}"
