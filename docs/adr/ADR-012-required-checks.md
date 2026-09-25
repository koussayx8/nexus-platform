# ADR-012: One Unfiltered Required Check for main and dev

## Status: Accepted

## Context
Spec §19 designs one path-filtered workflow per component. A path-filtered workflow cannot be a
required status check: a pull request outside its paths never gets the check, so it waits forever.
Path filtering also let secrets through. The GitLeaks job sits inside the sample-api workflow
(`ci.yml`, `apps/sample-api/**`), so commit `719ade0`, which touched only `platform/crossplane/**`,
added a credential that no scan ever saw (ADR-010).

## Decision
- **`.github/workflows/repo-checks.yml`**:
  - triggers: every pull request to `main` and `dev` with no path filter, and pushes to both;
  - one job whose check context is `repo-checks`. It is the only required check on `main` and `dev`;
  - the logic lives in `.github/scripts/repo-checks.sh`, which runs the same way locally.
- **What it checks:**
  1. **GitLeaks** over the pull request's commit range (base..head; for pushes before..after) and
     over the committed tree at HEAD (`git archive HEAD`). **Never the full history.** The dead
     DigitalOcean token stays there by design (ADR-010), and a history scan would fail every run.
  2. **`kustomize build`** of every kustomization, **except under parked paths**
     (`platform/crossplane/`, ADR-011). GitLeaks still covers them.
  3. **`kubeconform -strict`** on the build output for **Kubernetes 1.34.6**, the k3s version of
     the pre-M0 cluster. Schemas for 1.34.6 exist, so no fallback was needed. Core schemas are
     pinned to `yannh/kubernetes-json-schema@de494cc2`, CRD schemas to
     `datreeio/CRDs-catalog@ad3b08c5`.
- **Pinning:** gitleaks 8.30.1, kubeconform 0.8.0 and kustomize 5.8.1, each SHA-256 verified;
  `actions/checkout` pinned by commit SHA (v7.0.1); `contents: read` only; no cluster credential (§19).
- The path-filtered component workflows remain for component work (lint, tests, build, sign). The
  sample-api workflow also runs lint and tests on pull requests to `dev`; it builds, pushes and
  signs only from `main`.
- **Standing rule:** `main` is never left red. A failing workflow on `main` is fixed before the next
  pull request merges.

## Rationale
- The thesis claims that policy and RBAC changes reach the cluster only through pull requests with
  green checks. That needs one check every pull request actually receives.
- Scanning the range plus the tree blocks new secrets without failing on ones that are already public.
- Pinned tools and schemas make a check result reproducible; a Ruff upgrade already broke `main` once.

## Tradeoff
An unfiltered check runs on every pull request, including docs-only ones, costing about 10 s of CI.
The check departs from §19's path-filtered-only design; spec v1.1 records it. `helm template`
validation joins in M0-4, when the charts' values files are in Git.

## Validation (2026-09-25, local, same pinned tools)
- The range `cbe9f8e..311ad81` (25 commits) and the tree at `311ad81` show no leaks. 8
  kustomizations and 14 resources are valid.
- Negative controls: GitLeaks exits 1 on a generated fake token, and kubeconform exits 1 on an
  unknown Service field.
