# ADR-017: sample-api Overlays and the Pinned, Verified Image

## Status: Accepted

## Context
Before M0, `sample-api` ran in `nexus-apps` with 1 replica and no PDB. It ran the digest
`c693838d…`, built before `prometheus-fastapi-instrumentator` was added, so `/metrics` returned 404
(snapshot `20260925T064759Z`, `06c`). CI builds and signs by digest but never writes the digest
back to Git; `main` was red from Ruff drift until PR #40 (M0-3).

## Decision
- **Layout** (spec §25): the base is `apps/sample-api/k8s/base`, with no namespace and no digest.
  The overlays are `overlays/dev` (`nexus-dev`, and it owns the Namespace at level `"0"`) and
  `overlays/prod` (`nexus-prod`, whose Namespace belongs to the `platform` Application).
- **Workload** (§3): 2 replicas, a PodDisruptionBudget with `maxUnavailable: 1`,
  `automountServiceAccountToken: false`. The restricted `securityContext` and the probes are unchanged.
- **Applications:** `sample-api-dev` tracks `experiment/dev-state`; `sample-api-prod` tracks
  `main`. Both have `ignoreDifferences` on `Deployment/sample-api` `/spec/replicas` with
  `RespectIgnoreDifferences=true` (§3, §13).
- **Image pinned in both overlays:**
  `ghcr.io/koussayx8/nexus-platform/sample-api@sha256:45e7a88c950a40fd6ce37a6544d95bc435c76a6aa5af2eefcc9463363150d57c`.
  It was built and signed by green `main` run 36136557684 at `311ad81`, the lint-fix merge that
  includes `prometheus-fastapi-instrumentator`. A digest bump is a commit (§19).

## Signature verification
`cosign verify` with cosign v3.1.3 (binary SHA-256 checked against `cosign_checksums.txt`), anonymous
(the package is public), run 2026-09-25:

```text
cosign verify ghcr.io/koussayx8/nexus-platform/sample-api@sha256:45e7a88c950a40fd6ce37a6544d95bc435c76a6aa5af2eefcc9463363150d57c \
  --certificate-identity 'https://github.com/koussayx8/nexus-platform/.github/workflows/ci.yml@refs/heads/main' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'

exit 0 — 1 signature
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - Existence of the claims in the transparency log was verified offline
  - The code-signing certificate was verified using trusted certificate authority certificates
```

| Field | Value |
| --- | --- |
| `critical.identity.docker-reference` | `ghcr.io/koussayx8/nexus-platform/sample-api` |
| `critical.image.docker-manifest-digest` | `sha256:45e7a88c950a40fd6ce37a6544d95bc435c76a6aa5af2eefcc9463363150d57c` |
| Certificate identity (Subject) | `https://github.com/koussayx8/nexus-platform/.github/workflows/ci.yml@refs/heads/main` |
| Certificate issuer (Issuer, OID 1.3.6.1.4.1.57264.1.1) | `https://token.actions.githubusercontent.com` |
| githubWorkflowTrigger (…1.2) | `push` |
| githubWorkflowSha (…1.3) | `311ad814110967ae7438e29e3fb214d7bbd6c691` |
| githubWorkflowName (…1.4) | `NEXUS CI Pipeline` |
| githubWorkflowRepository (…1.5) | `koussayx8/nexus-platform` |
| githubWorkflowRef (…1.6) | `refs/heads/main` |
| Rekor log index · integrated time | `2956040310` · `2026-09-25T12:46:04Z` |

## Evidence that the image serves `/metrics`
The same source (`311ad81`), run locally, returns HTTP 200 on `/metrics`, with
`http_requests_total{handler,method,status}` (status grouped as `2xx`/`4xx`/`5xx`) and the histogram
`http_request_duration_seconds` (ADR-016). The live check against the image itself is part of
`verify-state.sh` after the rebuild (M0-5).

## Tradeoff
The governance merge (`a12c202`) built a second signed image from the same source. It is not used.
Kyverno `verifyImages` for this identity is a SHOULD for M3 (§19); until then, the pin plus this
record is the provenance.
