# Security policy

NEXUS is a research project about keeping Kubernetes remediation safe even when the reasoner is wrong.
Security reports are welcome and taken seriously.

## Supported versions

NEXUS is pre-1.0 and evolves on a single line. Security fixes are made on `main` and released in the
next tag; older tags are not patched.

| Version | Supported |
| --- | --- |
| `main` and the latest tag listed in [`CHANGELOG.md`](CHANGELOG.md) | Yes |
| Older tags | No |

## Reporting a vulnerability

**Do not open a public issue or pull request for a vulnerability.**

Use GitHub's private reporting: open the repository's **Security** tab, choose **Report a vulnerability**,
and describe the problem. If that option is not available, open an issue titled "Security contact
request" with no technical detail, and the maintainer will arrange a private channel.

Please include:

- what is affected (a file, a manifest, a workflow, an image digest) and the commit or tag;
- how to reproduce it, or a proof of concept;
- the impact you believe it has.

This is a one-person project. Reports are handled on a best-effort basis: expect an acknowledgement
within about a week, and a fix or a decision as soon as the work allows. Reporters who want credit are
named in the release notes.

## In scope

- A way for the reasoner, the operator or a workload to perform a cluster mutation that the enforcement
  plane (RBAC, Kyverno admission, CRD validation, the Kill Switch) should refuse.
- A way for untrusted text (logs, alert content, Incident fields) to influence an action, or to inject
  content into a rendered view.
- Credentials or tokens committed to the repository, or leaked through CI logs or build artifacts.
- Weaknesses in the supply chain: unsigned or unverifiable images, unpinned or unchecked dependencies, CI
  workflow permissions broader than they need to be.
- Overly broad RBAC, privileged containers, or pods that mount service-account tokens without need.

## Out of scope

- Credentials that earlier commits contain and that were revoked before this policy; they remain in Git
  history by design ([ADR-010](docs/adr/ADR-010-m0-triage-and-secret-files.md)). A live credential is in scope.
- Findings that need access to the single-node host or its kubeconfig in the first place.
- Denial of service against a development cluster, and findings in third-party components (k3s,
  ArgoCD, Kyverno, Prometheus, PostgreSQL) that belong upstream. Report those to the upstream project;
  tell us if NEXUS configures them unsafely.
- Anything in components that are parked or removed from the architecture (see
  [`docs/adr/ADR-011-repository-cleanup.md`](docs/adr/ADR-011-repository-cleanup.md)).

## How the project handles security today

- Secret scanning and push protection are enabled; `repo-checks` runs GitLeaks on every pull request.
- `main` and `dev` are protected, and the only way a change reaches the cluster is a merge to Git, which
  ArgoCD pulls.
- Images are referenced by digest and signed with Cosign (keyless); the README shows how to verify them.
- Python dependencies are installed with `--require-hashes`.
- The operator runs as a non-root user with a read-only root filesystem, under RBAC limited to what its
  milestone needs.
