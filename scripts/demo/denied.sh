#!/usr/bin/env bash
# denied.sh — the "denied" moment on its own: scale sample-api to 40, refused, with the layer named.
# One typed "yes" for the server-side dry-run. See docs/demo/README.md.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_tools
denied_moment
