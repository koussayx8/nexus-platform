# ADR-014: Converge in Git, Then Rebuild

## Status: Accepted

## Context
The pre-M0 cluster diverges from the frozen architecture in several ways:
- Loki and Crossplane are installed;
- Kyverno and Crossplane exist only as Helm releases, outside Git;
- `sample-db` is a busybox sleep loop;
- there is no root Application.

It holds no persistent data (no PV, no PVC; snapshot `20260925T064759Z`, `10b`), and M0-5 rebuilds
it anyway. Removing things by hand would mean editing finalizers, running `helm uninstall` and
cascading Application deletions: none of it through Git, and all of it lost at the rebuild.

## Decision
- **M0-4 changes Git only.** Git describes the complete target state, validated offline by the
  required check: `kustomize build`, `helm template` with the value files from Git, an AppProject
  admission check and `kubeconform -strict` (ADR-012). Nothing is applied to the live cluster; it
  stays untouched as the "before" reference.
- **Branch flow.** Feature branches merge into `dev` by PR, and `dev` merges into `main` by PR only
  at a gate. M0-4 changes paths the live cluster tracks on `main` (`apps/sample-api/k8s`,
  `platform/argocd`), so it accumulates on `dev` until the rebuild.
- **M0-5 order:**
  1. final capture of the old cluster;
  2. the owner uninstalls k3s;
  3. the `dev` → `main` PR is merged;
  4. `main` is merged into `experiment/dev-state`;
  5. `bootstrap.sh`.

  M0 is done when `verify-state.sh` exits 0.
- **Root Application** `platform/argocd/root.yaml` owns `platform/argocd/applications/`. It sits
  outside that directory, so it never manages itself. It is applied only by `bootstrap.sh`, after
  the AppProject, and never to the pre-M0 cluster, where its first sync would prune and cascade.
- **Target Application set:** `platform`, `kyverno`, `observability`, `sample-api-dev` (tracks
  `experiment/dev-state`) and `sample-api-prod`. There is no `loki` and no
  `crossplane-infrastructure`. `dependency-db` arrives in M1 with `/items`; `nexus` arrives in M1
  with the operator.

## Settled facts (read-only, 2026-09-25)

| Question | Answer | Consequence for `bootstrap.sh` |
| --- | --- | --- |
| Is the ghcr package public? | Yes: `gh api /users/koussayx8/packages/container/nexus-platform%2Fsample-api` gives `visibility=public`, and an anonymous manifest GET of the pinned digest returns 200 | No image pull secret |
| Is the repository public? | Yes (`visibility=public`) | No ArgoCD repository credentials |
| Does any Ingress use Traefik? | No: no Ingress, Traefik IngressRoute or Gateway API route exists; the Traefik LoadBalancer routes nothing | k3s is installed with `--disable traefik --disable servicelb` |

## Rationale
- Every change reaches the rebuilt cluster through the same path the thesis claims: pull request →
  green checks → ArgoCD.
- A rebuild from Git proves the state is reproducible, which a hand-cleaned cluster cannot.

## Tradeoff
Until M0-5 the live cluster keeps running the old state: Loki, Crossplane, the exposed Grafana
password and the busybox `sample-db`. This is accepted: nothing in it is persistent or reachable
off this machine (ADR-010).
