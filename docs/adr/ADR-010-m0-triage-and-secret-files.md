# ADR-010: M0 Triage of Uncommitted Work and Secret-Bearing Files

## Status: Accepted

## Context
M0-1 (snapshot `docs/state/20260925T064759Z`, `docs/CURRENT_STATE.md`) found 5 uncommitted
changes and 24 untracked files in the WSL2 tree. It also found Secret manifests with data in Git:
`platform/crossplane/secrets/digitalocean-creds.yaml`, committed in `719ade0` even though
`.gitignore` excludes `secrets/`; `apps/sample-api/infrastructure/postgresql-manual.yaml`; and
example Secrets in ADR-006 and ADR-007. Everything was backed up first, under tag `backup/pre-m0`
and in `~/nexus-backup/`.

## Decision
- **Committed:**
  - the frozen spec `docs/architecture/final-spec.md`;
  - `CLAUDE.md`;
  - `.claude/settings.json`;
  - `scripts/capture-state.sh`;
  - `docs/state/.gitignore` (snapshots stay local);
  - `docs/CURRENT_STATE.md` and `TASKS.md`;
  - three observability fixes (alert label, the kube-state-metrics subchart key, the ServiceMonitor release label);
  - `platform/observability/config/kustomization.yaml`.
- **Dropped:** the AGENTS.md roster change (AGENTS.md is now one line pointing to CLAUDE.md), the
  namespace-pinned dashboard change (parked as a stash and kept in the backup),
  `platform/argocd/applications/observability-config.yaml` (§3 has a single observability
  Application), `.omo/` and every `*:Zone.Identifier` (now ignored).
- **Secret-bearing files:** `digitalocean-creds.yaml` and `postgresql-manual.yaml` are removed with
  `git rm`. In ADR-006 and ADR-007, every value under `data:`/`stringData:` is replaced by
  `<REDACTED>` through a scripted edit that never printed a value. **History is not rewritten.**
  Credential status after the owner's check: *to be recorded at the M0-2 gate.*
- **Rule A2 for agents:**
  - a path under `secrets/` is never opened;
  - a file with `kind: Secret` and `data`/`stringData` is never shown;
  - a file that only names `kind: Secret`, with no data, may be read and edited.
- ADRs live in `docs/adr/` (spec §25).

## Rationale
- Git becomes the complete record of desired state before M0-4 converges it.
- Rewriting history would break the backup tag and every clone. Removing the files stops further
  spread, and any live credential is rotated instead.
- Snapshots contain cluster detail and are regenerated at will, so they stay out of Git.

## Tradeoff
The removed values stay readable in Git history (`719ade0` and earlier commits). This is mitigated by
rotation, not by rewriting.

## Consequences
- Grafana `adminPassword` is a 5-character literal in two tracked files,
  `platform/argocd/applications/observability.yaml:76` and
  `platform/observability/k8s/base/kube-prometheus-stack-values.yaml:84`, since `103676e`.
  The rebuilt Grafana takes its password from a Secret created by `bootstrap.sh`, never from Git
  (M0-4 removes the literal; M0-5 creates the Secret).
- The live cluster is untouched. None of these removals is referenced by a tracked kustomization.
