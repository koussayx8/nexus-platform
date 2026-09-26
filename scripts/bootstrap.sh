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
# before the real run. Verified by stubbing every external tool it calls and confirming the
# stub log stays empty (see PR #53's review).
#
# Order (ADR-019):
#   0. merge-order guard (preflight — before touching anything, and re-checked before step e)
#   a. k3s (pinned version, §14 audit policy, audit-log pre-created for group-readable rotation)
#   b. kubeconfig
#   c. monitoring namespace + grafana-admin Secret
#   d. ArgoCD (pinned version)
#   e. merge-order guard (again), then the AppProject and the root Application
#   f. wait for the platform Application
#   g. nexus-killswitch and nexus-operator-config (created directly, never through ArgoCD)
#   h. wait for every Application
#   i. verify-state.sh
#
# Usage: scripts/bootstrap.sh [--plan]
# Timeout overrides: NEXUS_WAIT_TIMEOUT_DEFAULT (default 300), NEXUS_WAIT_TIMEOUT_OBSERVABILITY
# (default 600), or NEXUS_WAIT_TIMEOUT_<NAME> for any specific Application.
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
# release name "observability" + chart's own "-grafana" suffix (round 4). The chart's grafana.admin
# keys (existingSecret: grafana-admin, userKey: admin-user, passwordKey: admin-password —
# platform/observability/kube-prometheus-stack-values.yaml:78-81) are exactly the keys the Secret
# below is created with.
GRAFANA_DEPLOYMENT=observability-grafana
EXPECTED_APPS=(root platform kyverno observability sample-api-dev sample-api-prod)
MARKER_PATHS=(
  platform/argocd/root.yaml
  platform/argocd/projects/nexus.yaml
  platform/kyverno/values.yaml
  platform/bootstrap-templates/nexus-killswitch.yaml
)

WAIT_TIMEOUT_DEFAULT=${NEXUS_WAIT_TIMEOUT_DEFAULT:-300}
WAIT_TIMEOUT_OBSERVABILITY=${NEXUS_WAIT_TIMEOUT_OBSERVABILITY:-600}
timeout_for_app() {   # timeout_for_app <name> -> echoes the resolved timeout in seconds
  local name=$1
  local var=NEXUS_WAIT_TIMEOUT_${name^^}
  var=${var//-/_}
  if [[ -n ${!var:-} ]]; then echo "${!var}"; return; fi
  case $name in
    observability) echo "$WAIT_TIMEOUT_OBSERVABILITY" ;;
    *) echo "$WAIT_TIMEOUT_DEFAULT" ;;
  esac
}

# ---------------------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------------------
step() {   # step <label> [SUDO]
  if [[ -n ${2:-} ]]; then printf '\n[%s] %s\n' "$2" "$1"; else printf '\n%s\n' "$1"; fi
}

fatal() { echo "bootstrap: FATAL — $*" >&2; exit 1; }

# run_cmd <label> [SUDO|""] -- <command...>
# Prints the label and the exact command, then runs it unless --plan is set. Single source of
# truth: what --plan prints is exactly what would run for real, never a separately maintained copy.
# Only for single, non-piped commands — anything with a pipe or shared state is written out by
# hand instead, so the printed text and the executed text can never drift apart.
#
# A failing command is fatal, immediately, named by its label — this function does not merely
# propagate an exit code for some caller to remember to check (nothing in this script relied on
# `set -e`, whose semantics are suspended inside any if/while/&&/|| condition anyway, which is
# exactly where several of this script's real command invocations live).
run_cmd() {
  local label=$1 tag=$2
  shift 2
  [[ ${1:-} == -- ]] && shift
  step "$label" "$tag"
  printf '  $ %s\n' "$*"
  (( PLAN )) && return 0
  "$@" || fatal "$label (exit $?)"
}

# ---------------------------------------------------------------------------------------
# 0/e. merge-order guard — run as a preflight before step a, and again right before applying
# the AppProject/root Application (step e), in case time passed between the two.
# ---------------------------------------------------------------------------------------
merge_order_guard() {   # merge_order_guard <context label>
  local context=$1 p
  step "merge-order guard ($context): origin/main must already contain the M0-4/M0-5 convergence, and origin/experiment/dev-state must already contain origin/main"
  printf '  $ git fetch origin main experiment/dev-state\n'
  for p in "${MARKER_PATHS[@]}"; do printf '  $ git cat-file -e origin/main:%s\n' "$p"; done
  printf '  $ git merge-base --is-ancestor origin/main origin/experiment/dev-state\n'
  (( PLAN )) && return 0

  git fetch origin main experiment/dev-state
  for p in "${MARKER_PATHS[@]}"; do
    git cat-file -e "origin/main:$p" 2>/dev/null || fatal "origin/main does not yet contain $p — merge dev into main first"
  done
  git merge-base --is-ancestor origin/main origin/experiment/dev-state \
    || fatal "origin/main is not yet merged into origin/experiment/dev-state"
}

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
    sudo install -d -m 0755 "$(dirname "$AUDIT_POLICY_FILE")" || fatal "creating $(dirname "$AUDIT_POLICY_FILE") failed"
    printf '%s' "$AUDIT_POLICY_YAML" | sudo tee "$AUDIT_POLICY_FILE" >/dev/null || fatal "writing $AUDIT_POLICY_FILE failed"
  fi

  step "k3s: pre-create the audit log — group-readable mode survives every rotation (ADR-019)" SUDO
  printf '  $ sudo install -d -m 0750 -o root -g adm %s\n' "$AUDIT_DIR"
  printf '  $ sudo install -m 0640 -o root -g adm /dev/null %s\n' "$AUDIT_LOG"
  if (( ! PLAN )); then
    sudo install -d -m 0750 -o root -g adm "$AUDIT_DIR" || fatal "creating $AUDIT_DIR failed"
    sudo install -m 0640 -o root -g adm /dev/null "$AUDIT_LOG" || fatal "pre-creating $AUDIT_LOG failed"
  fi

  step "k3s: write the apiserver config (disabled addons, audit flags) to config.yaml, not INSTALL_K3S_EXEC — editable later without reinstalling" SUDO
  printf '  $ sudo install -d -m 0755 %s\n' "$(dirname "$K3S_CONFIG")"
  printf '  $ sudo tee %s <<'"'"'YAML'"'"'\n' "$K3S_CONFIG"
  printf '%s' "$K3S_CONFIG_YAML" | sed 's/^/  /'
  printf '  YAML\n'
  if (( ! PLAN )); then
    sudo install -d -m 0755 "$(dirname "$K3S_CONFIG")" || fatal "creating $(dirname "$K3S_CONFIG") failed"
    printf '%s' "$K3S_CONFIG_YAML" | sudo tee "$K3S_CONFIG" >/dev/null || fatal "writing $K3S_CONFIG failed"
  fi

  step "k3s: optional docker.io mirror auth" SUDO
  if [[ -f $NEXUS_DIR/dockerhub.env ]]; then
    printf '  present -> ~/.nexus/dockerhub.env\n'
    printf '  $ sudo install -d -m 0755 /etc/rancher/k3s\n'
    printf '  $ sudo tee /etc/rancher/k3s/registries.yaml <<YAML   (then chmod 0600)\n'
    printf '  configs:\n    "docker.io":\n      auth:\n        username: $DOCKERHUB_USER\n        password: <REDACTED>\n  YAML\n'
    if (( ! PLAN )); then
      # shellcheck disable=SC1091
      source "$NEXUS_DIR/dockerhub.env"
      : "${DOCKERHUB_USER:?dockerhub.env must set DOCKERHUB_USER}" "${DOCKERHUB_TOKEN:?dockerhub.env must set DOCKERHUB_TOKEN}"
      sudo install -d -m 0755 /etc/rancher/k3s || fatal "creating /etc/rancher/k3s failed"
      umask 077
      printf 'configs:\n  "docker.io":\n    auth:\n      username: %s\n      password: %s\n' \
        "$DOCKERHUB_USER" "$DOCKERHUB_TOKEN" | sudo tee /etc/rancher/k3s/registries.yaml >/dev/null \
        || fatal "writing /etc/rancher/k3s/registries.yaml failed"
      sudo chmod 0600 /etc/rancher/k3s/registries.yaml || fatal "chmod on registries.yaml failed"
    fi
  else
    printf '  (skipped: ~/.nexus/dockerhub.env absent)\n'
  fi

  # Download first, run second — never pipe curl into sh. And never `VAR=... sudo cmd`: sudo does
  # not propagate an env var set that way unless the invoking user's sudoers config explicitly
  # keeps it (INSTALL_K3S_VERSION isn't a standard env_keep entry, so it would be silently dropped
  # and the pin would not actually apply). install.sh is designed to be run as the normal user —
  # it escalates via sudo itself for the specific steps that need root.
  step "k3s: install $K3S_VERSION — download the pinned tag's install.sh, then run it (no pipe into sh)" SUDO
  printf '  $ tmp=$(mktemp)\n'
  printf '  $ curl -fsSL -o "$tmp" %s\n' "$K3S_INSTALL_URL"
  printf '  $ INSTALL_K3S_VERSION=%s sh "$tmp"   # install.sh escalates via sudo itself\n' "$K3S_VERSION"
  printf '  $ k3s --version   # must report %s, or this is fatal\n' "$K3S_VERSION"
  printf '  $ rm -f "$tmp"\n'
  if (( ! PLAN )); then
    local tmp
    tmp=$(mktemp) || fatal "mktemp failed for the install.sh download target"
    trap 'rm -f "$tmp"' RETURN
    curl -fsSL -o "$tmp" "$K3S_INSTALL_URL" || fatal "downloading install.sh from $K3S_INSTALL_URL failed"
    INSTALL_K3S_VERSION="$K3S_VERSION" sh "$tmp" || fatal "k3s install.sh failed"
    k3s --version 2>/dev/null | grep -qF "$K3S_VERSION" \
      || fatal "k3s --version does not report $K3S_VERSION after install"
  fi
}

# ---------------------------------------------------------------------------------------
# b. kubeconfig
# ---------------------------------------------------------------------------------------
step_b_kubeconfig() {
  step "kubeconfig: back up the existing one, if present"
  printf '  $ test -f ~/.kube/config && cp -a ~/.kube/config ~/.kube/config.bak-$(date -u +%%Y%%m%%dT%%H%%M%%SZ)\n'
  if (( ! PLAN )) && [[ -f $HOME/.kube/config ]]; then
    # A failed defensive backup is a warning, not fatal: it doesn't block correctness, and the
    # step immediately after this overwrites the same file anyway.
    cp -a "$HOME/.kube/config" "$HOME/.kube/config.bak-$(date -u +%Y%m%dT%H%M%SZ)" \
      || echo "bootstrap: WARNING — could not back up the existing ~/.kube/config, continuing" >&2
  fi

  run_cmd "kubeconfig: create ~/.kube at 0700 as this user, before the config file is installed into it" "" -- \
    install -d -m 0700 "$HOME/.kube"

  run_cmd "kubeconfig: copy from k3s, owned by this user, mode 0600" SUDO -- \
    sudo install -m 0600 -o "$USER" -g "$(id -gn)" /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
}

# ---------------------------------------------------------------------------------------
# c. monitoring namespace and grafana-admin
# ---------------------------------------------------------------------------------------
step_c_monitoring() {
  step "monitoring: create the namespace (idempotent)"
  printf '  $ kubectl get namespace monitoring >/dev/null 2>&1 || kubectl create namespace monitoring\n'
  if (( ! PLAN )); then
    kubectl get namespace monitoring >/dev/null 2>&1 \
      || kubectl create namespace monitoring || fatal "creating namespace monitoring failed"
  fi

  step "grafana-admin: generate-or-reuse ~/.nexus/grafana-admin (0600, no trailing newline, umask 077)"
  printf '  $ install -d -m 0700 %s   # if missing\n' "$NEXUS_DIR"
  printf "  \$ test -f %s/grafana-admin || (umask 077; python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' > %s/grafana-admin)\n" "$NEXUS_DIR" "$NEXUS_DIR"

  local fresh=0
  if (( ! PLAN )); then
    [[ -d $NEXUS_DIR ]] || install -d -m 0700 "$NEXUS_DIR" || fatal "creating $NEXUS_DIR failed"
    if [[ ! -f $NEXUS_DIR/grafana-admin ]]; then
      ( umask 077; python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' > "$NEXUS_DIR/grafana-admin" ) \
        || fatal "generating grafana-admin failed"
      [[ -s $NEXUS_DIR/grafana-admin ]] || fatal "grafana-admin was generated empty"
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
        kubectl delete secret grafana-admin -n monitoring --ignore-not-found \
          || fatal "deleting the old grafana-admin Secret failed"
        kubectl create secret generic grafana-admin -n monitoring \
          --from-literal=admin-user=admin --from-file=admin-password="$NEXUS_DIR/grafana-admin" \
          || fatal "creating the rotated grafana-admin Secret failed"
        # Grafana's DB here is not persisted (chart default, unoverridden in our values — round 3
        # point 1): a restart alone re-bootstraps the admin user against the new password.
        kubectl rollout restart deployment/"$GRAFANA_DEPLOYMENT" -n monitoring \
          || fatal "restarting $GRAFANA_DEPLOYMENT after rotating grafana-admin failed"
      fi
      # else: reused unchanged, touch nothing
    else
      kubectl create secret generic grafana-admin -n monitoring \
        --from-literal=admin-user=admin --from-file=admin-password="$NEXUS_DIR/grafana-admin" \
        || fatal "creating the grafana-admin Secret failed"
    fi
  fi
}

# ---------------------------------------------------------------------------------------
# d. ArgoCD
# ---------------------------------------------------------------------------------------
step_d_argocd() {
  step "ArgoCD: create the namespace (idempotent)"
  printf '  $ kubectl get namespace argocd >/dev/null 2>&1 || kubectl create namespace argocd\n'
  if (( ! PLAN )); then
    kubectl get namespace argocd >/dev/null 2>&1 \
      || kubectl create namespace argocd || fatal "creating namespace argocd failed"
  fi

  run_cmd "ArgoCD: install $ARGOCD_VERSION with server-side apply (its manifest exceeds the client-side annotation limit)" "" -- \
    kubectl apply --server-side --force-conflicts -n argocd -f "$ARGOCD_INSTALL_URL"

  step "ArgoCD: wait for the server, the application controller and the repo server to roll out"
  printf '  $ kubectl -n argocd rollout status deployment/argocd-server --timeout=180s\n'
  printf '  $ kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=180s\n'
  printf '  $ kubectl -n argocd rollout status deployment/argocd-repo-server --timeout=180s\n'
  if (( ! PLAN )); then
    kubectl -n argocd rollout status deployment/argocd-server --timeout=180s \
      || fatal "argocd-server did not roll out"
    kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=180s \
      || fatal "argocd-application-controller did not roll out"
    kubectl -n argocd rollout status deployment/argocd-repo-server --timeout=180s \
      || fatal "argocd-repo-server did not roll out"
  fi

  step "argocd-admin: poll for argocd-initial-admin-secret (timeout 120s), then read it to a temp file and move it into place; a failed or empty read is fatal"
  printf '  $ for up to 120s: kubectl -n argocd get secret argocd-initial-admin-secret\n'
  printf '  $ umask 077; kubectl -n argocd get secret argocd-initial-admin-secret -o go-template=... > %s/.argocd-admin.tmp; test -s <tmp> || FATAL; mv <tmp> %s/argocd-admin; chmod 0600\n' "$NEXUS_DIR" "$NEXUS_DIR"
  if (( ! PLAN )); then
    local waited=0
    while (( waited < 120 )); do
      kubectl -n argocd get secret argocd-initial-admin-secret >/dev/null 2>&1 && break
      sleep 5; waited=$((waited + 5))
    done
    (( waited >= 120 )) && fatal "argocd-initial-admin-secret did not appear within 120s"

    [[ -d $NEXUS_DIR ]] || install -d -m 0700 "$NEXUS_DIR" || fatal "creating $NEXUS_DIR failed"
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
# e. the AppProject and the root Application (merge-order guard re-checked first)
# ---------------------------------------------------------------------------------------
step_e_root_application() {
  merge_order_guard "before applying the AppProject/root Application"

  step "root Application: apply the AppProject, then root.yaml, both read from origin/main"
  printf '  $ git show origin/main:platform/argocd/projects/nexus.yaml | kubectl apply --server-side -f -\n'
  printf '  $ git show origin/main:platform/argocd/root.yaml           | kubectl apply --server-side -f -\n'
  (( PLAN )) && return 0

  git show origin/main:platform/argocd/projects/nexus.yaml | kubectl apply --server-side -f - \
    || fatal "applying the AppProject from origin/main failed"
  git show origin/main:platform/argocd/root.yaml | kubectl apply --server-side -f - \
    || fatal "applying root.yaml from origin/main failed"
}

# ---------------------------------------------------------------------------------------
# f. wait for the platform Application (owns nexus-system, needed before step g)
# ---------------------------------------------------------------------------------------
wait_for_app() {   # wait_for_app <name> <timeout-seconds>
  local name=$1 timeout=$2 waited=0 sync='' health=''
  while (( waited < timeout )); do
    sync=$(kubectl get "applications.argoproj.io/$name" -n argocd -o jsonpath='{.status.sync.status}' 2>/dev/null)
    health=$(kubectl get "applications.argoproj.io/$name" -n argocd -o jsonpath='{.status.health.status}' 2>/dev/null)
    [[ $sync == Synced && $health == Healthy ]] && return 0
    sleep 5; waited=$((waited + 5))
  done
  echo "bootstrap: $name did not reach Synced/Healthy within ${timeout}s — last status: sync=${sync:-<none>} health=${health:-<none>}" >&2
  echo "bootstrap: $name .status.conditions:" >&2
  kubectl get "applications.argoproj.io/$name" -n argocd -o jsonpath='{.status.conditions}' 2>&1 >&2
  echo >&2
  return 1
}

step_f_wait_platform() {
  local t
  t=$(timeout_for_app platform)
  step "wait for the platform Application to be Synced/Healthy (timeout ${t}s) — it owns nexus-system, needed before the Kill Switch ConfigMaps can be created"
  printf '  $ kubectl get application platform -n argocd -o jsonpath=... (polled every 5s, %ss timeout)\n' "$t"
  (( PLAN )) && return 0
  wait_for_app platform "$t" || fatal "platform Application did not reach Synced/Healthy within ${t}s"
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
      git show "origin/main:platform/bootstrap-templates/$name.yaml" | kubectl create -f - \
        || fatal "creating ConfigMap $name failed"
    fi
  done
}

# ---------------------------------------------------------------------------------------
# h. wait for every Application
# ---------------------------------------------------------------------------------------
step_h_wait_all() {
  step "wait for every Application to be Synced/Healthy (timeout configurable per app; observability defaults to ${WAIT_TIMEOUT_OBSERVABILITY}s, others to ${WAIT_TIMEOUT_DEFAULT}s)"
  local name t
  for name in "${EXPECTED_APPS[@]}"; do
    t=$(timeout_for_app "$name")
    printf '  $ kubectl get application %s -n argocd -o jsonpath=... (polled every 5s, %ss timeout)\n' "$name" "$t"
  done
  (( PLAN )) && return 0
  for name in "${EXPECTED_APPS[@]}"; do
    t=$(timeout_for_app "$name")
    wait_for_app "$name" "$t" || fatal "Application $name did not reach Synced/Healthy within ${t}s"
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

merge_order_guard "preflight, before touching anything"
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
