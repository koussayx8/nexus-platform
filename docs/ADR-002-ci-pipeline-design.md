# ADR-002: CI Pipeline Design — Security-Hardened GitHub Actions

**Status:** Accepted
**Date:** 2026-04-27
**Author:** Koussay Belhouchet

## Context

NEXUS needs a CI pipeline that enforces quality, security, and supply chain integrity
on every commit. The pipeline must be free-tier compatible (GitHub Actions: 2000 min/month
for public repos) and produce auditable artifacts.

## Decision

Seven-stage pipeline: **Ruff → Pytest → Semgrep SAST → GitLeaks → Docker Build+Push → Trivy → Cosign**

### Tool Choices

| Stage | Tool | Rationale |
|-------|------|-----------|
| Lint | **Ruff** (not flake8/pylint) | 10-100× faster, replaces flake8+isort+pyupgrade in one binary |
| Test | **Pytest** | Industry standard, async support, fixture system |
| SAST | **Semgrep** (not Bandit) | Multi-language, custom rules, SARIF output for GitHub Security tab |
| Secrets | **GitLeaks** (not TruffleHog) | Lighter, faster, official GitHub Action, zero config |
| Registry | **GHCR** (not DockerHub/Harbor) | Free for public repos, integrated with GitHub OIDC, no rate limits |
| Scan | **Trivy** (not Grype/Snyk) | OSS, comprehensive (OS+lang+config), SARIF output |
| Sign | **Cosign keyless** (not GPG) | No key management, Sigstore transparency log, SLSA-aligned |

### Architecture Decisions

1. **Sequential gates:** Lint must pass before test/security run. All must pass before build.
   Rationale: fail fast, save CI minutes.
2. **SARIF uploads:** Semgrep and Trivy results go to GitHub Security tab for visibility.
3. **Multi-stage Dockerfile:** Builder + production stage. Non-root user, read-only filesystem,
   minimal attack surface.
4. **Keyless signing:** Cosign uses GitHub OIDC identity, no secret keys to rotate.
5. **GHA cache:** Docker layer caching via `type=gha` to reduce build time.

## Consequences

- Every image in GHCR has a verifiable signature and vulnerability scan
- Pipeline takes ~2 minutes end-to-end (acceptable for development velocity)
- Free tier: ~2000 min/month is sufficient for single-developer workflow
- SARIF integration gives us the GitHub Security tab for free
- Foundation for SLSA Level 2 attestation (provenance from CI)

## Future

- Add SBOM generation (`syft`) to complete supply chain story
- Add `cosign verify` as Kyverno admission policy (Week 4)
- Self-hosted runner on k3s if GitHub minutes become scarce
