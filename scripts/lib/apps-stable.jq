# apps-stable.jq — the shared "every expected Application is stable at the expected commit"
# predicate (TASKS.md M1-3 commit 1), used by bootstrap.sh step h and verify-state.sh M1.
#
# A pure jq filter, not a shell wrapper: bootstrap.sh uses it without sourcing readonly.sh.
# Input: one `kubectl get applications.argoproj.io -n argocd -o json` snapshot on stdin.
# Args:
#   --arg names    "<space-separated Application names>"
#   --arg repo     <this repository's Git repoURL, exactly as the Applications spell it>
#   --arg main     <origin/main SHA>
#   --arg devstate <origin/experiment/dev-state SHA>
#   --arg detail 1 (optional) print the per-app evaluation instead of the bare boolean
# Output: true or false; with detail=1, {stable, apps: [{name, sync, health, expected, observed, ok}]}.
#
# True only if every expected Application, in this one snapshot, is Synced and Healthy and at the
# expected commit: `devstate` for sample-api-dev, `main` for every other Application. `Synced`
# alone is relative to the last revision ArgoCD fetched, so for up to ~180 s after a merge every
# app is Synced/Healthy at the old commit; the revision check is what makes the predicate mean
# "the merge has landed".
#   - Single source: `.status.sync.revision`, and `.spec.source.repoURL` must equal `repo`.
#   - Multi-source: every `.status.sync.revisions[i]` whose `.spec.sources[i].repoURL` equals
#     `repo`, by index; chart sources are skipped.
#   - No Git source, or a `revisions` array shorter than `sources`: false.

def expected_commit: if . == "sample-api-dev" then $devstate else $main end;

# The observed revisions of the app's Git sources, or null when there is none to compare.
def git_revisions:
  if (.spec.sources // null) != null then
    [.spec.sources | to_entries[] | select(.value.repoURL == $repo) | .key] as $idx
    | (.status.sync.revisions // []) as $revs
    | if ($idx | length) == 0 or ($revs | length) < (.spec.sources | length) then null
      else [$idx[] | $revs[.]] end
  elif (.spec.source.repoURL // null) == $repo then [.status.sync.revision]
  else null end;

($names | split(" ") | map(select(length > 0))) as $want
| ([.items[]? | {key: .metadata.name, value: .}] | from_entries) as $apps
| [ $want[] as $n
    | ($apps[$n] // null) as $a
    | ($n | expected_commit) as $exp
    | if $a == null then
        {name: $n, sync: null, health: null, expected: $exp, observed: null, ok: false}
      else
        ($a | git_revisions) as $obs
        | {name: $n,
           sync: $a.status.sync.status,
           health: $a.status.health.status,
           expected: $exp,
           observed: $obs,
           ok: ($a.status.sync.status == "Synced"
                and $a.status.health.status == "Healthy"
                and $exp != ""
                and $obs != null
                and ($obs | all(. == $exp)))}
      end ] as $rows
| (($want | length) > 0 and ($rows | all(.ok))) as $stable
| if ($ARGS.named.detail // "") == "1" then {stable: $stable, apps: $rows} else $stable end
