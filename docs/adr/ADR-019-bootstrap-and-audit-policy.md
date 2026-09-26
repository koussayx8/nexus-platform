# ADR-019: Bootstrap Order, the §14 Audit Policy, and the Audit-Log Access Design

## Status: Accepted

## Context
M0-5 needs a `bootstrap.sh` that takes an empty k3s node to the full M0 target state (spec §25),
and the audit-log flags spec §14 requires but leaves unpinned (path, rotation values, non-sudo
read access). This ADR records both, plus the review findings that changed the design from a
first draft: an ACL-on-directory approach for the audit log turned out not to work; several checks
and secret-handling steps had real bugs (vacuous passes, reading from the wrong git ref, writing
secrets in ways that could reach argv or a `kubectl apply` diff) caught before merge; and the
review asked several upstream facts be verified against source rather than assumed.

## The §14 audit policy (verbatim)

```yaml
apiVersion: audit.k8s.io/v1
kind: Policy
omitStages:
  - RequestReceived
rules:
  - level: RequestResponse
    users: ["system:serviceaccount:nexus-system:nexus-operator"]
    resources:
      - group: apps
        resources: ["deployments/scale"]
      - group: ""
        resources: ["pods/eviction"]
      - group: nexus.io
        resources: ["incidents", "incidents/status"]
  - level: RequestResponse
    userGroups: ["nexus-approvers"]
    resources:
      - group: nexus.io
        resources: ["incidents"]
  - level: Metadata
    verbs: ["create", "update", "patch", "delete", "deletecollection"]
  - level: None
```
Written to `/etc/rancher/k3s/audit-policy.yaml` by `bootstrap.sh`. `nexus-operator` and the
`incidents` CRD don't exist until M1 — an audit rule that matches nothing yet is inert, not wrong.

## Flags not mandated by the spec (a project decision, not a spec requirement)
`--audit-log-maxage=30 --audit-log-maxbackup=10 --audit-log-maxsize=100`, path
`/var/log/nexus-audit/audit.log`. Written into `/etc/rancher/k3s/config.yaml`
(`kube-apiserver-arg:` list), not baked into `INSTALL_K3S_EXEC`, so they can be edited and applied
with `systemctl restart k3s` alone — this is also what makes the rotation test (below) practical
without a reinstall.

## Audit-log access without sudo

**The first design (a default ACL on the log directory) does not work, and was replaced before
being written into the script.** Two independent reasons, both verified against source rather than
assumed:
- `k8s.io/apiserver`'s `audit.go` builds the writer as `&lumberjack.Logger{Filename: o.Path,
  MaxAge: o.MaxAge, MaxBackups: o.MaxBackups, MaxSize: o.MaxSize}` (`gopkg.in/natefinch/
  lumberjack.v2`). Its `openNew()` (v2.2.1, read in full) opens every file — first creation and
  every rotation alike — with `os.OpenFile(name, O_CREATE|O_WRONLY|O_TRUNC, mode)`, where `mode`
  defaults to the hardcoded `0600` unless a file already exists at that path, in which case it
  copies that file's mode. It never reads or writes a POSIX ACL — `os.FileInfo.Mode()` cannot see
  one.
- Separately, POSIX ACL semantics (`acl(5)`) intersect an inherited default ACL's permissions with
  the *creation mode* for the group class. A file always opened at mode `0600` (group bits `000`)
  has any inherited named-user/group grant masked down to `---` at the moment of creation,
  independent of the parent directory's default ACL. (Not exercised live in this sandbox —
  `setfacl`/`getfacl` aren't installed and installing them needs `sudo`, which is out of scope for
  an agent session; this rests on the documented POSIX rule and the lumberjack source, not a live
  test.)

**Design used instead:** `bootstrap.sh` pre-creates `/var/log/nexus-audit/audit.log` as
`root:adm 0640`, *before* k3s's first start. Lumberjack's `openNew()` finds that file at its very
first open, copies its plain Unix mode (`0640`, not ACLs — a mode never gets reset to `0600` once a
file already exists there), and — because `openNew()` runs the `os.Stat` check *before* renaming
the outgoing file away — every subsequent rotation copies the mode of the file being replaced, so
`0640` propagates forever. The invoking account is already a member of `adm`; no group change is
needed for this machine. (Caveat for a different machine: group membership is read at login time —
adding a *different* user to `adm` would need a fresh login or `newgrp adm` before a same-session
read would succeed; a `sudo usermod -aG` in the same shell would not take effect immediately.)

**Rotation test procedure** (run at the real rebuild, per the owner's instruction — not assumed
correct from the source reading alone):
1. `stat -c '%a %U:%G %n' /var/log/nexus-audit/audit.log` — expect `640 root:adm`.
2. Edit `/etc/rancher/k3s/config.yaml`, set `audit-log-maxsize: 1` (MB, the smallest useful value).
   `sudo systemctl restart k3s`; wait for `kubectl get --raw /healthz`.
3. Loop ~500–1000 `kubectl create configmap verify-state-probe-<n> -n nexus-system --dry-run=server
   -o yaml >/dev/null` calls (the same dry-run probe `verify-state.sh` uses) to cross 1 MB quickly.
4. Watch for a backup file (`audit-<timestamp>.log`) to appear.
5. `stat` both the new active file and the backup — **both must be `640 root:adm`**.
6. As the plain user, no `sudo`: `head -c1 /var/log/nexus-audit/audit.log` and the same on the
   backup — the real end-to-end proof; mode/ownership alone can look right while something else
   (a mount option, an LSM policy) still blocks the read.
7. Restore `audit-log-maxsize: 100`, `sudo systemctl restart k3s`, confirm every Application
   returns to `Synced`/`Healthy`.
8. Record the `stat` and read-test results here once run.

## The audit-growth check is a probe, not "is it growing"

Policy rule 3 logs *every* write, including routine lease renewals — "the log is growing" is true
at all times regardless of whether anything meaningful happened, so it proves nothing.
`verify-state.sh` instead issues one `--dry-run=server` create with a unique name (a real, audited
`create` request that persists nothing) and confirms that exact event lands in the audit log, plus
a before/after delta on `apiserver_audit_event_total` (`kubectl get --raw /metrics`, read directly —
kube-apiserver scraping is disabled in the observability chart values, but the bootstrap kubeconfig
already has cluster-admin access to the raw endpoint).

## k3s and ArgoCD: version pin and install source

`INSTALL_K3S_VERSION=v1.34.6+k3s1` set explicitly. Fetching the tagged `install.sh` alone is not
enough to pin the binary — read directly from the fetched script: it resolves a version from
`https://update.k3s.io/v1-release/channels/stable` whenever `INSTALL_K3S_VERSION` (or
`INSTALL_K3S_COMMIT`/`INSTALL_K3S_PR`) isn't set. `bootstrap.sh` also refuses to run the k3s step
if `k3s` or `/etc/systemd/system/k3s.service` is already present (the ADR-014 rebuild order runs
the uninstall first; this is a hard stop, not a silent reinstall over a live cluster).

ArgoCD `v3.3.8` is installed with `kubectl apply --server-side --force-conflicts`, matching
ArgoCD's own installation docs for its CRD manifest (the same 262144-byte last-applied-annotation
limit its full manifest can exceed). Checked its install manifest directly (`curl` + `grep
image:`): `quay.io/argoproj/argocd`, `ghcr.io/dexidp/dex`, `public.ecr.aws/docker/library/redis` —
zero `docker.io` images from ArgoCD itself.

## Docker Hub pull estimate

Traced every image, not guessed. `k3s v1.34.6+k3s1`'s own release asset `k3s-images.txt` lists
eight images, all `docker.io/rancher/*`; with `--disable traefik --disable servicelb` and no use of
k3s's own `HelmChart` controller, three of the eight (traefik, klipper-lb, klipper-helm) are never
pulled, but five are: coredns, metrics-server, `local-path-provisioner`, `library-busybox`
(the provisioner's helper pod), and `pause` (every pod's sandbox). `helm template` of both pinned
charts against our actual values files adds exactly one more: `docker.io/grafana/grafana` (the
chart's sidecar image moved to `quay.io/kiwigrid/k8s-sidecar` in the pinned chart version, so it
does **not** add a second `docker.io` pull). **Six `docker.io` pulls per full bootstrap.** At the
confirmed current policy (100 pulls/6h anonymous, 200/6h free-authenticated, both IP-scoped), that
is roughly 16–33 full rebuilds inside a 6-hour window before a pull would fail — plausible during an
active debugging session on this exact script, not a theoretical concern. `bootstrap.sh` supports
an **optional** hedge: if `~/.nexus/dockerhub.env` (`DOCKERHUB_USER`/`DOCKERHUB_TOKEN`) exists, it
writes `/etc/rancher/k3s/registries.yaml` (`0600 root:root`) with one `docker.io` auth entry, which
covers every `docker.io/*` repository uniformly. Absent that file, nothing changes. Recommended if
doing several rebuild attempts in a row.

## Bootstrap order and why

a. k3s → b. kubeconfig → c. monitoring namespace + `grafana-admin` → d. ArgoCD → e. merge-order
guard, then the AppProject and `root.yaml` → f. wait for `platform` → g. `nexus-killswitch` +
`nexus-operator-config` → h. wait for every Application → i. `verify-state.sh`.

`platform` must be `Synced`/`Healthy` (step f) before step g, because `platform` owns
`nexus-system` (`platform/namespaces/namespaces.yaml`) — the Kill Switch ConfigMaps can't be
created in a namespace that doesn't exist yet. Both ConfigMaps are applied directly by
`bootstrap.sh`, never through ArgoCD (spec line 924): reconciliation must never be able to undo an
emergency stop or an experiment reset. Both are **create-only-if-absent** — a re-run must not reset
a halted Kill Switch back to `active`.

## Merge-order guard

`bootstrap.sh` refuses to apply the AppProject/root Application unless: `origin/main` already
contains the M0-4/M0-5 convergence (checked via `git cat-file -e origin/main:<path>` on four marker
paths — `root.yaml`, the AppProject, `platform/kyverno/values.yaml`,
`platform/bootstrap-templates/nexus-killswitch.yaml` — self-documenting, no commit SHA to go
stale), and `origin/main` is already merged into `origin/experiment/dev-state`
(`git merge-base --is-ancestor`). Both direct applies (the AppProject and `root.yaml` — everything
else is pulled by ArgoCD's own repo-server straight from GitHub, never from the local clone) are
read with `git show origin/main:<path>`, not the local checkout: this guarantees `bootstrap.sh`
applies exactly the bytes ArgoCD itself will also fetch from `main`, independent of whatever branch
happens to be checked out on the machine running it. `--plan` prints these commands but runs none
of them — not even `git fetch` — confirmed by running `--plan` with every one of `k3s`, `kubectl`,
`helm`, `git`, `sudo`, `curl`, `systemctl`, `install` replaced by a stub that logs its own
invocation and exits 1: the log stayed empty, and `--plan`'s output was byte-identical with and
without the stubs on `PATH`.

## Not applied: `argocd-cm-patch.yaml`

Read in full: every line (the tracking-method setting, the `ProviderConfigUsage` exclusion, all
five `ignoreResourceUpdates` entries) exists only because of Crossplane, which is parked and
outside the M0-5 target Application set. `bootstrap.sh` applies none of it. It is reintroduced,
narrowed to what's actually needed, in whichever milestone resumes Crossplane — consistent with the
new standing rule that the AppProject (and, by the same logic, `argocd-cm`) widens only in the PR
that needs the change.

## Secret contracts

**`grafana-admin`** is a value NEXUS chooses and can legitimately keep stable across rebuilds:
reuse the file at `~/.nexus/grafana-admin` if present (rotation is deleting the file), generate
with `python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))'` if not (no
pipe, no trailing newline, `umask 077`). The Secret is created if absent; deleted and recreated
only if the value was *freshly generated this run* (i.e. the file didn't exist before); otherwise
untouched — never `kubectl apply`, which would make a rotation attempt silently reach the cluster
even when the file was reused unchanged, or (worse) leave a stale Secret in place when the file
*was* rotated. On the delete-and-recreate branch, `kubectl rollout restart
deployment/observability-grafana` follows — confirmed correct by `helm template` (the actual
rendered Deployment name), and safe because the chart's `persistence.enabled` is `false` by default
and unoverridden in our values (`helm show values` on the pinned `grafana-community/grafana@12.4.4`
confirms), so Grafana's DB is ephemeral and a restart alone re-bootstraps the admin user against
the new password.

**`argocd-admin`** is different: it is not a value NEXUS controls, it is a copy of whatever ArgoCD
randomly generated at install time. Since the k3s step already refuses to run against an existing
install, every real bootstrap follows a genuine reinstall, so `argocd-initial-admin-secret` is
always fresh — reusing an old file here would be reading a password for a cluster that no longer
exists. It is therefore always overwritten, written to a temp file first
(`~/.nexus/.argocd-admin.tmp.$$`, `umask 077`) with `trap 'rm -f "$tmp"' EXIT` so it can't be left
behind on any exit path, checked non-empty, and only then moved into place — a failed or empty read
is **fatal** (the script aborts), not a warning, since a bootstrap that silently proceeds without a
usable admin credential is worse than one that stops.

## Verify-state design points carried over from review

`verify-state.sh`'s Application check now expects six names, not five — `root` itself is a real
Application object bootstrap-only or not, and should also reach `Synced`/`Healthy`. Its sample-api
digest check reads `origin/experiment/dev-state` for `nexus-dev` and `origin/main` for
`nexus-prod` (ADR-013/ADR-017 — each Application tracks a different branch; reading the local
checkout would check neither). Its namespace-level check treats a namespace that doesn't exist as
a failure even where the wanted label is empty (`nexus-system`/`-reasoner`/`-load`) — the earlier
version conflated "namespace absent" with "namespace exists, unlabeled," which vacuously passed all
three on every pre-rebuild run. Pod readiness reads `containerStatuses[].ready`, not phase, and
explicitly skips `Succeeded` pods (a completed Job has no ready containers to check) while still
failing `Failed` pods outright.

## Consequences
- `bootstrap.sh` is a mutation script and does not source `scripts/lib/readonly.sh` — nearly
  everything it does is exactly what that library's wrappers exist to forbid. It uses `kubectl`/
  `git` directly throughout, under the Cluster Admin's own authorization to run it (rule 6: it is
  never invoked through an agent's tool).
- The rotation test (above) is real evidence, not yet collected — it happens at the actual rebuild.
  Until then, the audit-log design rests on source-code reading and documented POSIX semantics,
  which is weaker than a live test and is flagged as such rather than asserted as proven.
