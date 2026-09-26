# readonly.sh — shared read-only helpers for NEXUS operational scripts.
#
# Sourced by scripts/capture-state.sh and scripts/verify-state.sh. The caller must set $OUT
# (a writable directory) before calling any function here: guard_fail, sudo_needed, check and
# leak_check all write into it.
#
# Guarantees carried by these wrappers (spec §25, ADR-010/ADR-012):
#   - kubectl (k) is limited to get, describe, logs, version, api-resources, auth can-i;
#     Secrets are never read directly, only listed by name via secret_names.
#   - helm (h) is limited to list, status, get values. git (g) is limited to read-only
#     subcommands and never fetches. gh (gh_ro) is limited to `run list` and GET `api` calls.
#   - Every check's output passes through redact() before it is written to disk.

have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# Redaction. Token prefixes are written with character classes so that this
# file never contains the literal strings the leak self-check looks for.
# ---------------------------------------------------------------------------
REDACT_PY=$(cat <<'PY'
import io, re, sys
sys.stdin = io.TextIOWrapper(sys.stdin.buffer, errors="replace")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, errors="replace", line_buffering=False)
R = "<REDACTED>"
RULES = [
    (re.compile(r"-----BEGIN (?:[A-Z ]*PRIVAT[E] KEY|PGP PRIVAT[E] KEY BLOCK)-----.*?-----END (?:[A-Z ]*PRIVAT[E] KEY|PGP PRIVAT[E] KEY BLOCK)-----"), "<REDACTED-PEM>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]*"), R),
    (re.compile(r"github_pa[t]_[A-Za-z0-9_]*"), R),
    (re.compile(r"gs[k]_[A-Za-z0-9_]*"), R),
    (re.compile(r"sk-an[t]-[A-Za-z0-9_\-]*"), R),
    (re.compile(r"do[por]_v1_[A-Za-z0-9_]*"), R),
    (re.compile(r"AKI[A][0-9A-Z]*"), R),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "<REDACTED-JWT>"),
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s:]+:[^/@\s]+@"), r"\1" + R + "@"),
    (re.compile(r"""(--(?:agent-)?token|--datastore-endpoint)(["']?(?:=|\s+|\s*,\s*)["']?)([^\s"',\]]+)"""), r"\1\2" + R),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + R),
]
KEY = re.compile(r"""(?ix)
    (?P<k>["']?(?P<name>[\w.\-]*(?:passw(?:or)?d|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|credentials?)[\w.\-]*)["']?)
    (?P<sep>\s*[:=]\s*)
    (?P<v>"[^"]*"|'[^']*'|[^\s,;}\]]+)""")
SAFE_NAME = re.compile(r"(?i)(name|names|ref|file|path|dir|namespace|mount|selector)$|^automountserviceaccounttoken$")
SAFE_VAL = re.compile(r"(?i)^([\"']?)(true|false|null|~|<redacted[^>]*>|)\1$")
def keyrepl(m):
    if SAFE_NAME.search(m.group("name")) or SAFE_VAL.match(m.group("v")):
        return m.group(0)
    return m.group("k") + m.group("sep") + R
PEM_BEGIN = re.compile(r"-----BEGIN (?:[A-Z ]*PRIVAT[E] KEY|PGP PRIVAT[E] KEY BLOCK)-----")
PEM_END = re.compile(r"-----END (?:[A-Z ]*PRIVAT[E] KEY|PGP PRIVAT[E] KEY BLOCK)-----")
in_pem = False
for line in sys.stdin:
    if in_pem:
        if PEM_END.search(line):
            in_pem = False
        continue
    for rx, rep in RULES:
        line = rx.sub(rep, line)
    if PEM_BEGIN.search(line):
        sys.stdout.write("<REDACTED-PEM-BLOCK>\n")
        in_pem = not PEM_END.search(line)
        continue
    sys.stdout.write(KEY.sub(keyrepl, line))
sys.stdout.flush()
PY
)
redact() { python3 -c "$REDACT_PY"; }

# ---------------------------------------------------------------------------
# Read-only guards. A violation leaves a marker and aborts the whole run.
# ---------------------------------------------------------------------------
guard_fail() {
  : > "$OUT/.guard-violation"
  echo "GUARD VIOLATION: $*" >&2
  exit 2
}

k() {
  local a re='(^|,)secrets?($|[.,/])'
  case ${1:-} in
    get|describe|logs|version|api-resources) ;;
    auth) [[ ${2:-} == can-i ]] || guard_fail "kubectl $*" ;;
    *) guard_fail "kubectl $*" ;;
  esac
  for a in "$@"; do
    a=${a,,}
    if [[ $a =~ $re || $a == */secrets* ]]; then guard_fail "kubectl $* (Secrets are listed only through secret_names)"; fi
  done
  timeout 45 kubectl --request-timeout=20s "$@"
}

secret_names() {   # default columns only: namespace, name, type, data count, age
  local a
  for a in "$@"; do
    case $a in -o*|--output*|--show-labels|-w|--watch) guard_fail "secret_names $*" ;; esac
  done
  timeout 45 kubectl --request-timeout=20s get secrets "$@"
}

h() {
  case "${1:-} ${2:-}" in
    "list "*|"status "*|"get values") ;;
    *) guard_fail "helm $*" ;;
  esac
  timeout 30 helm "$@"
}

g() {
  local sub=${1:-} a b
  case $sub in
    rev-parse|rev-list|log|status|diff|ls-files|ls-tree|ls-remote|show|for-each-ref|grep|--version) ;;
    branch)
      for a in "${@:2}"; do
        case $a in -a|-r|-v|-vv|--no-color|--show-current|--list) ;; *) guard_fail "git $*" ;; esac
      done ;;
    remote) [[ ${2:-} == -v && $# -eq 2 ]] || guard_fail "git $*" ;;
    stash) [[ ${2:-} == list && $# -eq 2 ]] || guard_fail "git $*" ;;
    *) guard_fail "git $*" ;;
  esac
  for a in "${@:2}"; do
    [[ $a == --output* ]] && guard_fail "git $* (writes a file)"
    if [[ $a == *secrets/* && $a != ':(exclude'* && $a != ':!'* ]]; then
      case $sub in
        ls-files) ;;
        log)
          for b in "${@:2}"; do
            case $b in -p|-u|--patch|--stat|--numstat|-G*|-S*|--follow) guard_fail "git $* (content of a secrets/ path)" ;; esac
          done ;;
        *) guard_fail "git $* (content of a secrets/ path)" ;;
      esac
    fi
  done
  timeout 30 git "$@"
}

# gh_ro: allows only `gh run list ...` and GET `gh api ...`. Rejects any flag, short or long,
# that can add a request parameter or switch the HTTP method away from GET, then always
# appends --method GET itself to every `api` call it lets through (round 2/3 point 3/8).
# A short-option cluster like -iXPOST or -if is rejected because it CONTAINS a dangerous
# letter, not just because it begins with one.
gh_ro() {
  case ${1:-} in
    run)
      [[ ${2:-} == list ]] || guard_fail "gh $*"
      timeout 30 gh "$@"
      ;;
    api)
      local a lead
      for a in "${@:2}"; do
        case $a in
          --field*|--raw-field*|--input*|--method*) guard_fail "gh $* (method/body flag)" ;;
          -[A-Za-z]*)
            lead=${a%%=*}
            lead=${lead#-}
            if [[ $lead == *f* || $lead == *F* || $lead == *X* ]]; then
              guard_fail "gh $* (short flag cluster carries f/F/X)"
            fi
            ;;
        esac
      done
      timeout 30 gh api --method GET "${@:2}"
      ;;
    *) guard_fail "gh $*" ;;
  esac
}

# ---------------------------------------------------------------------------
# Check plumbing
# ---------------------------------------------------------------------------
show() { printf '\n$ %s\n' "$*"; }

run() {
  local disp=("$@") rc
  case ${disp[0]} in
    k) disp[0]=kubectl ;; h) disp[0]=helm ;; g) disp[0]=git ;; gh_ro) disp[0]=gh ;;
    secret_names) disp=(kubectl get secrets "${disp[@]:1}") ;;
  esac
  show "${disp[*]}"
  "$@"
  rc=$?
  (( rc == 0 )) || printf '[exit %s]\n' "$rc"
  return 0
}

need_jq() { have jq && return 0; echo "jq not installed: this part of the check is skipped"; return 1; }

sudo_needed() {
  printf '%s\n    # %s\n' "$1" "$2" >> "$OUT/SUDO-REQUIRED.txt"
  printf 'SKIPPED (needs sudo; recorded in SUDO-REQUIRED.txt): %s\n' "$1"
}

check() {   # check <id> <title> <function> [args...]
  local id=$1 title=$2 file rc fails
  shift 2
  file=$OUT/$id.txt
  printf '# %s — %s\n# captured %s by scripts/capture-state.sh (%s); commands are shown after "$"\n' \
    "$id" "$title" "$(date -u +%FT%TZ)" "$1" > "$file"
  ( "$@" ) 2>&1 | redact >> "$file"
  local ps=("${PIPESTATUS[@]}")
  rc=${ps[0]}
  if (( ps[1] != 0 )); then echo "capture-state: redactor failed on $id" >&2; exit 2; fi
  fails=$(grep -c '^\[exit ' "$file")
  printf '\n# check exit %s; failed commands %s\n' "$rc" "$fails" >> "$file"
  printf '%-46s exit=%-3s failed-cmds=%-3s %s\n' "$id.txt" "$rc" "$fails" "$title" >> "$OUT/INDEX.txt"
  if [[ -e $OUT/.guard-violation ]]; then echo "capture-state: guard violation in $id" >&2; exit 2; fi
}

# leak_check <dir>: greps a completed, redacted output directory for raw credential prefixes.
# Never prints a match, only the list of files that contain one (amendment A4). The
# PRIVATE-KEY/PGP-BLOCK alternatives are narrowed to the actual PEM header line so prose
# mentioning "private key" in passing doesn't trip it (round 2 point 9).
leak_check() {
  local dir=$1 leaks
  LEAK_RE='do''p_v1_|gs''k_|gh''p_|github''_pat_|sk-''ant-|AK''IA|-----BEGIN[A-Z ]*PRIVATE'' KEY-----|-----BEGIN PGP PRIVATE'' KEY BLOCK-----'
  leaks=$(grep -rlE -- "$LEAK_RE" "$dir" 2>/dev/null)
  if [[ -n $leaks ]]; then
    { echo "# leak self-check FAILED: a credential pattern matched in these files (matches not shown)"; echo "$leaks"; } > "$dir/LEAK-CHECK.txt"
    echo "leak self-check FAILED, see $dir/LEAK-CHECK.txt" >&2
    return 1
  fi
  echo "# leak self-check: no credential pattern found in $(find "$dir" -type f | wc -l) files" > "$dir/LEAK-CHECK.txt"
  return 0
}
