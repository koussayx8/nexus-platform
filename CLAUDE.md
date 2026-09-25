# NEXUS — instructions for Claude Code

## What this project is

NEXUS is an ESPRIT final-year engineering project (PFE). It is a Kubernetes
incident-response system that tests one hypothesis: when authority over cluster
mutations is enforced outside the reasoning component — Kyverno admission
control, least-privilege RBAC and a GitOps-compatible action boundary — the
safety of autonomous remediation becomes independent of the reasoner's quality,
while its effectiveness does not. The LLM reasoner is untrusted and never
authorises a mutation.

## Sources of truth

- Architecture: `docs/architecture/final-spec.md` — version 1.0, frozen.
  Do not redesign. If reality contradicts the spec, report the contradiction;
  never silently adapt the design.
- Current milestone, phases and gates: `TASKS.md`.
- Observed state: `docs/CURRENT_STATE.md` — generated from commands you ran,
  never written from memory.
- Decisions: `docs/adr/`.

## Environment (unverified until a command in this session confirms it)

- WSL2 Ubuntu 24.04. Repository `~/nexus-platform`, Python venv `.venv`.
  Never work under `/mnt/c/`.
- Single-node k3s (v1.34.x). ArgoCD with automated sync and selfHeal; Helm
  values are inline in the Application manifests. Kyverno 1.18.
  kube-prometheus-stack. Loki and Crossplane are still installed but are being
  removed.
- CI: GitHub Actions, images on ghcr.io, Cosign keyless signing.
  Remote: github.com/koussayx8/nexus-platform.

## Hard rules

1. Evidence before claims. Every statement about the repository or the cluster
   comes from a command you ran in this session; anything else is labelled
   UNVERIFIED.
2. Git is the only way to change desired state. ArgoCD selfHeal reverts manual
   changes within about 30 seconds, so never fix an ArgoCD-managed resource
   with kubectl.
3. No cluster mutation, no Helm install, upgrade or uninstall, no ArgoCD sync,
   and no deletion of anything, unless the current phase in `TASKS.md` allows
   it and I approved it in chat.
4. Never rewrite Git history, force-push, `reset --hard`, `clean`, or discard
   uncommitted changes. Work on a branch; small conventional commits
   (`type(scope): summary`), one concern per commit.
5. Never read, print or commit secrets: `~/.nexus/keys.env`, Kubernetes Secret
   data, tokens, kubeconfig credentials. Redact anything credential-like in
   every output you produce.
6. If a command needs `sudo`, do not run it. Print it and ask me to run it.
7. Stop at every phase gate in `TASKS.md` and report. Never start the next
   phase on your own.
8. Removed from the architecture — never reintroduce: Loki, OpenTelemetry,
   Tempo, MCP, Vault, any service mesh, Redis, LitmusChaos, MLflow, an agentic
   tool loop, autonomy level L4. Crossplane and Backstage stay frozen until the
   project's final steps.
9. Out-of-scope findings go to the "Later" section of `TASKS.md`, never into
   the current change.
10. Be brief: tables, file paths and commands, not essays.

## Report format at every gate

1. What you ran.
2. What you found — VERIFIED, CONTRADICTED (with evidence) or UNKNOWN (with the
   command that would settle it).
3. Files you created or changed.
4. Risks.
5. The proposed next step, then wait for my approval.
