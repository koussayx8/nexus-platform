# nexus-revert — C8, the ready revert pull request

ADR-041 (draft #237); `TASKS.md` D8, D15; Block 6. When an Incident ends `Escalated` with
`recommendation.kind: git_revert`, NEXUS prepares a pull request that reverts **one validated commit**
on the branch the target's Argo CD application tracks. The model can at most *name* the commit; the
diff is a plain `git revert` computed here. **NEXUS never merges**: the token has no write on
`koussayx8/nexus-platform`, and this package has no merge, auto-merge, review, label or comment call.

## Where it runs

**In the bot pod** (`nexus-bot` namespace; ADR-041 decision 1, which overrides spec:1005, X13), as a
library the bot's loop calls (`Service.tick`), or on its own with `python -m nexus_revert.main` (log
only) until the bot core (#252) is merged. **Not in the operator**: the operator keeps no Git
credential and no public egress.

| Change it needs | Where | In this PR |
|---|---|---|
| `git` in the bot image | `bot/Dockerfile` (#252): `apt-get install --no-install-recommends git` in the final stage | no — #252 is not on `dev` |
| The GitHub token as a file | Secret `nexus-bot/nexus-bot-github` (key `token`) at `/var/run/secrets/nexus-bot/github/token` | no (owner, after the token test) |
| The config | key `revert.yaml` in `nexus-bot-config` (`revert.example.yaml` here) at `/etc/nexus-bot/revert.yaml` | example only |
| A writable scratch directory (root FS is read-only) | `emptyDir` at `/tmp` (`NEXUS_REVERT_SCRATCH`) | no |
| Egress to `github.com` and `api.github.com` on 443 | already allowed by #252's `bot-egress` (public TCP 443) | nothing new |
| RBAC | none new: `get`/`list` on Incidents in `nexus-system` (#252's `nexus-bot-read`). No write on Incidents | nothing new |
| The operator's NetworkPolicy | **unchanged.** `operator-egress` (`platform/network/system.yaml`) keeps no public 443; its comment ("the spec's public-443 egress is for the SHOULD fork-PR feature and is omitted") stays true. Running C8 in the operator would need that public-443 rule (as `reasoner.yaml`'s) and the token mounted there; ADR-041 rejects both | not touched |

## What it does

1. **Trigger**: phase `Escalated`, `status.proposal.actionId: escalate`, `recommendation.kind: git_revert`,
   a `Deployment` in a mapped namespace. Map (static, in Git): `nexus-dev` → `experiment/dev-state`,
   `overlays/dev`, app `sample-api-dev`. `nexus-prod` → `main` is config only, commented out (cut first).
2. **Candidate**: exactly one 40-hex SHA in `recommendation.detail` (untrusted), else **rule A1**: the
   newest commit on the tracked branch touching the overlay. A1 tries no older commit.
3. **Checks** (first failure: no push, no PR, one line naming the check):

| # | Check |
|---|---|
| 1 | a full 40-hex SHA |
| 2 | recent: in the upstream branch's last 50 commits (anonymous `fetch --depth 50`), an ancestor of its tip, **and** committed within 24 h (`maxAgeHours`, committer date) |
| 3 | exactly one parent (merges and the shallow boundary are refused) |
| 4 | changes at least one file, **every** file under the overlay (A2; `overlays/dev-evil/` is outside) |
| 4b | every file is a sample-api workload file (`allow`: `overlays/dev/kustomization.yaml`, `overlays/dev/sample-api*.yaml`, same depth; the set the S3 runner allows) and none is in `exclude` (`overlays/dev/namespace.yaml`, the autonomy level). Owner, 2026-11-19; ADR-041 line in #237 |
| 5 | `git revert --no-commit` applies without conflict and is not empty |
| 6 | no open `nexus/revert-*` PR by the machine account on that branch, and the branch is new in the fork |

4. **Revert**: fixed message `revert(<overlay>): revert <sha>` / `This reverts commit <sha>.`; author =
   the machine account. Pushed to the **fork** as `nexus/revert-<incident>`, never forced.
5. **Pull request** upstream, ready (not draft), `maintainer_can_modify: false`, title and body from the
   ADR template; no Reasoner free text. `prMode: compare_link` (outcome C), or a refused create, posts
   the compare link instead.
6. **Merge recognition**: poll every 30 s; "merged by `<login>`, merge commit `<sha>`", "closed, not
   merged", or 24 h timeout. After a restart, open PRs are found again by author and head prefix.
   **As ADR-041 (decision 10, limit 6) and the owner's answer of 2026-11-19: a notification only** (log;
   chat after #252). The Incident stays `Escalated`; nothing is written to it or to the Flight
   Recorder; the export reads `created_at` / `merged_at` from GitHub.

Git runs with no system/global config, `core.hooksPath=/dev/null`, https only, no prompts, no
credential helper, in a fresh scratch directory that is deleted after each attempt. The token is sent
only on the push, as an `http.<fork URL>.extraHeader` in `GIT_CONFIG_*` environment variables: never
in argv, a URL or a log line.

## Owner steps: the 10-minute token test (ADR-041, before Block 6 continues)

Nothing is merged; the test PR is closed at the end. Keep the token out of shell history and argv:
it is read at a silent prompt into a file under `~/.nexus/`, and every command below reads it from
there inside a pipe.

1. **Machine account**: a new GitHub account (two-step verification on), not a collaborator on
   `koussayx8/nexus-platform`, in no team. Fork the repository **with all branches** (untick "Copy the
   `main` branch only"). In the fork: Settings → Actions → General → **Disable actions**.
2. **Fine-grained token first** (machine account → Settings → Developer settings → Fine-grained
   tokens): resource owner the machine account; repository access **Only select repositories → the
   fork**; permissions **Contents: Read and write, Pull requests: Read and write, Metadata: Read**.
   Save it:

   ```bash
   read -rs -p 'GitHub token: ' T && echo && (umask 077 && printf '%s' "$T" > ~/.nexus/github-test.token); unset T
   ```

3. **Push an empty commit to the fork** (replace `MACHINE`):

   ```bash
   export M=MACHINE && cd "$(mktemp -d)" && git init -q && git fetch -q --depth 1 https://github.com/koussayx8/nexus-platform.git experiment/dev-state && git checkout -q FETCH_HEAD && git -c user.name=nexus-test -c user.email=nexus-test@invalid commit -q --allow-empty -m "test: token test, empty" && export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraHeader GIT_CONFIG_VALUE_0="Authorization: Basic $(printf '%s:%s' "$M" "$(cat ~/.nexus/github-test.token)" | base64 -w0)" && git push "https://github.com/$M/nexus-platform.git" HEAD:refs/heads/nexus/token-test
   ```

   Expected: accepted.
4. **Open the upstream PR — the question:**

   ```bash
   printf 'header = "Authorization: Bearer %s"\n' "$(cat ~/.nexus/github-test.token)" | curl -sS -K - -o ~/token-test-pr.json -w '%{http_code}\n' -X POST -H 'Accept: application/vnd.github+json' https://api.github.com/repos/koussayx8/nexus-platform/pulls -d "{\"title\":\"token test, close me\",\"head\":\"$M:nexus/token-test\",\"base\":\"experiment/dev-state\",\"maintainer_can_modify\":false}"
   ```

   `201` = outcome A (the PR number is in `~/token-test-pr.json`). `403`/`404`/`422` = refused (ADR-041
   expects this for a fine-grained token: UNVERIFIED).
5. **Negative checks, same token. All three must hold; if one does not, stop: decision 3 is broken.**
   No merge call is made at all.
   - The token has no push right upstream: `permissions.push` must print `false`:

     ```bash
     printf 'header = "Authorization: Bearer %s"\n' "$(cat ~/.nexus/github-test.token)" | curl -sS -K - -H 'Accept: application/vnd.github+json' https://api.github.com/repos/koussayx8/nexus-platform | python3 -c 'import json,sys; print(json.load(sys.stdin).get("permissions",{}).get("push"))'
     ```

   - A push of a throwaway ref to the upstream is refused with `403` (still in step 3's directory, with
     its `GIT_CONFIG_*` header set for all of github.com):

     ```bash
     git push https://github.com/koussayx8/nexus-platform.git HEAD:refs/heads/nexus/token-test-throwaway
     ```

     Expected: `The requested URL returned error: 403`. Anything else (above all, success): stop.
   - Steps 3 and 4 succeeded for the **fork** push and the upstream PR (outcome A), or will in step 6.

6. **Only if step 4 was refused: the classic token**, scope **`public_repo` only**. Save it over the
   same file (step 2's command), then repeat steps 3 to 5. Expected: 3 and 4 succeed, and step 5's
   `permissions.push` is `false` and its upstream push gets `403` (outcome B). If 4 is still refused: outcome C, `prMode: compare_link`.
7. **Clean up**: close the test PR in the browser (do not merge); delete `nexus/token-test` in the fork;
   `unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0`; revoke the token you will not use.
   Record the outcome (A, B or C) and the HTTP statuses of steps 4 and 5 in ADR-041.

Also (owner, ADR-041 U2): upstream Settings → Actions → General → "Require approval for fork pull
request workflows" stays on.

## Development (offline)

```bash
pip install -r nexusctl/requirements-dev.txt -e libs/nexus-client -e nexusctl -e libs/nexus-revert
cd libs/nexus-revert && export KUBECONFIG=/nonexistent && pytest -q
```

Git is real, on `file://` fixture repositories under the test's tmp directory (file:// is enabled
for tests only); GitHub is an in-memory fake, plus one fake API server on 127.0.0.1.
