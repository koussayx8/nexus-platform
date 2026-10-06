## Summary

<!-- What changes and why, in a few lines. One concern per pull request. -->

## Type

- [ ] `feat` — new behaviour
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `test` / `ci` / `chore` — tooling, no behaviour change

## Context

<!-- Link the TASKS.md phase, ADR or issue this belongs to. A new decision needs an ADR in docs/adr/. -->

## Checklist

- [ ] The pull request targets `dev` (`main` is promoted from `dev` by a gate pull request).
- [ ] `repo-checks` is green.
- [ ] Desired state changes only through Git; nothing was changed in the cluster by hand.
- [ ] No secrets, tokens or kubeconfig content in the diff, the description or the logs.
- [ ] Tests cover the change (or none are needed, and the description says why).
- [ ] Documentation is updated: README, an ADR, or `CHANGELOG.md` under *Unreleased*.
- [ ] Anything out of scope that I found is noted in the "Later" section of `TASKS.md`, not in this change.

## Risks and rollback

<!-- What could break, what it touches (workloads, RBAC, policies, images), and how to undo it. -->
