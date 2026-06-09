# ADR-006: Crossplane Design — GitOps-Native Resource Provisioning

**Status:** Accepted
**Date:** 2026-06-09
**Author:** Koussay Belhouchet

## Context

NEXUS has established GitOps (ArgoCD) and policy enforcement (Kyverno) foundations.
Developers currently provision infrastructure manually or via Terraform — this creates
bottlenecks, lacks GitOps auditability, and requires cloud console access. We need a
self-service infrastructure layer that:

1. Lets developers request resources via Kubernetes API (not cloud consoles)
2. Integrates with our existing GitOps pipeline (ArgoCD-managed)
3. Supports both dev (free, local) and prod (managed, billable) environments
4. Enforces platform standards (autonomy-level annotations, SHA-pinned images)
5. Provides visibility in Backstage (our IDP)

Key questions this ADR answers:
1. Why Crossplane over Terraform-only or Pulumi?
2. How does Crossplane integrate with ArgoCD GitOps?
3. What is our dev/prod resource provisioning strategy?
4. How do we handle image SHA-pinning for Crossplane?
5. What is our namespace and project convention?
6. How do we manage cloud provider credentials?

## Decision

### 1. Crossplane as the Resource Provisioning Layer

**Decision:** Adopt Crossplane for Kubernetes-native infrastructure provisioning.
Terraform remains for cluster-level infrastructure (DOKS, VPC) but Crossplane handles
all workload-level resources (databases, caches, buckets).

**Rationale:**
- Crossplane resources are Kubernetes CRDs — they inherit our GitOps pipeline automatically
- Developers use `kubectl apply` or Backstage templates — no cloud console access needed
- Claims are namespace-scoped and auditable via Kubernetes RBAC
- Compositions encode platform standards (SHA images, autonomy-level annotations) — developers cannot bypass them
- Crossplane integrates with Kyverno: claims can be validated by policy before provisioning

```
Developer creates Claim (YAML) → Crossplane selects Composition →
Dev: in-cluster PostgreSQL on k3s | Prod: DigitalOcean managed DB
```

### 2. ArgoCD Integration: Annotation Tracking + ignoreDifferences

**Decision:** Configure ArgoCD with `application.resourceTrackingMethod: annotation`
and `ignoreDifferences` for Crossplane-managed resources.

**Rationale:**
- Crossplane mutates resource status fields constantly (conditions, connection secrets)
- Without annotation tracking, ArgoCD fights with Crossplane over ownership labels
- Without ignoreDifferences, ArgoCD shows constant "Out of Sync" for healthy resources
- ProviderConfigUsage resources are noise — excluded from ArgoCD UI

```yaml
# argocd-cm additions
data:
  application.resourceTrackingMethod: annotation
  resource.exclusions: |
    - apiGroups: ["*"]
      kinds: ["ProviderConfigUsage"]
```

### 3. Dev/Prod Composition Split

**Decision:** Define two Composition variants per XRD:
- **Dev Composition**: Creates in-cluster resources (PostgreSQL Deployment + Service + PVC)
- **Prod Composition**: Creates managed cloud resources (DigitalOcean managed PostgreSQL)

**Rationale:**
- Dev environments must be free — no cloud credits spent on every developer test
- Prod environments use managed services for reliability, backups, and scaling
- The `environment` parameter on the claim selects the Composition automatically
- Dev Composition proves the pipeline works without cloud costs; Prod Composition is validated at DOKS deployment time

| Environment | Resource Type | Cost | Managed By |
|-------------|--------------|------|------------|
| dev | In-cluster PostgreSQL (Deployment + PVC) | $0 | Crossplane on k3s |
| prod | DigitalOcean managed PostgreSQL | ~$15/mo | DO + Crossplane |

### 4. Image SHA-Pinning Workaround

**Decision:** Pin Crossplane and provider images to SHA digests using the Helm chart's
`image.repository` field with `@sha256:` suffix and `image.ignoreTag: true`.

**Rationale:**
- The Crossplane Helm chart does not have a native `image.digest` parameter
- Setting `image.repository: crossplane/crossplane@sha256:<DIGEST>` with `ignoreTag: true`
  prevents Helm from appending a `:latest` or version tag
- This satisfies Hard Rule #2 (no :latest tags) while using the standard Helm installation path
- Provider packages (xpkg.upbound.io) also support digest pinning via `@sha256:` syntax

```yaml
# values.yaml
image:
  repository: xpkg.crossplane.io/crossplane/crossplane@sha256:<DIGEST>
  ignoreTag: true
  pullPolicy: Always
```

### 5. Namespace and Project Convention

**Decision:** Use `crossplane-system` for platform infrastructure (Crossplane core,
providers, ProviderConfigs) and `nexus-apps` for developer claims.

**Rationale:**
- Separates platform team concerns (provider configs, XRDs) from developer concerns (claims)
- `crossplane-system` is the Crossplane community standard — follows upstream conventions
- `nexus-apps` is our existing application namespace — claims live alongside the apps that use them
- ArgoCD AppProject `nexus` is expanded to include `crossplane-system` in destinations

### 6. Cloud Provider Credential Strategy

**Decision:** Store the DigitalOcean API token in a Kubernetes Secret manually.
Migrate to Vault-backed External Secrets in Week 7-8.

**Rationale:**
- Vault is not yet deployed (Week 7-8 scope)
- A manual Secret is sufficient for dev environment and thesis demonstration
- The Secret template is committed to Git with a placeholder token; the real token is applied manually
- This is documented as technical debt with a clear migration path

```yaml
# Template (committed to Git)
apiVersion: v1
kind: Secret
metadata:
  name: digitalocean-creds
  namespace: crossplane-system
stringData:
  credentials: '{"token":"REPLACE_WITH_REAL_TOKEN"}'
```

## Consequences

- **Positive:** Developers provision infrastructure via GitOps without cloud console access
- **Positive:** Platform standards (SHA images, autonomy-level) are enforced at the Composition level — developers cannot bypass them
- **Positive:** Dev environments are free (in-cluster resources); prod uses managed services
- **Positive:** Backstage integration provides self-service infrastructure visibility
- **Negative:** Crossplane adds ~1.07GB memory overhead (core + provider) to the k3s node
- **Negative:** ArgoCD requires global configuration changes (annotation tracking) that affect all applications
- **Negative:** Manual Secret management is temporary technical debt until Vault is deployed
- **Negative:** In-cluster PostgreSQL is not production-grade (no HA, no backups) — clearly documented as dev-only

## References

- Crossplane Documentation: https://docs.crossplane.io/latest/concepts/
- Crossplane + ArgoCD Guide: https://docs.crossplane.io/latest/guides/crossplane-with-argo-cd/
- provider-upjet-digitalocean: https://marketplace.upbound.io/providers/crossplane-contrib/provider-upjet-digitalocean
- ADR-004 (GitOps Strategy — ArgoCD sync policy and self-heal)
- ADR-005 (IDP Design — Backstage integration context)
- ADR-003 (Autonomy Ladder — Kyverno policy enforcement on Crossplane resources)
- ADR-009 (Incident Flight Recorder — audit trail for infrastructure changes)
