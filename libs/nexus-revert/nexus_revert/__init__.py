"""nexus-revert: C8, the ready revert pull request (ADR-041; TASKS.md D8, D15).

When an Incident ends Escalated with recommendation git_revert, compute a mechanical `git revert`
of one validated commit on the branch the target's Argo CD application tracks, push it to the
machine account's fork, and open a pull request upstream. NEXUS never merges: the token has no
upstream write, and no merge call exists in this package. Runs in the bot pod (ADR-041 decision 1),
never in the operator.
"""
