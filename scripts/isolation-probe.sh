#!/usr/bin/env bash
# isolation-probe.sh — the Reasoner's isolation, live (Block 3 step 3; manual scenario 3). The owner
# runs it: `bash scripts/isolation-probe.sh`. Moved into Git from the Block 3 evidence folder
# (ADR-047), with the `* *` question quoted: it was unquoted, the shell globbed it, and that line
# read FAIL although RBAC answered "no" (block3-2026-11-17/isolation-probe-wildcard-recheck.txt).
#
# Cluster writes: NONE. The only non-read is ONE `kubectl exec` into the Reasoner pod (an ask rule),
# which runs a short Python program inside it. Every other call is a read (`get`, `auth can-i`).
# Targets are looked up at run time, because pod IPs change on every restart.
#
# Checks (spec §18 N1-N6, TASKS.md Block 3 "isolation only"):
#   T1 no ServiceAccount token: the token path and its directory are absent inside the container
#   T2 no Kubernetes credentials in the environment (names only are printed, never values)
#   R1 RBAC: the Reasoner's `auth can-i --list` equals that of a never-bound identity, and
#      ten resource questions all answer "no"
#   N  TCP from inside the Reasoner, 3 s timeout each:
#        must FAIL: API server (Service 10.43.0.1:443, kubernetes.default.svc:443, node:6443),
#                   kubelet (node:10250), operator, Flight Recorder DB, both sample-api pods,
#                   Dependency DB, Prometheus, ArgoCD server
#        must SUCCEED: integrate.api.nvidia.com:443, the provider R2 and R3 call (ADR-045; it was
#                      api.groq.com before the Groq configs were retired) — the control: without
#                      it a FAIL above proves nothing
# Exit 0 only if every check holds.
# shellcheck disable=SC2015,SC2001  # ok() always returns 0; sed for a readable prefix
set -uo pipefail

C=(--context default)
SA=system:serviceaccount:nexus-reasoner:nexus-reasoner
CONTROL=system:serviceaccount:nexus-reasoner:never-bound-control
fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }

ip_of() {  # ip_of <ns> <selector>
  kubectl "${C[@]}" -n "$1" get pod -l "$2" -o jsonpath='{.items[0].status.podIP}'
}

pod=$(kubectl "${C[@]}" -n nexus-reasoner get pod -l app.kubernetes.io/name=nexus-reasoner \
  -o jsonpath='{.items[0].metadata.name}') || { echo "no Reasoner pod"; exit 2; }
node=$(kubectl "${C[@]}" get node -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
api=$(kubectl "${C[@]}" -n default get svc kubernetes -o jsonpath='{.spec.clusterIP}')
echo "Reasoner pod: $pod   node: $node   kubernetes Service: $api"

# ---- R1: RBAC (reads only)
want=$(kubectl "${C[@]}" -n nexus-reasoner auth can-i --list --as="$CONTROL" | sort | sha256sum)
got=$(kubectl "${C[@]}" -n nexus-reasoner auth can-i --list --as="$SA" | sort | sha256sum)
[[ $got == "$want" ]] && ok "R1 can-i --list equals a never-bound identity's (discovery and self-review only)" \
                      || bad "R1 can-i --list differs from a never-bound identity's"
for q in "get pods" "list secrets" "get configmaps" "create pods" "get incidents.nexus.io" \
         "patch deployments/scale" "create pods/eviction" "get nodes" "create serviceaccounts/token" "* *"; do
  # Each question is two words, verb and resource. `read -ra` splits it without pathname expansion,
  # and the quoted array passes `*` to kubectl as a literal: never globbed into file names.
  read -r -a words <<< "$q"
  a=$(kubectl "${C[@]}" auth can-i "${words[@]}" --all-namespaces --as="$SA" 2>/dev/null | tail -1)
  [[ $a == no ]] && ok "R1 can-i $q -A: no" || bad "R1 can-i $q -A: $a"
done
spec=$(kubectl "${C[@]}" -n nexus-reasoner get pod "$pod" \
  -o jsonpath='{.spec.automountServiceAccountToken}|{.spec.volumes[*].name}')
[[ $spec == "false|config" ]] && ok "T0 pod spec: automountServiceAccountToken=false, volumes=config only" \
                              || bad "T0 pod spec: $spec"

# ---- targets that must be unreachable, then the one that must be reachable
targets=(
  "api-service $api 443"
  "api-dns kubernetes.default.svc 443"
  "api-node $node 6443"
  "kubelet $node 10250"
  "operator-metrics $(ip_of nexus-system app.kubernetes.io/name=nexus-operator) 9102"
  "flight-recorder-db $(ip_of nexus-system app.kubernetes.io/name=flight-recorder-db) 5432"
  "sample-api-dev $(ip_of nexus-dev app.kubernetes.io/name=sample-api) 8000"
  "sample-api-prod $(ip_of nexus-prod app.kubernetes.io/name=sample-api) 8000"
  "dependency-db $(ip_of nexus-data app.kubernetes.io/name=dependency-db) 5432"
  "prometheus $(ip_of monitoring app.kubernetes.io/name=prometheus) 9090"
  "argocd-server $(ip_of argocd app.kubernetes.io/name=argocd-server) 8080"
  "CONTROL:nim integrate.api.nvidia.com 443"
)

# ---- the one exec: T1, T2 and every TCP probe, in one Python run inside the Reasoner
out=$(kubectl "${C[@]}" -n nexus-reasoner exec "$pod" -c reasoner -- python3 -c '
import os, socket, sys
tok = "/var/run/secrets/kubernetes.io/serviceaccount"
print("T1", "absent" if not os.path.exists(tok) else "PRESENT")
creds = sorted(k for k in os.environ if any(w in k.upper() for w in ("KUBECONFIG", "KUBE_TOKEN", "BEARER")))
print("T2", "none" if not creds else "PRESENT:" + ",".join(creds))   # names only, never values
for spec in sys.argv[1:]:
    name, host, port = spec.split()
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, int(port))); r = "connected"
    except OSError as e:
        r = "refused/unreachable (" + type(e).__name__ + ")"
    finally:
        s.close()
    print("N", name, host + ":" + port, r)
' "${targets[@]}" 2>&1)
rc=$?
echo "$out" | sed 's/^/  | /'
(( rc == 0 )) || bad "exec failed (exit $rc)"
grep -q '^T1 absent$' <<< "$out" && ok "T1 no ServiceAccount token in the container" || bad "T1 token path present"
grep -q '^T2 none$'   <<< "$out" && ok "T2 no Kubernetes credentials in the environment" || bad "T2 credential-like variables present"
while read -r _ name hostport result; do
  if [[ $name == CONTROL:* ]]; then
    [[ $result == connected ]] && ok "N  $name $hostport reachable (control)" || bad "N  $name $hostport not reachable: the FAIL rows prove nothing"
  else
    [[ $result != connected ]] && ok "N  $name $hostport unreachable" || bad "N  $name $hostport REACHABLE"
  fi
done < <(grep '^N ' <<< "$out")
exit $fail
