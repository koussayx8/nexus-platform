#!/usr/bin/env bash
# bootstrap.sh — empty k3s to full NEXUS platform (spec §25, ADR-014, ADR-019).
#
# This is a mutation script, categorically different from capture-state.sh/verify-state.sh: it
# does not source scripts/lib/readonly.sh, because almost everything it does is exactly what
# those wrappers exist to forbid. The Cluster Admin runs this directly (rule 6) — it is never
# invoked through an agent's tool.
#
# --plan prints every step's exact command, tagging the ones that need root [SUDO], and
# executes NOTHING: no git fetch, no kubectl call, no curl, no sudo. It is meant to be read
# before the real run.
#
# Order (ADR-019):
#   a. k3s (pinned version, §14 audit policy, audit-log pre-created for group-readable rotation)
#   b. kubeconfig
#   c. monitoring namespace + grafana-admin Secret
#   d. ArgoCD (pinned version)
#   e. merge-order guard, then the AppProject and the root Application
#   f. wait for the platform Application
#   g. nexus-killswitch and nexus-operator-config (created directly, never through ArgoCD)
#   h. wait for every Application
#   i. verify-state.sh
#
# Usage: scripts/bootstrap.sh [--plan]
# Exit codes: 0 success; 1 a step failed or refused to proceed; 2 script/argument error.

set -uo pipefail

PLAN=0
for a in "$@"; do
  case $a in
    --plan) PLAN=1 ;;
    *) echo "bootstrap: unknown argument $a" >&2; exit 2 ;;
  esac
done

# Resolved from the script's own location, not `git rev-parse` — --plan must call no external
# tool at all, including git, and this script always lives at <repo>/scripts/bootstrap.sh.
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) || { echo "bootstrap: cannot resolve the repository root" >&2; exit 2; }
cd "$REPO_ROOT" || exit 2

# ---------------------------------------------------------------------------------------
# Fixed configuration
# ---------------------------------------------------------------------------------------
K3S_VERSION=v1.34.6+k3s1
K3S_INSTALL_URL="https://raw.githubusercontent.com/k3s-io/k3s/v1.34.6%2Bk3s1/install.sh"
ARGOCD_VERSION=v3.3.8
ARGOCD_INSTALL_URL="https://raw.githubusercontent.com/argoproj/argo-cd/v3.3.8/manifests/install.yaml"
AUDIT_DIR=/var/log/nexus-audit
AUDIT_LOG=$AUDIT_DIR/audit.log
AUDIT_POLICY_FILE=/etc/rancher/k3s/audit-policy.yaml
K3S_CONFIG=/etc/rancher/k3s/config.yaml
NEXUS_DIR=$HOME/.nexus
# Confirmed by `helm template observability prometheus-community/kube-prometheus-stack ...`:
# release name "observability" + chart's own "-grafana" suffix (round 4).
GRAFANA_DEPLOYMENT=observability-grafana
EXPECTED_APPS=(root platform kyverno observability sample-api-dev sample-api-prod)

# ---------------------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------------------
step() {   # step <label> [SUDO]
  if [[ -n ${2:-} ]]; then printf '\n[%s] %s\n' "$2" "$1"; else printf '\n%s\n' "$1"; fi
}

# run_cmd <label> [SUDO|""] -- <command...>
# Prints the label and the exact command, then runs it unless --plan is set. Single source of
# truth: what --plan prints is exactly what would run for real, never a separately maintained copy.
run_cmd() {
  local label=$1 tag=$2
  shift 2
  [[ ${1:-} == -- ]] && shift
  step "$label" "$tag"
  printf '  $ %s\n' "$*"
  (( PLAN )) && return 0
  "$@"
}

fatal() { echo "bootstrap: FATAL — $*" >&2; exit 1; }

# ---------------------------------------------------------------------------------------
# a. k3s
# ---------------------------------------------------------------------------------------
AUDIT_POLICY_YAML='apiVersion: audit.k8s.io/v1
kind: Policy
omitStages:
  - RequestReceived
rules:
  # 1. nexus-operator on the two mutating subresources and the Incident CRD (spec §14 rule 1)
  - level: RequestResponse
    users: ["system:serviceaccount:nexus-system:nexus-operator"]
    resources:
      - group: apps
        resources: ["deployments/scale"]
      - group: ""
        resources: ["pods/eviction"]
      - group: nexus.io
        resources: ["incidents", "incidents/status"]
  # 2. group nexus-approvers on incidents (spec §14 rule 2)
  - level: RequestResponse
    userGroups: ["nexus-approvers"]
    resources:
      - group: nexus.io
        resources: ["incidents"]
  # 3. any write by any identity (spec §14 rule 3)
  - level: Metadata
    verbs: ["create", "update", "patch", "delete", "deletecollection"]
  # 4. reads, watches, health checks (spec §14 rule 4)
  - level: None
'

K3S_CONFIG_YAML="disable:
  - traefik
  - servicelb
kube-apiserver-arg:
  - audit-policy-file=$AUDIT_POLICY_FILE
  - audit-log-path=$AUDIT_LOG
  - audit-log-maxage=30
  - audit-log-maxbackup=10
  - audit-log-maxsize=100
"

step_a_k3s() {
  step "k3s: refuse to proceed if already installed (ADR-014: uninstall runs before bootstrap)"
  printf '  $ command -v k3s || test -e /etc/systemd/system/k3s.service\n'
  if (( ! PLAN )); then
    if command -v k3s >/dev/null 2>&1 || [[ -e /etc/systemd/system/k3s.service ]]; then
      fatal "k3s is already installed — run the uninstall first"
    fi
  fi

  step "k3s: write the §14 audit policy" SUDO
  printf '  $ sudo install -d -m 0755 %s\n' "$(dirname "$AUDIT_POLICY_FILE")"
  printf '  $ sudo tee %s <<'"'"'YAML'"'"'\n' "$AUDIT_POLICY_FILE"
  printf '%s' "$AUDIT_POLICY_YAML" | sed 's/^/  /'
  printf '  YAML\n'
  if (( ! PLAN )); then
    sudo install -d -m 0755 "$(dirname "$AUDIT_POLICY_FILE")"
    printf '%s' "$AUDIT_POLICY_YAML" | sudo tee "$AUDIT_POLICY_FILE" >/dev/null
  fi

  step "k3s: pre-create the audit log — group-readable mode survives every rotation (ADR-019)" SUDO
  printf '  $ sudo install -d -m 0750 -o root -g adm %s\n' "$AUDIT_DIR"
  printf '  $ sudo install -m 0640 -o root -g adm /dev/null %s\n' "$AUDIT_LOG"
  if (( ! PLAN )); then
    sudo install -d -m 0750 -o root -g adm "$AUDIT_DIR"
    sudo install -m 0640 -o root -g adm /dev/null "$AUDIT_LOG"
  fi

  step "k3s: write the apiserver config (disabled addons, audit flags) to config.yaml, not INSTALL_K3S_EXEC — editable later without reinstalling" SUDO
  printf '  $ sudo install -d -m 0755 %s\n' "$(dirname "$K3S_CONFIG")"
  printf '  $ sudo tee %s <<'"'"'YAML'"'"'\n' "$K3S_CONFIG"
  printf '%s' "$K3S_CONFIG_YAML" | sed 's/^/  /'
  printf '  YAML\n'
  if (( ! PLAN )); then
    sudo install -d -m 0755 "$(dirname "$K3S_CONFIG")"
    printf '%s' "$K3S_CONFIG_YAML" | sudo tee "$K3S_CONFIG" >/dev/null
  fi

  if [[ -f $NEXUS_DIR/dockerhub.env ]]; then
    step "k3s: optional docker.io mirror auth — ~/.nexus/dockerhub.env is present" SUDO
    printf '  $ sudo install -d -m 0755 /etc/rancher/k3s\n'
    printf '  $ sudo tee /etc/rancher/k3s/registries.yaml <<YAML   (then chmod 0600)\n'
    printf '  configs:\n    "docker.io":\n      auth:\n        username: $DOCKERHUB_USER\n        password: <REDACTED>\n  YAML\n'
    if (( ! PLAN )); then
      # shellcheck disable=SC1091
      source "$NEXUS_DIR/dockerhub.env"
      : "${DOCKERHUB_USER:?dockerhub.env must set DOCKERHUB_USER}" "${DOCKERHUB_TOKEN:?dockerhub.env must set DOCKERHUB_TOKEN}"
      sudo install -d -m 0755 /etc/rancher/k3s
      umask 077
      printf 'configs:\n  "docker.io":\n    auth:\n      username: %s\n      password: %s\n' \
        "$DOCKERHUB_USER" "$DOCKERHUB_TOKEN" | sudo tee /etc/rancher/k3s/registries.yaml >/dev/null
      sudo chmod 0600 /etc/rancher/k3s/registries.yaml
    fi
  fi

  run_cmd "k3s: install $K3S_VERSION, installer pinned to the same tag (not get.k3s.io)" SUDO -- \
    bash -c "curl -fsSL '$K3S_INSTALL_URL' | INSTALL_K3S_VERSION='$K3S_VERSION' sudo sh -"
}

# ---------------------------------------------------------------------------------------
# b. kubeconfig
# ---------------------------------------------------------------------------------------
step_b_kubeconfig() {
  step "kubeconfig: back up the existing one, if present"
  printf '  $ test -f ~/.kube/config && cp -a ~/.kube/config ~/.kube/config.bak-$(date -u +%%Y%%m%%dT%%H%%M%%SZ)\n'
  if (( ! PLAN )) && [[ -f $HOME/.kube/config ]]; then
    cp -a "$HOME/.kube/config" "$HOME/.kube/config.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  fi

  run_cmd "kubeconfig: copy from k3s, owned by this user, mode 0600" SUDO -- \
    sudo install -m 0600 -o "$USER" -g "$(id -gn)" /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
}

# ---------------------------------------------------------------------------------------
# c. monitoring namespace and grafana-admin
# ---------------------------------------------------------------------------------------
step_c_monitoring() {
  run_cmd "monitoring: create the namespace (idempotent; 'already exists' on a rerun is harmless)" "" -- \
    kubectl create namespace monitoring

  step "grafana-admin: generate-or-reuse ~/.nexus/grafana-admin (0600, no trailing newline, umask 077)"
  printf '  $ install -d -m 0700 %s   # if missing\n' "$NEXUS_DIR"
  printf "  \$ test -f %s/grafana-admin || (umask 077; python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' > %s/grafana-admin)\n" "$NEXUS_DIR" "$NEXUS_DIR"

  local fresh=0
  if (( ! PLAN )); then
    [[ -d $NEXUS_DIR ]] || install -d -m 0700 "$NEXUS_DIR"
    if [[ ! -f $NEXUS_DIR/grafana-admin ]]; then
      ( umask 077; python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' > "$NEXUS_DIR/grafana-admin" )
      fresh=1
    fi
  fi

  step "grafana-admin: create the Secret if absent; delete+recreate only if the value is freshly generated; never kubectl apply"
  printf '  $ kubectl get secret grafana-admin -n monitoring\n'
  printf '  $ # absent -> kubectl create secret generic grafana-admin -n monitoring --from-literal=admin-user=admin --from-file=admin-password=%s/grafana-admin\n' "$NEXUS_DIR"
  printf '  $ # present AND value just (re)generated -> kubectl delete secret grafana-admin -n monitoring --ignore-not-found; then the create above; then kubectl rollout restart deployment/%s -n monitoring\n' "$GRAFANA_DEPLOYMENT"
  printf '  $ # present AND value reused unchanged -> no-op\n'
  if (( ! PLAN )); then
    if kubectl get secret grafana-admin -n monitoring >/dev/null 2>&1; then
      if (( fresh )); then
        kubectl delete secret grafana-admin -n monitoring --ignore-not-found
        kubectl create secret generic grafana-admin -n monitoring \
          --from-literal=admin-user=admin --from-file=admin-password="$NEXUS_DIR/grafana-admin"
        # Grafana's DB here is not persisted (chart default, unoverridden in our values — round 3
        # point 1): a restart alone re-bootstraps the admin user against the new password.
        kubectl rollout restart deployment/"$GRAFANA_DEPLOYMENT" -n monitoring
      fi
      # else: reused unchanged, touch nothing
    else
      kubectl create secret generic grafana-admin -n monitoring \
        --from-literal=admin-user=admin --from-file=admin-password="$NEXUS_DIR/grafana-admin"
    fi
  fi
}

# ---------------------------------------------------------------------------------------
# d. ArgoCD
# ---------------------------------------------------------------------------------------
step_d_argocd() {
  run_cmd "ArgoCD: create the namespace (idempotent; 'already exists' on a rerun is harmless)" "" -- \
    kubectl create namespace argocd

  run_cmd "ArgoCD: install $ARGOCD_VERSION with server-side apply (its manifest exceeds the client-side annotation limit)" "" -- \
    kubectl apply --server-side --force-conflicts -n argocd -f "$ARGOCD_INSTALL_URL"

  run_cmd "ArgoCD: wait for the server to roll out" "" -- \
    kubectl -n argocd rollout status deployment/argocd-server --timeout=180s

  step "argocd-admin: read the fresh initial password to a temp file, then move it into place; a failed or empty read is fatal"
  printf '  $ umask 077; kubectl -n argocd get secret argocd-initial-admin-secret -o go-template=... > %s/.argocd-admin.tmp; test -s <tmp> || FATAL; mv <tmp> %s/argocd-admin; chmod 0600\n' "$NEXUS_DIR" "$NEXUS_DIR"
  if (( ! PLAN )); then
    [[ -d $NEXUS_DIR ]] || install -d -m 0700 "$NEXUS_DIR"
    local tmp
    tmp="$NEXUS_DIR/.argocd-admin.tmp.$$"
    trap 'rm -f "$tmp"' EXIT
    ( umask 077
      kubectl -n argocd get secret argocd-initial-admin-secret \
        -o go-template='{{.data.password | base64decode}}' > "$tmp" )
    if [[ ! -s $tmp ]]; then
      fatal "could not read a fresh ArgoCD admin password from argocd-initial-admin-secret"
    fi
    mv "$tmp" "$NEXUS_DIR/argocd-admin"
    chmod 0600 "$NEXUS_DIR/argocd-admin"
    trap - EXIT
  fi
}

# ---------------------------------------------------------------------------------------
# e. merge-order guard, then the AppProject and the root Application
# ---------------------------------------------------------------------------------------
MARKER_PATHS=(
  platform/argocd/root.yaml
  platform/argocd/projects/nexus.yaml
  platform/kyverno/values.yaml
  platform/bootstrap-templates/nexus-killswitch.yaml
)

step_e_root_application() {
  step "merge-order guard: origin/main must already contain the M0-4/M0-5 convergence, and origin/experiment/dev-state must already contain origin/main"
  printf '  $ git fetch origin main experiment/dev-state\n'
  local p
  for p in "${MARKER_PATHS[@]}"; do printf '  $ git cat-file -e origin/main:%s\n' "$p"; done
  printf '  $ git merge-base --is-ancestor origin/main origin/experiment/dev-state\n'

  if (( PLAN )); then
    step "root Application: apply the AppProject, then root.yaml, both read from origin/main"
    printf '  $ git show origin/main:platform/argocd/projects/nexus.yaml | kubectl apply --server-side -f -\n'
    printf '  $ git show origin/main:platform/argocd/root.yaml           | kubectl apply --server-side -f -\n'
    return 0
  fi

  git fetch origin main experiment/dev-state
  for p in "${MARKER_PATHS[@]}"; do
    git cat-file -e "origin/main:$p" 2>/dev/null || fatal "origin/main does not yet contain $p — merge dev into main first"
  done
  git merge-base --is-ancestor origin/main origin/experiment/dev-state \
    || fatal "origin/main is not yet merged into origin/experiment/dev-state"

  step "root Application: apply the AppProject, then root.yaml, both read from origin/main"
  printf '  $ git show origin/main:platform/argocd/projects/nexus.yaml | kubectl apply --server-side -f -\n'
  git show origin/main:platform/argocd/projects/nexus.yaml | kubectl apply --server-side -f -
  printf '  $ git show origin/main:platform/argocd/root.yaml           | kubectl apply --server-side -f -\n'
  git show origin/main:platform/argocd/root.yaml | kubectl apply --server-side -f -
}

# ---------------------------------------------------------------------------------------
# f. wait for the platform Application (owns nexus-system, needed before step g)
# ---------------------------------------------------------------------------------------
wait_for_app() {   # wait_for_app <name> <timeout-seconds>
  local name=$1 timeout=$2 waited=0
  while (( waited < timeout )); do
    local sync health
    sync=$(kubectl get "applications.argoproj.io/$name" -n argocd -o jsonpath='{.status.sync.status}' 2>/dev/null)
    health=$(kubectl get "applications.argoproj.io/$name" -n argocd -o jsonpath='{.status.health.status}' 2>/dev/null)
    [[ $sync == Synced && $health == Healthy ]] && return 0
    sleep 5; waited=$((waited + 5))
  done
  return 1
}

step_f_wait_platform() {
  step "wait for the platform Application to be Synced/Healthy (timeout 300s) — it owns nexus-system, needed before the Kill Switch ConfigMaps can be created"
  printf '  $ kubectl get application platform -n argocd -o jsonpath=... (polled every 5s, 300s timeout)\n'
  (( PLAN )) && return 0
  wait_for_app platform 300 || fatal "platform Application did not reach Synced/Healthy within 300s"
}

# ---------------------------------------------------------------------------------------
# g. nexus-killswitch and nexus-operator-config (create-only, direct, never through ArgoCD)
# ---------------------------------------------------------------------------------------
step_g_killswitch() {
  local name
  for name in nexus-killswitch nexus-operator-config; do
    step "$name: create only if absent, read from origin/main (excluded from ArgoCD — spec line 924)"
    printf '  $ kubectl get configmap %s -n nexus-system || git show origin/main:platform/bootstrap-templates/%s.yaml | kubectl create -f -\n' "$name" "$name"
    (( PLAN )) && continue
    if ! kubectl get configmap "$name" -n nexus-system >/dev/null 2>&1; then
      git show "origin/main:platform/bootstrap-templates/$name.yaml" | kubectl create -f -
    fi
  done
}

# ---------------------------------------------------------------------------------------
# h. wait for every Application
# ---------------------------------------------------------------------------------------
step_h_wait_all() {
  step "wait for every Application to be Synced/Healthy (timeout 300s each)"
  local name
  for name in "${EXPECTED_APPS[@]}"; do
    printf '  $ kubectl get application %s -n argocd -o jsonpath=... (polled every 5s, 300s timeout)\n' "$name"
  done
  (( PLAN )) && return 0
  for name in "${EXPECTED_APPS[@]}"; do
    wait_for_app "$name" 300 || fatal "Application $name did not reach Synced/Healthy within 300s"
  done
}

# ---------------------------------------------------------------------------------------
# i. verify-state.sh
# ---------------------------------------------------------------------------------------
step_i_verify() {
  run_cmd "run verify-state.sh (writes docs/CURRENT_STATE.md, non-zero exit on any failure)" "" -- \
    ./scripts/verify-state.sh
}

# ---------------------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------------------
if (( PLAN )); then
  echo "bootstrap.sh --plan — printing every step; nothing below is executed."
fi

step_a_k3s
step_b_kubeconfig
step_c_monitoring
step_d_argocd
step_e_root_application
step_f_wait_platform
step_g_killswitch
step_h_wait_all
step_i_verify

echo
echo "bootstrap: done"
