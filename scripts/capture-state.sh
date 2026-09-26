#!/usr/bin/env bash
# capture-state.sh — read-only snapshot of the NEXUS repository and cluster.
#
# Spec §25 (operational tooling), milestone M0-1. Writes one file per check into
# docs/state/<UTC timestamp>/, plus INDEX.txt, SUDO-REQUIRED.txt and LEAK-CHECK.txt.
#
# Guarantees:
#   - kubectl is limited to get, describe, logs, version, api-resources, auth can-i,
#     plus one temporary port-forward to sample-api /metrics that is stopped afterwards;
#     helm to list, status, get values; git to read-only subcommands, never fetch.
#   - Secret data is never read. Cluster Secrets are listed by name only. Files in Git
#     under a secrets/ path are never opened; files containing "kind: Secret" are only
#     tested for data/stringData presence. Every output passes through a redactor.
#   - Log output is capped at 200 lines per log block.
#   - Anything that needs sudo is skipped and written to SUDO-REQUIRED.txt.
#
# Exit codes: 0 every check ran (findings never fail the run); 2 script error or
# read-only guard violation; 3 the leak self-check matched a file in the snapshot.
#
# Usage: scripts/capture-state.sh [--backup]
#   --backup   after a clean run, copy the snapshot verbatim to ~/nexus-backup/state-<TS>/
#              (created at 0700 if missing). Only runs after the leak self-check passes.

set -uo pipefail

BACKUP=0
for a in "$@"; do
  case $a in
    --backup) BACKUP=1 ;;
    *) echo "capture-state: unknown argument $a" >&2; exit 2 ;;
  esac
done

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "capture-state: not inside a git repository" >&2; exit 2; }
cd "$REPO_ROOT" || exit 2
command -v python3 >/dev/null 2>&1 || { echo "capture-state: python3 is required for redaction" >&2; exit 2; }

export GIT_OPTIONAL_LOCKS=0 GIT_PAGER=cat PAGER=cat
STATE_ROOT=docs/state
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$STATE_ROOT/$TS
[[ -e $OUT ]] && OUT=$OUT-$$
mkdir -p "$STATE_ROOT" && mkdir "$OUT" || { echo "capture-state: cannot create $OUT" >&2; exit 2; }
if [[ ! -f $STATE_ROOT/.gitignore ]]; then
  printf '*\n!.gitignore\n' > "$STATE_ROOT/.gitignore" || exit 2
fi
trap 'rm -f -- "$OUT"/.tmp.* 2>/dev/null' EXIT

# shellcheck source=lib/readonly.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/readonly.sh"

# ---------------------------------------------------------------------------
# Secret-bearing files in Git (amendment A2, narrowed in M0-2): a path under
# secrets/ is never opened; a file with kind: Secret and data/stringData is
# excluded from every content capture; a file that only names kind: Secret is
# listed but readable.
# ---------------------------------------------------------------------------
mapfile -d '' TRACKED < <(g ls-files -z)
mapfile -d '' UNTRACKED < <(g ls-files -z --others --exclude-standard)
declare -A SB_REASON=() SECRET_NAMED=()
is_secret_path() { [[ /$1 == */secrets/* ]]; }
for f in "${TRACKED[@]}" "${UNTRACKED[@]}"; do
  if is_secret_path "$f"; then
    SB_REASON[$f]="path under secrets/ — never opened"
  elif [[ -f $f && ! -L $f ]] && grep -qE '^[[:space:]]*kind:[[:space:]]*"?Secret"?[[:space:]]*$|"kind"[[:space:]]*:[[:space:]]*"Secret"' -- "$f" 2>/dev/null; then
    if grep -qE '^[[:space:]]*"?(data|stringData)"?[[:space:]]*:' -- "$f" 2>/dev/null; then
      SB_REASON[$f]="contains kind: Secret; data/stringData present"
    else
      SECRET_NAMED[$f]="names kind: Secret; no data/stringData — readable"
    fi
  fi
done
EXCLUDES=(':(exclude,glob)**/secrets/**' ':(exclude,glob)secrets/**')
for f in "${!SB_REASON[@]}"; do EXCLUDES+=(":(exclude,literal)$f"); done

# ---------------------------------------------------------------------------
# 00 Environment
# ---------------------------------------------------------------------------
c_env() {
  run uname -a
  show "grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release"; grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release
  show "grep -ci microsoft /proc/version   (WSL kernel marker)"; grep -ci microsoft /proc/version
  show "repository path"; echo "$REPO_ROOT"
  if [[ $REPO_ROOT == /mnt/c/* ]]; then echo "WARNING: repository is under /mnt/c"; else echo "not under /mnt/c"; fi
  show ".venv/bin/python --version"
  if [[ -x .venv/bin/python ]]; then .venv/bin/python --version; else echo ".venv/bin/python absent"; fi
  show "tool presence"
  local t
  for t in kubectl helm git curl jq python3 gh argocd shellcheck kyverno k8sgpt; do
    printf '%-11s %s\n' "$t" "$(command -v "$t" || echo absent)"
  done
  run g --version
  run k version --client
  show "kubeconfig source (path only, never read)"; echo "${KUBECONFIG:-default (~/.kube/config)}"
  run k auth can-i '*' '*' --all-namespaces
}

# ---------------------------------------------------------------------------
# 01 Git
# ---------------------------------------------------------------------------
c_git_refs() {
  run g branch --show-current
  run g rev-parse HEAD
  run g remote -v
  run g branch -a -vv --no-color
  run g for-each-ref --format='%(refname) %(objectname:short) %(objecttype) %(creatordate:iso-strict)' refs/tags
  run g stash list
  run g ls-remote --heads --tags origin
  show "compare HEAD, local refs/remotes/origin/main and remote main (this script never fetches)"
  local head lorig rorig
  head=$(g rev-parse HEAD 2>/dev/null)
  lorig=$(g rev-parse --verify -q refs/remotes/origin/main 2>/dev/null)
  rorig=$(g ls-remote origin refs/heads/main 2>/dev/null | awk '{print $1}')
  printf 'HEAD              %s\nlocal origin/main %s\nremote main       %s\n' "$head" "${lorig:-none}" "${rorig:-unreachable}"
  if [[ -n $rorig && $lorig == "$rorig" ]]; then echo "local origin/main is current"; else echo "local origin/main differs from remote main or remote unreachable"; fi
  if [[ -n $lorig ]]; then
    run g rev-list --left-right --count HEAD...refs/remotes/origin/main
    echo "(left = commits only on HEAD, right = commits only on origin/main)"
  fi
}

c_git_log() {
  run g log -n 20 --date=iso-strict --format='%h %ad %an%d %s'
}

c_git_status() {
  run g status --porcelain=v1 --branch --untracked-files=all
  run g diff --stat
  run g diff --numstat
  run g diff --cached --stat
}

c_git_diff() {
  show "git diff -- . <pathspec exclusions for ${#SB_REASON[@]} Secret-bearing files and every secrets/ path>"
  g diff -- . "${EXCLUDES[@]}"
  show "git diff --cached -- . <same exclusions>"
  g diff --cached -- . "${EXCLUDES[@]}"
}

c_git_untracked() {
  show "git ls-files --others --exclude-standard, then per file: size, lines, first 30 lines (Secret-bearing files: path only)"
  local f
  for f in "${UNTRACKED[@]}"; do
    printf '\n=== %s\n' "$f"
    if [[ -n ${SB_REASON[$f]:-} ]]; then
      printf 'size=%s bytes; Secret-bearing (%s): content not captured\n' "$(stat -c %s -- "$f" 2>/dev/null)" "${SB_REASON[$f]}"
      continue
    fi
    printf 'size=%s bytes lines=%s\n' "$(stat -c %s -- "$f")" "$(wc -l < "$f")"
    if [[ ! -s $f ]]; then
      echo "(empty file)"
    elif grep -Iq . -- "$f" 2>/dev/null; then
      echo "--- head -n 30"
      head -n 30 -- "$f"
    else
      echo "(binary file; head not captured)"
    fi
  done
}

c_git_ci() {
  run ls -la .github/workflows
  local f
  for f in .github/workflows/*; do
    [[ -f $f ]] || continue
    if [[ -n ${SB_REASON[$f]:-} ]]; then echo "$f is Secret-bearing; content not captured"; continue; fi
    show "cat $f"; cat -- "$f"
  done
  show "GitLab CI files"
  run g ls-files '*gitlab-ci*'
  if [[ -e .gitlab-ci.yml ]]; then echo ".gitlab-ci.yml present"; else echo ".gitlab-ci.yml absent"; fi
  show "GitHub Actions run history and branch protection (gh, read-only)"
  if have gh && gh auth status >/dev/null 2>&1; then
    run gh run list -L 10
    run gh api repos/koussayx8/nexus-platform/branches/main/protection
    run gh api repos/koussayx8/nexus-platform/branches/dev/protection
  else
    echo "gh not installed or not authenticated: skipped."
    echo "To settle: gh run list -L 10; gh api repos/koussayx8/nexus-platform/branches/main/protection"
  fi
}

c_git_secret_files() {
  show "Secret-bearing files (tracked and untracked): path and reason only, never content"
  local f state
  for f in "${!SB_REASON[@]}"; do
    state=tracked
    printf '%s\n' "${UNTRACKED[@]}" | grep -qxF -- "$f" && state=untracked
    printf '%-70s %-10s %s\n' "$f" "$state" "${SB_REASON[$f]}"
  done | sort
  show "Files that only name kind: Secret (readable under narrowed A2)"
  for f in "${!SECRET_NAMED[@]}"; do printf '%-70s %s\n' "$f" "${SECRET_NAMED[$f]}"; done | sort
  show "git ls-files '*secrets/*'   (index only)"
  g ls-files '*secrets/*'
  show "grep -n secrets .gitignore"; grep -n secrets .gitignore || echo "(no secrets rule in .gitignore)"
  run g log --date=short --format='%h %ad %an %s' -- platform/crossplane/secrets/digitalocean-creds.yaml
}

c_git_layout() {
  show "git ls-files | top two path levels | count"
  g ls-files | awk -F/ 'NF>2{print $1"/"$2"/"} NF==2{print $1"/"} NF==1{print $1}' | sort | uniq -c
  show "presence of spec §25 top-level paths"
  local p
  for p in apps/sample-api platform/namespaces platform/crds platform/rbac platform/policies platform/network \
           platform/monitoring platform/argocd platform/bootstrap-templates overlays/dev overlays/prod operator \
           reasoner baseline nexusctl libs/nexus-client runbooks experiments scripts demo docs/adr Makefile; do
    if [[ -e $p ]]; then printf '%-30s present\n' "$p"; else printf '%-30s absent\n' "$p"; fi
  done
}

# ---------------------------------------------------------------------------
# 02 k3s
# ---------------------------------------------------------------------------
c_k8s_version() {
  run k version
  run k get nodes -o wide
  run k get nodes -o go-template='{{range .items}}{{.metadata.name}} kubelet={{.status.nodeInfo.kubeletVersion}} os={{.status.nodeInfo.osImage}} kernel={{.status.nodeInfo.kernelVersion}} runtime={{.status.nodeInfo.containerRuntimeVersion}}{{"\n"}}{{end}}'
}

c_node_resources() {
  run k get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\n"}}  capacity:    cpu={{.status.capacity.cpu}} memory={{.status.capacity.memory}} pods={{.status.capacity.pods}}{{"\n"}}  allocatable: cpu={{.status.allocatable.cpu}} memory={{.status.allocatable.memory}} pods={{.status.allocatable.pods}}{{"\n"}}{{end}}'
  show "kubectl describe nodes | sed -n '/^Non-terminated Pods:/,/^Events:/p'"
  k describe nodes | sed -n '/^Non-terminated Pods:/,/^Events:/p'
  run k get --raw /apis/metrics.k8s.io/v1beta1/nodes
  run nproc
  show "grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree' /proc/meminfo"
  grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree' /proc/meminfo
}

K3S_CONFIGS=(/etc/rancher/k3s/config.yaml /etc/rancher/k3s/config.yaml.d/*.yaml)

c_k3s_flags() {
  local nodeargs f
  show "kubectl get nodes -o go-template='<annotation k3s.io/node-args>'"
  nodeargs=$(k get nodes -o go-template='{{range .items}}{{.metadata.name}}: {{index .metadata.annotations "k3s.io/node-args"}}{{"\n"}}{{end}}')
  echo "$nodeargs"
  run pgrep -af 'k3s (server|agent)'
  run systemctl cat k3s
  run ls -la /etc/rancher/k3s/
  for f in "${K3S_CONFIGS[@]}"; do
    [[ -e $f ]] || continue
    if [[ -r $f ]]; then
      show "grep -n audit $f   (audit lines only)"
      grep -n audit -- "$f" || echo "(no audit lines)"
    else
      echo "$f exists but is not readable"
      sudo_needed "sudo grep -n audit $f" "audit lines of the k3s config"
    fi
  done
  show "ls -la /var/lib/rancher/k3s/server/logs/"
  if ! ls -la /var/lib/rancher/k3s/server/logs/ 2>&1; then
    sudo_needed "sudo ls -la /var/lib/rancher/k3s/server/logs/" "whether an API audit log file exists and grows"
  fi
  if ! pgrep -af 'k3s (server|agent)' 2>/dev/null | grep -q -- '--'; then
    sudo_needed "sudo cut -d= -f1 /etc/systemd/system/k3s.service.env" "names only of env vars that could carry k3s flags"
  fi
  show "summary: audit-related flags in node-args, process args, unit file and readable config"
  local src hits
  src=$( { echo "$nodeargs"; pgrep -af 'k3s (server|agent)'; systemctl cat k3s; \
           for f in "${K3S_CONFIGS[@]}"; do [[ -r $f ]] && grep -h audit -- "$f"; done; } 2>/dev/null )
  hits=$(grep -oE 'audit-(policy-file|log-[a-z]+|webhook-[a-z-]+)[=:" ]*[^ ",]*' <<<"$src" | sort -u)
  if [[ -n $hits ]]; then echo "$hits"; else echo "NONE FOUND: no audit-policy-file or audit-log-* flag in any readable source"; fi
}

# ---------------------------------------------------------------------------
# 03 Cluster-wide
# ---------------------------------------------------------------------------
c_namespaces() { run k get ns --show-labels; }

c_crds() {
  run k get crd -o custom-columns=NAME:.metadata.name,GROUP:.spec.group,CREATED:.metadata.creationTimestamp --sort-by=.spec.group
  show "kubectl get crd -o custom-columns=GROUP:.spec.group --no-headers | sort | uniq -c"
  k get crd -o custom-columns=GROUP:.spec.group --no-headers | sort | uniq -c | sort -rn
}

c_helm() {
  run h list -A -a
  local rel ns
  while read -r rel ns; do
    [[ -n $rel ]] || continue
    run h status "$rel" -n "$ns"
    run h get values "$rel" -n "$ns"
  done < <(h list -A -a 2>/dev/null | awk 'NR>1{print $1, $2}')
}

c_workloads() { run k get deploy,sts,ds -A -o wide; }

UNHEALTHY_AWK='{split($3,r,"/"); if ($4!="Completed" && ($4!="Running" || r[1]!=r[2])) print}'
c_pods_unhealthy() {
  show "kubectl get pods -A -o wide --no-headers | awk '<STATUS not Running/Completed, or READY x/y with x<y>'"
  local out
  out=$(k get pods -A -o wide --no-headers | awk "$UNHEALTHY_AWK")
  if [[ -n $out ]]; then echo "$out"; else echo "(none: every pod is Running and Ready, or Completed)"; fi
  run k get pods -A -o wide
}

c_pod_logs() {   # <namespace> <pod> <restarts>
  local ns=$1 pod=$2 restarts=${3:-0}
  show "kubectl logs -n $ns $pod --all-containers --prefix --tail=200 | head -n 200"
  k logs -n "$ns" "$pod" --all-containers --prefix --tail=200 2>&1 | head -n 200
  if [[ $restarts != 0 ]]; then
    show "kubectl logs -n $ns $pod --all-containers --prefix --previous --tail=200 | head -n 200"
    k logs -n "$ns" "$pod" --all-containers --prefix --previous --tail=200 2>&1 | head -n 200
  fi
  show "kubectl describe pod -n $ns $pod | sed -n '/^Events:/,\$p' | head -n 60"
  k describe pod -n "$ns" "$pod" | sed -n '/^Events:/,$p' | head -n 60
}

c_events() {
  show "kubectl get events -A --field-selector type=Warning --sort-by=.lastTimestamp | tail -n 200"
  k get events -A --field-selector type=Warning --sort-by=.lastTimestamp 2>&1 | tail -n 200
}

c_apiservices() {
  show "kubectl get apiservices | awk 'NR==1 || \$3 != \"True\"'   (not Available)"
  k get apiservices | awk 'NR==1 || $3 != "True"'
}

# ---------------------------------------------------------------------------
# 04 ArgoCD
# ---------------------------------------------------------------------------
JQ_APPS='.items[] | [
  .metadata.name,
  "sync=" + (.status.sync.status // "-"),
  "health=" + (.status.health.status // "-"),
  "rev=" + ((.status.sync.revision // ((.status.sync.revisions // []) | join(","))) | tostring),
  "repo=" + (if .spec.source then (.spec.source.repoURL // "-") else ([.spec.sources[]?.repoURL] | join(",")) end),
  "src=" + (if .spec.source then (if .spec.source.path then "path:" + .spec.source.path else "chart:" + (.spec.source.chart // "-") end)
            else ([.spec.sources[]? | (if .path then "path:" + .path else "chart:" + (.chart // "-") end)] | join(",")) end),
  "targetRevision=" + (if .spec.source then (.spec.source.targetRevision // "-") else ([.spec.sources[]?.targetRevision] | join(",")) end),
  "helmValues=" + ([(.spec.source // empty), (.spec.sources[]?)] | map((.helm // null) | if . == null then "no-helm" elif (.values // .valuesObject) then "inline" elif .valueFiles then "valueFiles" else "helm-no-values" end) | join(",")),
  "multiSource=" + ((.spec.sources != null) | tostring),
  "automated=" + ((.spec.syncPolicy.automated != null) | tostring),
  "selfHeal=" + ((.spec.syncPolicy.automated.selfHeal // false) | tostring),
  "prune=" + ((.spec.syncPolicy.automated.prune // false) | tostring),
  "ignoreDifferences=" + ((.spec.ignoreDifferences // []) | length | tostring),
  "syncOptions=" + ((.spec.syncPolicy.syncOptions // []) | join(";")),
  "destNs=" + (.spec.destination.namespace // "-"),
  "op=" + (.status.operationState.phase // "-")
] | join("  ")'

c_argocd_apps() {
  run k get applications.argoproj.io -A -o wide
  need_jq || return 0
  show "kubectl get applications.argoproj.io -n argocd -o json | jq <per-application summary>"
  k get applications.argoproj.io -n argocd -o json | jq -r "$JQ_APPS"
  show "conditions per Application"
  k get applications.argoproj.io -n argocd -o json | jq -r '.items[] | select((.status.conditions // []) | length > 0) | .metadata.name + ": " + ([.status.conditions[] | .type + ": " + (.message // "")] | join(" | "))'
  show "last operation per Application"
  k get applications.argoproj.io -n argocd -o json | jq -r '.items[] | select(.status.operationState) | .metadata.name + ": " + (.status.operationState.phase // "-") + " — " + (.status.operationState.message // "")'
  show "ignoreDifferences per Application"
  k get applications.argoproj.io -n argocd -o json | jq -r '.items[] | select(.spec.ignoreDifferences) | .metadata.name + ": " + (.spec.ignoreDifferences | tostring)'
  run k get applicationsets.argoproj.io -A
}

c_argocd_resources() {
  need_jq || return 0
  show "kubectl get applications.argoproj.io -n argocd -o json | jq '.status.resources[]'   (app, kind, namespace, name, sync, health)"
  k get applications.argoproj.io -n argocd -o json | jq -r '.items[] | .metadata.name as $a | (.status.resources // [])[] | [$a, .kind, (.namespace // "-"), .name, (.status // "-"), (.health.status // "-")] | @tsv'
}

c_argocd_projects() {
  run k get appprojects.argoproj.io -n argocd
  need_jq || return 0
  show "kubectl get appprojects.argoproj.io -n argocd -o json | jq <sourceRepos, destinations, cluster resource whitelist>"
  k get appprojects.argoproj.io -n argocd -o json | jq '.items[] | {name: .metadata.name, sourceRepos: .spec.sourceRepos, destinations: .spec.destinations, clusterResourceWhitelist: .spec.clusterResourceWhitelist}'
}

c_argocd_config() {
  run k get deploy,sts -n argocd -o custom-columns=NAME:.metadata.name,IMAGES:.spec.template.spec.containers[*].image,READY:.status.readyReplicas
  run k get pods -n argocd -o wide
  need_jq || return 0
  show "kubectl get cm argocd-cm -n argocd -o json | jq '.data | keys'   (key names only)"
  k get cm argocd-cm -n argocd -o json | jq -r '(.data // {}) | keys[]'
  show "argocd-cm application.resourceTrackingMethod"
  k get cm argocd-cm -n argocd -o json | jq -r '(.data // {})["application.resourceTrackingMethod"] // "(unset: ArgoCD default)"'
}

c_argocd_git_vs_cluster() {
  local files f n gitnames clusternames
  show "git grep -l --untracked '^kind: Application$' -- . <exclusions>"
  files=$(g grep -l --untracked -E '^kind:[[:space:]]*Application[[:space:]]*$' -- . "${EXCLUDES[@]}")
  echo "$files"
  show "metadata.name of each Application manifest in the working tree"
  gitnames=$(for f in $files; do
      n=$(awk '/^metadata:/{m=1; next} m && /^[^ ]/{m=0} m && /^  name:/{print $2; exit}' "$f")
      printf '%s\t%s\n' "$n" "$f"
    done | sort)
  echo "$gitnames"
  show "kubectl get applications.argoproj.io -n argocd -o custom-columns=NAME:.metadata.name --no-headers"
  clusternames=$(k get applications.argoproj.io -n argocd -o custom-columns=NAME:.metadata.name --no-headers 2>&1 | sort)
  echo "$clusternames"
  show "in Git only (column 1) · in cluster only (column 2) · both (column 3)"
  comm <(cut -f1 <<<"$gitnames" | sort -u) <(sort -u <<<"$clusternames")
  run g ls-tree --name-only refs/remotes/origin/main platform/argocd/applications/
  echo "(ls-tree shows the local origin/main ref; see 01a-git-refs for whether it is current)"
}

# ---------------------------------------------------------------------------
# 05 Grafana
# ---------------------------------------------------------------------------
c_grafana_ds_cms() {
  run k get cm -A -l grafana_datasource --show-labels
  need_jq || return 0
  show "kubectl get cm -A -l grafana_datasource -o json | jq <every data key and value>"
  k get cm -A -l grafana_datasource -o json | jq -r '.items[] | "=== \(.metadata.namespace)/\(.metadata.name)  labels=\(.metadata.labels | tostring)", ((.data // {}) | to_entries[] | "--- key: \(.key)", .value)'
}

c_grafana_cms() {
  run k get cm -A -l app.kubernetes.io/name=grafana
  need_jq || return 0
  show "Grafana ConfigMaps: key names, and the content of any key that names datasources"
  k get cm -A -l app.kubernetes.io/name=grafana -o json | jq -r '.items[] | "=== \(.metadata.namespace)/\(.metadata.name) keys=\((.data // {}) | keys | join(","))", ((.data // {}) | to_entries[] | select(.key | test("datasource"; "i")) | "--- key: \(.key)", .value)'
}

c_grafana_ds_secrets() {
  run secret_names -A -l grafana_datasource
  show "kubectl get secrets -A | awk 'NR==1 || /grafana/'   (names only)"
  secret_names -A | awk 'NR==1 || /grafana/'
}

c_grafana_workload() {
  run k get deploy,sts -A -l app.kubernetes.io/name=grafana -o wide
  need_jq || return 0
  show "Grafana containers, images and datasource-sidecar settings"
  k get deploy -A -l app.kubernetes.io/name=grafana -o json | jq -r '.items[] | "=== \(.metadata.namespace)/\(.metadata.name) replicas=\(.spec.replicas) ready=\(.status.readyReplicas // 0)", (.spec.template.spec.containers[] | "  container \(.name) image=\(.image)", ((.env // [])[] | select(.name | test("^(LABEL|LABEL_VALUE|FOLDER|RESOURCE|NAMESPACE|METHOD|UNIQUE_FILENAMES|REQ_URL|REQ_METHOD)$")) | "    env \(.name)=\(.value // "(valueFrom)")"))'
  show "Grafana pod container states"
  k get pods -A -l app.kubernetes.io/name=grafana -o json | jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name) phase=\(.status.phase)", (.status.containerStatuses[]? | "  \(.name) ready=\(.ready) restarts=\(.restartCount) state=\(.state | keys[0]) lastState=\((.lastState | keys[0]) // "-") \(.lastState.terminated.reason // "")")'
}

c_grafana_logs() {
  local ns pod restarts cur prev hits
  while read -r ns pod restarts; do
    [[ -n ${pod:-} ]] || continue
    show "kubectl logs -n $ns $pod -c grafana --tail=200 | head -n 200"
    cur=$(k logs -n "$ns" "$pod" -c grafana --tail=200 2>&1 | head -n 200)
    echo "$cur"
    prev=
    if [[ ${restarts:-0} != 0 ]]; then
      show "kubectl logs -n $ns $pod -c grafana --previous --tail=200 | head -n 200"
      prev=$(k logs -n "$ns" "$pod" -c grafana --previous --tail=200 2>&1 | head -n 200)
      echo "$prev"
    fi
    show "grep -iE 'datasource|default|provisioning' over the log lines above (max 50)"
    hits=$(printf '%s\n%s\n' "$cur" "$prev" | grep -iE 'datasource|default|provisioning' | head -n 50)
    echo "${hits:-(no matches)}"
  done < <(k get pods -A -l app.kubernetes.io/name=grafana -o go-template='{{range .items}}{{.metadata.namespace}} {{.metadata.name}} {{range .status.containerStatuses}}{{if eq .name "grafana"}}{{.restartCount}}{{end}}{{end}}{{"\n"}}{{end}}')
}

c_grafana_isdefault() {
  need_jq || return 0
  show "live: isDefault: true count and datasource names per labelled ConfigMap"
  k get cm -A -l grafana_datasource -o json | jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name)  isDefault_true=\([(.data // {})[] | scan("(?i)isDefault\"?\\s*:\\s*true")] | length)  names=\([(.data // {})[] | scan("\\bname:\\s*\"?([^\"\\n]+)") | .[0]] | join(","))"'
  show "live: same count over Grafana ConfigMaps (keys naming datasources)"
  k get cm -A -l app.kubernetes.io/name=grafana -o json | jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name)  isDefault_true=\([(.data // {}) | to_entries[] | select(.key | test("datasource"; "i")) | .value | scan("(?i)isDefault\"?\\s*:\\s*true")] | length)"'
  show "git grep -n --untracked -i -E 'isDefault|additionalDataSources|datasources:' -- platform apps <exclusions>"
  g grep -n --untracked -i -E 'isDefault|additionalDataSources|datasources:' -- platform apps "${EXCLUDES[@]}"
}

# ---------------------------------------------------------------------------
# 06 sample-api
# ---------------------------------------------------------------------------
c_sampleapi_deployed() {
  need_jq || return 0
  show "Deployments whose name contains sample-api: namespace, name, replicas, ready, image"
  k get deploy -A -o json | jq -r '.items[] | select(.metadata.name | test("sample-api")) | [.metadata.namespace, .metadata.name, "replicas=\(.spec.replicas)", "ready=\(.status.readyReplicas // 0)", ([.spec.template.spec.containers[].image] | join(","))] | @tsv'
  show "Pods owned by sample-api ReplicaSets: running imageID"
  k get pods -A -o json | jq -r '.items[] | select((.metadata.ownerReferences[0].name // "") | test("^sample-api-")) | [.metadata.namespace, .metadata.name, .status.phase, ([.status.containerStatuses[]? | "\(.name) ready=\(.ready) restarts=\(.restartCount) image=\(.image) imageID=\(.imageID)"] | join("; "))] | @tsv'
}

SA_BASE=apps/sample-api/k8s/base/deployment.yaml
SA_OVERLAY=apps/sample-api/k8s/overlays/dev/kustomization.yaml
c_sampleapi_git_digest() {
  run g ls-files 'apps/sample-api/k8s/*'
  local rev
  for rev in HEAD refs/remotes/origin/main; do
    show "git show $rev:$SA_BASE | grep -nE 'image:|namespace:|replicas:'"
    g show "$rev:$SA_BASE" | grep -nE 'image:|namespace:|replicas:'
    show "git show $rev:$SA_OVERLAY | grep -nE 'image|digest|newTag|newName|replicas|namespace'"
    g show "$rev:$SA_OVERLAY" | grep -nE 'image|digest|newTag|newName|replicas|namespace' || echo "(no image or digest override in the overlay)"
  done
  show "working tree: grep -nE 'image|digest' $SA_BASE $SA_OVERLAY"
  grep -nE 'image|digest' "$SA_BASE" "$SA_OVERLAY"
  show "compare: digest pinned in Git (HEAD, base) vs running pod imageIDs"
  local git_digest
  git_digest=$(g show "HEAD:$SA_BASE" | grep -oE 'sha256:[0-9a-f]{64}' | head -n 1)
  echo "git HEAD digest: ${git_digest:-none}"
  need_jq || return 0
  k get pods -A -o json | jq -r --arg d "$git_digest" '.items[] | select((.metadata.ownerReferences[0].name // "") | test("^sample-api-")) | .metadata.namespace as $ns | .metadata.name as $p | .status.containerStatuses[]? | "\($ns)/\($p) \(.name) running=\(([(.imageID // "") | match("sha256:[0-9a-f]{64}").string] | first) // "none") \(if ($d != "") and ((.imageID // "") | contains($d)) then "MATCH" else "MISMATCH" end)"'
}

c_sampleapi_metrics() {
  need_jq || return 0
  local svcs ns port lport body pflog pid code i
  show "Services named sample-api (namespace, first port)"
  svcs=$(k get svc -A -o json | jq -r '.items[] | select(.metadata.name == "sample-api") | "\(.metadata.namespace) \(.spec.ports[0].port)"')
  echo "${svcs:-(none)}"
  [[ -n $svcs ]] || return 0
  while read -r ns port; do
    lport=$(( 20000 + RANDOM % 20000 ))
    body=$(mktemp "$OUT/.tmp.XXXXXX"); pflog=$(mktemp "$OUT/.tmp.XXXXXX")
    # The only non-read verb in this script: a temporary port-forward, stopped below
    # and bounded by timeout(1) in case the kill is missed.
    show "kubectl port-forward -n $ns svc/sample-api $lport:$port --address 127.0.0.1   (temporary, background)"
    timeout 60 kubectl --request-timeout=20s port-forward -n "$ns" svc/sample-api "$lport:$port" --address 127.0.0.1 >"$pflog" 2>&1 &
    pid=$!
    trap '[[ -n ${pid:-} ]] && kill "$pid" 2>/dev/null' EXIT
    for i in $(seq 1 40); do
      grep -q 'Forwarding from' "$pflog" && break
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    cat "$pflog"
    show "curl -s -o <body> -w '%{http_code}' --max-time 10 http://127.0.0.1:$lport/metrics"
    code=$(curl -s -o "$body" -w '%{http_code}' --max-time 10 "http://127.0.0.1:$lport/metrics")
    echo "namespace=$ns HTTP /metrics status=$code"
    if [[ $code == 200 ]]; then
      show "metric families (# TYPE lines)"; grep '^# TYPE' "$body"
      echo "sample lines: $(grep -vc '^#' "$body")"
    else
      show "response body (first 20 lines)"; head -n 20 "$body"
    fi
    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    if kill -0 "$pid" 2>/dev/null; then echo "WARNING: port-forward pid $pid still running"; else echo "port-forward stopped (pid $pid gone)"; fi
    show "pgrep -af 'port-forward -n $ns svc/sample-api $lport'"
    pgrep -af "port-forward -n $ns svc/sample-api $lport" || echo "(no port-forward process left)"
    trap - EXIT
    rm -f -- "$body" "$pflog"
  done <<<"$svcs"
}

# ---------------------------------------------------------------------------
# 07 Kyverno
# ---------------------------------------------------------------------------
c_kyverno_install() {
  run k get deploy -n kyverno -o custom-columns=NAME:.metadata.name,IMAGES:.spec.template.spec.containers[*].image,READY:.status.readyReplicas
  run k get pods -n kyverno -o wide
  run h list -n kyverno -a
}

c_kyverno_cpol() {
  run k get clusterpolicies.kyverno.io -o wide
  need_jq || return 0
  show "per ClusterPolicy: validationFailureAction, failurePolicy, background, Ready; per rule: type and validate.failureAction"
  k get clusterpolicies.kyverno.io -o json | jq -r '.items[] | "\(.metadata.name)  validationFailureAction=\(.spec.validationFailureAction // "unset")  failurePolicy=\(.spec.failurePolicy // "unset(default Fail)")  background=\(if (.spec | has("background")) then (.spec.background | tostring) else "unset" end)  ready=\(([.status.conditions[]? | select(.type == "Ready") | .status] | first) // "?")", ((.spec.rules // [])[] | "    rule \(.name): type=\(if .validate then "validate" elif .mutate then "mutate" elif .generate then "generate" elif .verifyImages then "verifyImages" else "?" end) failureAction=\(.validate.failureAction // "unset")")'
}

c_kyverno_other() {
  run k get policies.kyverno.io -A
  run k api-resources --api-group=kyverno.io
  run k api-resources --api-group=policies.kyverno.io
  local r
  while read -r r; do
    [[ -n $r ]] || continue
    run k get "$r" -A
  done < <(k api-resources --api-group=policies.kyverno.io -o name 2>/dev/null)
}

c_webhooks() {
  need_jq || return 0
  show "kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o json | jq <configuration, webhook, failurePolicy, timeout>"
  k get validatingwebhookconfigurations,mutatingwebhookconfigurations -o json | jq -r '.items[] | .kind as $k | .metadata.name as $n | (.webhooks // [])[] | [$k, $n, .name, "failurePolicy=\(.failurePolicy)", "timeout=\(.timeoutSeconds)"] | @tsv'
}

c_kyverno_git() {
  run g ls-files platform/kyverno
  show "git grep -n -E 'kind:|name:|validationFailureAction|failureAction|failurePolicy' -- platform/kyverno <exclusions>"
  g grep -n -E '^kind:|^  name:|validationFailureAction|failureAction|failurePolicy' -- platform/kyverno "${EXCLUDES[@]}"
  show "git grep -n --untracked 'kyverno' -- platform/argocd   (is any Application tracking the policies?)"
  g grep -n --untracked -i 'kyverno' -- platform/argocd "${EXCLUDES[@]}" || echo "(no Application references kyverno)"
}

# ---------------------------------------------------------------------------
# 08 Crossplane and Loki
# ---------------------------------------------------------------------------
c_cl_argocd() {
  need_jq || return 0
  show "Applications matching loki|crossplane|promtail and every resource they track"
  k get applications.argoproj.io -n argocd -o json | jq -r '.items[] | select(.metadata.name | test("loki|crossplane|promtail")) | .metadata.name as $a | "=== \($a) sync=\(.status.sync.status // "-") health=\(.status.health.status // "-")", ((.status.resources // [])[] | [$a, .kind, (.namespace // "-"), .name, (.status // "-"), (.health.status // "-")] | @tsv)'
  show "helm releases matching loki|promtail|crossplane"
  h list -A -a 2>&1 | awk 'NR==1 || tolower($0) ~ /loki|promtail|crossplane/'
}

c_loki_objects() {
  local sel
  for sel in app.kubernetes.io/instance=loki app.kubernetes.io/name=loki app.kubernetes.io/name=promtail release=loki app=loki app=promtail; do
    run k get all,cm,sa,pvc,ingress,networkpolicy,role,rolebinding -A -l "$sel" -o wide
    run k get clusterrole,clusterrolebinding -l "$sel"
    run k get servicemonitors.monitoring.coreos.com,podmonitors.monitoring.coreos.com,prometheusrules.monitoring.coreos.com -A -l "$sel"
    run secret_names -A -l "$sel"
  done
  show "kubectl get pvc,pv -A | grep -iE 'loki|promtail'"
  k get pvc -A 2>&1 | awk 'NR==1 || tolower($0) ~ /loki|promtail/'
  k get pv 2>&1 | awk 'NR==1 || tolower($0) ~ /loki|promtail/'
  show "any object name containing loki or promtail across common kinds"
  k get deploy,sts,ds,svc,cm,sa -A 2>&1 | awk 'NR==1 || /^NAMESPACE/ || tolower($0) ~ /loki|promtail/'
}

c_crossplane_ns() {
  run k get ns crossplane-system --show-labels
  run k get all,cm,sa,pvc,role,rolebinding -n crossplane-system -o wide
  run secret_names -n crossplane-system
}

CP_GROUP_RE='crossplane\.io$|upbound\.io$|digitalocean'
cp_crds() {   # CRD names in Crossplane-related groups, plus groups defined by XRDs
  local xrd_groups re
  xrd_groups=$(k get compositeresourcedefinitions.apiextensions.crossplane.io -o jsonpath='{range .items[*]}{.spec.group}{"\n"}{end}' 2>/dev/null | sort -u | sed 's/\./\\./g' | paste -sd'|' -)
  re=$CP_GROUP_RE${xrd_groups:+|^($xrd_groups)$}
  k get crd -o custom-columns=NAME:.metadata.name,GROUP:.spec.group --no-headers 2>/dev/null | RE=$re awk '$2 ~ ENVIRON["RE"] {print $1}'
}

c_crossplane_crds() {
  show "CRDs whose group matches $CP_GROUP_RE or an XRD-defined group"
  cp_crds
  run k get compositeresourcedefinitions.apiextensions.crossplane.io
  run k get compositions.apiextensions.crossplane.io
}

c_crossplane_objects() {
  show "for each Crossplane-related CRD: kubectl get <crd> -A -o wide   (kinds with no objects are counted, not printed)"
  local crd out empty=0 total=0
  while read -r crd; do
    [[ -n $crd ]] || continue
    total=$((total + 1))
    out=$(k get "$crd" -A -o wide 2>&1)
    if [[ -z $out || $out == "No resources found"* ]]; then empty=$((empty + 1)); else printf '\n## %s\n%s\n' "$crd" "$out"; fi
  done < <(cp_crds)
  printf '\nCRDs inspected: %s; with no objects: %s\n' "$total" "$empty"
}

c_crossplane_managed() {
  run k get managed
  run k get composite
  run k get claim -A
  need_jq || return 0
  show "managed resources: kind, name, external-name, deletionPolicy, providerConfig, connection secret (name only), conditions"
  k get managed -o json | jq -r '.items[] | [.kind, .metadata.name, "external-name=\(.metadata.annotations["crossplane.io/external-name"] // "-")", "deletionPolicy=\(.spec.deletionPolicy // "unset(Delete)")", "providerConfig=\(.spec.providerConfigRef.name // "-")", "connSecret=\(.spec.writeConnectionSecretToRef.namespace // "-")/\(.spec.writeConnectionSecretToRef.name // "-")", ([.status.conditions[]? | "\(.type)=\(.status)"] | join(","))] | @tsv'
}

c_crossplane_cluster() {
  show "cluster roles and bindings whose name contains crossplane"
  k get clusterroles,clusterrolebindings -o name 2>&1 | grep -i crossplane || echo "(none)"
  show "webhook configurations whose name contains crossplane"
  k get validatingwebhookconfigurations,mutatingwebhookconfigurations -o name 2>&1 | grep -i crossplane || echo "(none)"
  show "apiservices whose name contains crossplane"
  k get apiservices -o name 2>&1 | grep -i crossplane || echo "(none)"
}

# ---------------------------------------------------------------------------
# 09 Database
# ---------------------------------------------------------------------------
DB_RE='db|postgres|pgsql|mysql|maria|mongo|sql'
c_db_workloads() {
  show "kubectl get deploy,sts,svc,pods -A -o wide | grep -iE '$DB_RE'"
  k get deploy,sts,svc,pods -A -o wide 2>&1 | awk -v re="$DB_RE" '/^NAMESPACE/ || tolower($0) ~ re'
}

JQ_CONTAINERS='"  container \(.name) image=\(.image) command=\(.command // [] | tostring) args=\(.args // [] | tostring) ports=\([.ports[]?.containerPort] | tostring)", ((.env // [])[] | if .valueFrom then "    env \(.name) <- \(.valueFrom | tostring)" else "    env \(.name)=\(.value // "")" end), ((.envFrom // [])[] | "    envFrom \(tostring)")'
JQ_VOLUMES='"  volumes: \([.spec.template.spec.volumes[]? | (.name + "=" + (if .persistentVolumeClaim then "pvc:" + .persistentVolumeClaim.claimName elif .emptyDir then "emptyDir" elif .configMap then "configMap:" + .configMap.name elif .secret then "secretName:" + .secret.secretName else ((keys - ["name"]) | join(",")) end))] | join(" "))"'
c_db_specs() {
  need_jq || return 0
  show "Deployments and StatefulSets whose name matches $DB_RE: containers, image, command, env (literals redacted), volumes"
  k get deploy,sts -A -o json | jq -r --arg re "$DB_RE" \
    '.items[] | select(.metadata.name | test($re; "i")) | "=== \(.kind) \(.metadata.namespace)/\(.metadata.name) replicas=\(.spec.replicas) ready=\(.status.readyReplicas // 0)", (.spec.template.spec.containers[] | '"$JQ_CONTAINERS"'), '"$JQ_VOLUMES"
}

c_sampleapi_env() {
  need_jq || return 0
  show "sample-api Deployments: env (names; literals redacted), probes, ServiceAccount token mount"
  k get deploy -A -o json | jq -r \
    '.items[] | select(.metadata.name | test("sample-api")) | "=== \(.metadata.namespace)/\(.metadata.name)", (.spec.template.spec.containers[] | '"$JQ_CONTAINERS"', "    readinessProbe=\(.readinessProbe // {} | tostring)", "    livenessProbe=\(.livenessProbe // {} | tostring)"), "  automountServiceAccountToken=\(if (.spec.template.spec | has("automountServiceAccountToken")) then (.spec.template.spec.automountServiceAccountToken | tostring) else "unset" end)", '"$JQ_VOLUMES"
}

c_db_claims() {
  show "kubectl api-resources | grep -i postgresql"
  local r
  k api-resources --no-headers 2>&1 | awk 'tolower($0) ~ /postgresql/'
  while read -r r; do
    [[ -n $r ]] || continue
    run k get "$r" -A -o wide
    if have jq; then
      show "$r: conditions, resourceRef, connection secret (name only)"
      k get "$r" -A -o json | jq -r '.items[] | [.kind, "\(.metadata.namespace // "-")/\(.metadata.name)", "resourceRef=\(.spec.resourceRef // {} | tostring)", "connSecret=\(.spec.writeConnectionSecretToRef.name // "-")", ([.status.conditions[]? | "\(.type)=\(.status):\(.reason // "")"] | join(","))] | @tsv'
    fi
  done < <(k api-resources -o name 2>/dev/null | grep -i postgresql)
}

c_db_git() {
  run g ls-files apps/sample-api/infrastructure
  show "Secret-bearing files under apps/sample-api (path and reason only)"
  local f
  for f in "${!SB_REASON[@]}"; do [[ $f == apps/sample-api/* ]] && printf '%s  %s\n' "$f" "${SB_REASON[$f]}"; done
  show "git grep -n -I -i -E 'database|postgres|psycopg|sqlalchemy|asyncpg|DB_[A-Z]+|sample-db' -- apps/sample-api <exclusions>"
  g grep -n -I -i -E 'database|postgres|psycopg|sqlalchemy|asyncpg|DB_[A-Z]+|sample-db' -- apps/sample-api "${EXCLUDES[@]}"
  show "git grep -n --untracked -E 'infrastructure|sample-db' -- platform/argocd   (does any Application deploy it?)"
  g grep -n --untracked -E 'infrastructure|sample-db' -- platform/argocd "${EXCLUDES[@]}" || echo "(no Application references apps/sample-api/infrastructure)"
}

# ---------------------------------------------------------------------------
# 10 Network and storage
# ---------------------------------------------------------------------------
c_netpol() {
  run k get networkpolicies -A -o wide
  show "NetworkPolicy count per namespace; namespaces with none"
  local withnp
  withnp=$(k get networkpolicies -A --no-headers 2>/dev/null | awk '{print $1}' | sort | uniq -c)
  echo "${withnp:-(no NetworkPolicies in any namespace)}"
  comm -23 <(k get ns -o custom-columns=N:.metadata.name --no-headers 2>/dev/null | sort) \
           <(k get networkpolicies -A --no-headers 2>/dev/null | awk '{print $1}' | sort -u) | sed 's/^/no-netpol: /'
}

c_storage() {
  run k get pvc -A -o wide
  run k get pv -o wide
  run k get storageclass
}

c_cidrs() {
  run k get nodes -o go-template='{{range .items}}{{.metadata.name}} podCIDR={{.spec.podCIDR}} podCIDRs={{.spec.podCIDRs}} {{range .status.addresses}}{{.type}}={{.address}} {{end}}{{"\n"}}{{end}}'
  run k get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}{"\n"}'
  run k get svc kube-dns -n kube-system -o jsonpath='{.spec.clusterIP}{"\n"}'
  run k get servicecidrs.networking.k8s.io -o wide
  show "distinct pod IP /16 prefixes and service ClusterIP /16 prefixes"
  k get pods -A -o jsonpath='{range .items[*]}{.status.podIP}{"\n"}{end}' 2>/dev/null | awk -F. 'NF==4{print "pod "$1"."$2".0.0/16"}' | sort | uniq -c
  k get svc -A -o jsonpath='{range .items[*]}{.spec.clusterIP}{"\n"}{end}' 2>/dev/null | awk -F. 'NF==4{print "svc "$1"."$2".0.0/16"}' | sort | uniq -c
  need_jq || return 0
  show "node annotations: flannel and k3s (node-args redacted)"
  k get nodes -o json | jq -r '.items[] | .metadata.annotations | to_entries[] | select(.key | test("flannel|k3s\\.io/(node-args|internal-ip|external-ip)")) | "\(.key)=\(.value)"'
  show "network-related flags in node-args (cidr, network-policy, flannel, disable)"
  local flags
  flags=$(k get nodes -o json | jq -r '.items[].metadata.annotations["k3s.io/node-args"] // "[]" | fromjson | join(" ")' \
          | grep -oE -- '--(cluster-cidr|service-cidr|flannel-backend|disable-network-policy|disable|egress-selector-mode)([= ][^ ]*)?')
  echo "${flags:-(none of these flags set: k3s defaults apply)}"
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
printf '# capture-state index — %s — %s\n' "$TS" "$REPO_ROOT" > "$OUT/INDEX.txt"
printf '# Commands skipped because they need sudo. Run them yourself if you want the evidence.\n' > "$OUT/SUDO-REQUIRED.txt"

check 00-environment              "Environment and tools"                         c_env
check 01a-git-refs                "Git branch, remotes, refs, tags, origin"       c_git_refs
check 01b-git-log                 "Git last 20 commits"                           c_git_log
check 01c-git-status              "Git uncommitted and untracked, diff stats"     c_git_status
check 01d-git-diff                "Git diff (redacted, Secret-bearing excluded)"  c_git_diff
check 01e-git-untracked           "Git untracked files: size, lines, head"        c_git_untracked
check 01f-git-ci                  "CI workflow files"                             c_git_ci
check 01g-git-secret-files        "Secret-bearing files in Git (paths only)"      c_git_secret_files
check 01h-git-layout              "Tracked tree vs spec §25 layout"               c_git_layout
check 02a-k8s-version             "Kubernetes and k3s version, node info"         c_k8s_version
check 02b-node-resources          "Node CPU and memory"                           c_node_resources
check 02c-k3s-server-flags        "k3s server flags and audit flags"              c_k3s_flags
check 03a-namespaces              "Namespaces and labels"                         c_namespaces
check 03b-crds                    "CRDs"                                          c_crds
check 03c-helm-releases           "Helm releases, status, user values"            c_helm
check 03d-workloads               "Deployments, StatefulSets, DaemonSets"         c_workloads
check 03e-pods-unhealthy          "Pods not Running or not Ready"                 c_pods_unhealthy
while read -r ns pod restarts; do
  [[ -n ${pod:-} ]] || continue
  check "03f-logs-$ns-$pod"       "Logs of unhealthy pod $ns/$pod (≤200 lines)"   c_pod_logs "$ns" "$pod" "$restarts"
done < <(k get pods -A --no-headers 2>/dev/null | awk "$UNHEALTHY_AWK" | awk '{print $1, $2, $5}')
check 03g-events-warning          "Warning events (last 200)"                     c_events
check 03h-apiservices             "APIServices not Available"                     c_apiservices
check 04a-argocd-apps             "ArgoCD Applications"                           c_argocd_apps
check 04b-argocd-app-resources    "Resources tracked by each Application"         c_argocd_resources
check 04c-argocd-projects         "ArgoCD AppProjects"                            c_argocd_projects
check 04d-argocd-config           "ArgoCD version and config keys"                c_argocd_config
check 04e-argocd-git-vs-cluster   "Applications in Git vs cluster"                c_argocd_git_vs_cluster
check 05a-grafana-datasource-cms  "Grafana datasource ConfigMaps (live)"          c_grafana_ds_cms
check 05b-grafana-configmaps      "Grafana ConfigMaps (datasource keys)"          c_grafana_cms
check 05c-grafana-ds-secrets      "Grafana datasource Secrets (names only)"       c_grafana_ds_secrets
check 05d-grafana-workload        "Grafana workload and sidecar settings"         c_grafana_workload
check 05e-grafana-logs            "Grafana logs (≤200 lines each)"                c_grafana_logs
check 05f-grafana-isdefault       "isDefault: live and in Git"                    c_grafana_isdefault
check 06a-sample-api-deployed     "sample-api deployed image and digest"          c_sampleapi_deployed
check 06b-sample-api-git-digest   "sample-api digest pinned in Git"               c_sampleapi_git_digest
check 06c-sample-api-metrics      "sample-api /metrics via temporary port-forward" c_sampleapi_metrics
check 07a-kyverno-install         "Kyverno version and pods"                      c_kyverno_install
check 07b-kyverno-clusterpolicies "Kyverno ClusterPolicies and failure actions"   c_kyverno_cpol
check 07c-kyverno-other-policies  "Kyverno namespaced and CEL policy kinds"       c_kyverno_other
check 07d-webhooks                "Admission webhooks and failurePolicy"          c_webhooks
check 07e-kyverno-git             "Kyverno policies in Git"                       c_kyverno_git
check 08a-crossplane-loki-argocd  "Crossplane and Loki: Applications and Helm"    c_cl_argocd
check 08b-loki-objects            "Loki and Promtail objects"                     c_loki_objects
check 08c-crossplane-namespace    "crossplane-system contents"                    c_crossplane_ns
check 08d-crossplane-crds         "Crossplane CRDs, XRDs, Compositions"           c_crossplane_crds
check 08e-crossplane-objects      "Objects of every Crossplane CRD"               c_crossplane_objects
check 08f-crossplane-managed      "Managed, composite and claim resources"        c_crossplane_managed
check 08g-crossplane-cluster      "Crossplane cluster-scoped RBAC and webhooks"   c_crossplane_cluster
check 09a-db-workloads            "Database-like workloads"                       c_db_workloads
check 09b-db-specs                "Database workload specs"                       c_db_specs
check 09c-sample-api-env          "sample-api env and probes"                     c_sampleapi_env
check 09d-db-claims               "PostgreSQL claims (Crossplane)"                c_db_claims
check 09e-db-git                  "Database usage in Git"                         c_db_git
check 10a-networkpolicies         "NetworkPolicies per namespace"                 c_netpol
check 10b-storage                 "PVCs, PVs, StorageClasses"                     c_storage
check 10c-cidrs                   "Pod and service CIDRs"                         c_cidrs

if [[ $(wc -l < "$OUT/SUDO-REQUIRED.txt") -eq 1 ]]; then echo "(none)" >> "$OUT/SUDO-REQUIRED.txt"; fi

# Leak self-check (amendment A4): list files only, never the match.
if ! leak_check "$OUT"; then
  echo "capture-state: leak self-check FAILED, see $OUT/LEAK-CHECK.txt" >&2
  exit 3
fi

if (( BACKUP )); then
  BACKUP_ROOT=$HOME/nexus-backup
  mkdir -p -- "$BACKUP_ROOT" || exit 2
  chmod 0700 -- "$BACKUP_ROOT" || exit 2   # enforced even if the directory pre-existed looser
  cp -a -- "$OUT" "$BACKUP_ROOT/state-$TS" && echo "capture-state: snapshot copied to $BACKUP_ROOT/state-$TS"
fi

echo "capture-state: snapshot written to $OUT"
exit 0
