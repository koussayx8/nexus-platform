# PARKED — Crossplane

**Status:** parked since M0-2 (2026-09-25). It is kept in the tree and referenced by no Application
on the rebuilt cluster. See ADR-011 (repository cleanup) and ADR-006 (the original design).

## Why it is parked
- Spec §23: "Crossplane on the cluster — DEFERRED to the final steps. Unused by the thesis; noise on
  one node." The Dependency DB is a plain PostgreSQL StatefulSet instead (§3, §25).
- Spec §0 and §27: Crossplane work happens only in the project's final steps, if time remains.
- On the pre-M0 cluster it cost 94 CRDs, 26 APIServices and four controllers, and it produced no
  managed resource (snapshot `20260925T064759Z`, checks `08c`–`08g`).

## Until the M0-5 rebuild
The live Application `crossplane-infrastructure` still tracks `platform/crossplane/k8s/`, where it
renders only the `crossplane-system` Namespace. **Do not change anything under `k8s/`** until the
rebuild. After the rebuild, no Application references this directory.

## What resuming needs
1. An ArgoCD Application under the root Application for Crossplane core: Helm chart `crossplane`
   2.3.1, with the image pinned by digest as in `k8s/base/values.yaml`.
2. The provider `provider-upjet-digitalocean` v0.3.2 and the function
   `function-patch-and-transform` v0.2.0. `function-apply` v0.1.0 could not be resolved on the
   old cluster; drop it or pin a resolvable version.
3. Provider credentials from a Secret created by `bootstrap.sh`, never from Git. The old
   `secrets/digitalocean-creds.yaml` was removed in M0-2 (ADR-010).
4. A fix for the dev Composition. The old XR failed with "an empty namespace may not be set when a
   resource name is provided".
5. Retargeting the claim `apps/sample-api/infrastructure/database.yaml`, which is parked with this
   directory, from `nexus-apps` to a §3 namespace, and an ADR explaining how it coexists with the
   `dependency-db` StatefulSet.
6. AppProject `nexus` destinations and cluster-resource whitelists for `crossplane-system` and the
   Crossplane API groups.

## Contents
`compositions/`, `providers/`, `xrds/` and `k8s/` (the Namespace kustomization).
