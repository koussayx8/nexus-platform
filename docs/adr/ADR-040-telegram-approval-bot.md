# ADR-040: The Telegram Approval Bot (OUTLINE)

**Status:** Outline — draft follows in the next commit
**Date:** 2026-11-16
**Spec:** §10, §11, §16A, §18, §24; `TASKS.md` D9, D21, D22

## Outline

1. **Context** — D9, D21, D22; review plan Part 3 (3.1, 3.2); M3 boundary review section 5; what
   exists today (`nexusctl --as`, Role `nexus-approvers`, K5, K6, CRD C1–C2).
2. **Decisions** (table)
   - Transport: outbound long polling only, no webhook, no ingress.
   - Who may press: Telegram user-ID allowlist, private chat only.
   - Identity: the bot impersonates `koussay-approver` / `nexus-approvers`; the exact ClusterRole,
     limited by `resourceNames`; reads under the bot's own ServiceAccount.
   - Where the credential lives: namespace `nexus-bot`, never the operator pod or `nexus-system`.
   - Button payload: action letter + short Incident key + 32 hex of the proposal hash; re-check
     against the live Incident before writing `spec.approval`.
   - Read-only commands: `/status`, `/pods`, `/incident`.
   - Note flow: reject, then a new human Incident with `spec.source.note`.
   - `/ask`: one snapshot, one LLM call, advice only.
   - Rendering: plain text, no Markdown.
3. **RBAC** — the exact YAML.
4. **Button payload and the press sequence.**
5. **Commands, Note flow, `/ask`.**
6. **Threat model** (table) and **stated limits**.
7. **Contradictions with the spec and the sources** — for spec v1.1.
8. **Not verified** — each with the command that settles it.
9. **Build checklist, Block 7 (and Block 8)** — files, tests, hours.
10. **Consequences.**
