# ADR-011: Repository Cleanup

## Status: Accepted

## Context
The pre-M0 repository carried components the frozen architecture removes or defers (spec §23), plus
agent-era documents. The repository is public, so every unused component costs Dependabot PRs,
security alerts and false leak hits. M0-2 (2026-09-25) cleans the tree without rewriting history.

## Decision
Before anything was removed, `main` at `cbe9f8e` was tagged **`archive/pre-m0-cleanup`** and the tag
was pushed. Each removal is its own commit.

**Archived: `git rm`, no planned return.**

| Path | Why | Restore |
| --- | --- | --- |
| `infra/terraform/` | DigitalOcean Kubernetes provisioning; NEXUS runs on single-node k3s (§3) | `git checkout archive/pre-m0-cleanup -- infra/terraform` |
| `platform/vault/` | Vault is REMOVED (§23) | `git checkout archive/pre-m0-cleanup -- platform/vault` |
| `ai/` | Legacy agent, anomaly detector and RAG; the operator and Reasoner are built under `operator/` and `reasoner/` from M1 (§25) | `git checkout archive/pre-m0-cleanup -- ai` |
| `docs/NEXUS_OpenCode_Master_Brief_v4.md` | Superseded by `docs/architecture/final-spec.md` and `CLAUDE.md` | `git checkout archive/pre-m0-cleanup -- docs/NEXUS_OpenCode_Master_Brief_v4.md` |
| `platform/backstage/` | Frozen until the final steps (§0, §23). Restoring it then is one checkout plus a dependency upgrade it would need anyway | `git checkout archive/pre-m0-cleanup -- platform/backstage` |

**Parked: kept in the tree, referenced by no Application after the M0-5 rebuild.**

| Path | Why | Resume |
| --- | --- | --- |
| `platform/crossplane/` | DEFERRED to the final steps (§23) | `platform/crossplane/PARKED.md` |
| `apps/sample-api/infrastructure/database.yaml` | The Crossplane claim; parked with Crossplane | `platform/crossplane/PARKED.md`, step 5 |

**Removed in M0-2 (ADR-010).** Both credentials are dead.

| Path | Restore |
| --- | --- |
| `platform/crossplane/secrets/digitalocean-creds.yaml` | Do not restore: credentials come from a Secret created by `bootstrap.sh` |
| `apps/sample-api/infrastructure/postgresql-manual.yaml` | Do not restore: the Dependency DB arrives in M1 |

**Dependabot.** The repository has no `dependabot.yml`; its PRs come from Dependabot security
updates, a repository setting that stays **on** for `apps/sample-api`. Backstage PRs still open
after the archive are closed with a comment pointing to this ADR.

**No Ansible** is tracked, so there is nothing to archive there.

## Rationale
- A smaller public surface means fewer alerts and a tree that matches spec §25.
- The archive tag plus one-path-per-commit makes every restore a single command.
- History is not rewritten, so the backup tags and existing clones stay valid.

## Tradeoff
The restored Backstage will be stale. Accepted, because it needs an upgrade in the final steps regardless.

## Consequences
- Until the M0-5 rebuild, nothing merged to `main` changes a path a live Application tracks:
  `apps/sample-api/k8s`, `platform/crossplane/k8s`, `platform/argocd`.
- Local build artefacts under `platform/backstage/` (about 1.9 GB of `node_modules`, `dist` and Yarn
  state) are git-ignored and can be deleted locally.
