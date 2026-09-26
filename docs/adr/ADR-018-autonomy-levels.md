# ADR-018: Autonomy Levels Are Namespace Labels Declared in Git

## Status: Accepted

## Context
Spec §12: the label `nexus.io/autonomy-level` on the Namespace carries `0` to `3`; a missing label
is L0 (fail closed); levels are desired state; only the Cluster Admin changes a level. The pre-M0
repository used a superseded design instead:
- `nexus.io/autonomy-level` annotations on Deployments, pods and alert rules;
- two Kyverno ClusterPolicies that validate and default them (removed in M0-4, ADR-015).

## Decision

| Namespace | Level | Declared on | Owner |
| --- | --- | --- | --- |
| `nexus-prod` | `"1"` Recommend | `main` | `platform` Application (`platform/namespaces/`) |
| `nexus-data` | `"0"` Observe | `main` | `platform` Application |
| `nexus-dev` | `"0"` Observe, raised to `"3"` by commit in M2 | `experiment/dev-state` | `sample-api-dev` Application (`overlays/dev/`) |
| `nexus-system`, `nexus-reasoner`, `nexus-load` | no label | `main` | `platform`. These are not remediation targets; a missing label is L0 |

- Workload-level autonomy annotations are removed wherever M0-4 touches the manifests (sample-api,
  the observability values, the ServiceMonitor and the alert). Workload granularity is OUT OF SCOPE (§12).
- Every `nexus-*` namespace enforces the `restricted` Pod Security Standard.

## Rationale
- A level change is a commit, visible in history and in the API Audit Log once applied.
- Kyverno K1 (M3) reads exactly this label, and nothing else can grant a level.

## Tradeoff
`nexus-dev` is declared on `experiment/dev-state`. Merging `main` into that branch must not
overwrite a level the runner committed there. This holds as long as `main` never changes
`overlays/dev/namespace.yaml` after M2.
