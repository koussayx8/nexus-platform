# NEXUS documentation

Start with the [project README](../README.md). This page indexes everything under `docs/` and says
which documents are authoritative and which are historical.

## Authoritative

| Document | What it is |
| --- | --- |
| [`architecture/final-spec.md`](architecture/final-spec.md) | The architecture specification, version 1.0, frozen. It changes only through an ADR and a version bump. Start with §1 (the hypothesis), §10 (trust and security) and §12 (autonomy model). |
| [`adr/`](adr/) | One Architecture Decision Record per decision. The table in the [README](../README.md#architecture-decisions) lists them with notes on which ones later records supersede in part. |
| [`CURRENT_STATE.md`](CURRENT_STATE.md) | Observed cluster and repository state, **generated** by `scripts/verify-state.sh` and never edited by hand. |
| [`../TASKS.md`](../TASKS.md) | The current milestone, its phases and gates, and the "Later" list of out-of-scope findings. |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Release notes, one section per tag. |

## Reading order

| If you want to… | Read |
| --- | --- |
| Understand the idea | README → spec §1 → spec §10 |
| See how an incident is handled | spec §6 (lifecycle), §7 (state machine), §9 (Action Catalogue), §12 (autonomy levels) |
| See how detection works | [ADR-024](adr/ADR-024-detection-rules.md), spec §3 |
| See how the cluster is built and checked | [ADR-014](adr/ADR-014-converge-in-git-then-rebuild.md), [ADR-019](adr/ADR-019-bootstrap-and-audit-policy.md), `scripts/bootstrap.sh`, `scripts/verify-state.sh` |
| See how the operator works | [ADR-023](adr/ADR-023-incident-crd-and-kopf-persistence.md), [ADR-025](adr/ADR-025-operator-skeleton.md) |
| Check the supply chain | [ADR-017](adr/ADR-017-sample-api-overlays-and-image-pin.md), [ADR-012](adr/ADR-012-required-checks.md), the README's CI/CD section |
| Know what is live and what is next | README status table, then `TASKS.md` |

## Plans and records

| Document | What it is |
| --- | --- |
| [`plans/m1-plan.md`](plans/m1-plan.md) | The approved M1 plan, frozen at approval. Current state lives in `TASKS.md` and ADR-020. |
| [`gitops-validation-log.md`](gitops-validation-log.md) | Early GitOps validation (May 2026): sync from Git and self-heal. Written before the M0 rebuild, so resource names differ from today's. |
| [`screenshots/`](screenshots/) | Screenshots from that early validation. |

## Historical

These predate the frozen specification. Where they conflict with it, the specification governs. They
mention tools that are no longer part of the architecture.

| Document | What it is |
| --- | --- |
| [`NEXUS_STATUS.md`](NEXUS_STATUS.md) | Weekly status notes from the first four weeks. |
| [`CUT_LIST.md`](CUT_LIST.md) | The original scope contract (keep / cut / defer). |
| [`CONTRIBUTION.md`](CONTRIBUTION.md) | The original research-contribution statement. The current contribution is stated in spec §1. |

## Conventions

- **Specification, ADRs, `CURRENT_STATE.md`** — the specification is frozen; every later decision gets an
  ADR; `CURRENT_STATE.md` is regenerated, not edited.
- **Branches** — work goes to `dev` by pull request; `main` is promoted from `dev` by a gate pull request.
  Both are protected by the required `repo-checks` check ([ADR-012](adr/ADR-012-required-checks.md)).
- **Commits** — conventional (`type(scope): summary`), one concern per commit.
