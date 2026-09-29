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
correct from the source reading alone). `audit-log-maxsize` is a `kube-apiserver-arg` list item in
`/etc/rancher/k3s/config.yaml` (`- audit-log-maxsize=100`), not a top-level YAML key — corrected
here after the first draft of this procedure got the file format wrong:
1. `stat -c '%a %U:%G %n' /var/log/nexus-audit/audit.log` — expect `640 root:adm`.
2. Edit `/etc/rancher/k3s/config.yaml`, change the list item to `- audit-log-maxsize=1` (MB, the
   smallest useful value). `sudo systemctl restart k3s`; wait for `kubectl get --raw /healthz`.
3. Loop ~500–1000 `kubectl create configmap verify-state-probe-<n> -n nexus-system --dry-run=server
   -o yaml >/dev/null` calls (the same dry-run probe `verify-state.sh` uses) to cross 1 MB quickly —
   only if no backup file has appeared within ~2 minutes of the restart.
4. Watch for a backup file (`audit-<timestamp>.log`) to appear.
5. `stat` both the new active file and the backup — **both must be `640 root:adm`**.
6. As the plain user, no `sudo`: `head -c1 /var/log/nexus-audit/audit.log` and the same on the
   backup — the real end-to-end proof; mode/ownership alone can look right while something else
   (a mount option, an LSM policy) still blocks the read.
7. Restore `- audit-log-maxsize=100`, `sudo systemctl restart k3s`, confirm every Application
   returns to `Synced`/`Healthy`.
8. Record the `stat` and read-test results here once run.

**Results (run 2026-09-26, against the from-empty M0-5 rebuild cluster):**
- Baseline (step 1): `640 root:adm`, and `head -c1` as the plain user (`azure`, no sudo) already
  confirmed readable by the same-day `verify-state.sh` runs — reused as the baseline rather than
  re-measured.
- Step 2/3 (owner, sudo): `audit-log-maxsize` changed `100` → `1`, `systemctl restart k3s`
  completed without error.
- Step 4: rotation happened immediately at restart, before any dry-run loop was needed — the
  existing `audit.log` (~29 MB, well over the new 1 MB threshold) was rotated out the moment the
  apiserver's audit writer reopened it. A second, size-triggered rotation followed shortly after
  as the new active file itself crossed 1 MB under normal write volume. The step-3 loop was not
  run.
- Step 5/7: all three files present after the test (the pre-test log, the restart-triggered
  backup, and the post-restart active file) read `640 root:adm`.
- Step 6: `head -c1` succeeded as `azure`, no sudo, on the active file and both backups.
- Step 9/10 (owner, sudo): restored to `100`, `systemctl restart k3s` completed without error.
- Post-restore: `kubectl get --raw /healthz` → `ok`; all 6 Applications `Synced`/`Healthy`, held
  stable for ≥60s; `verify-state.sh` re-run independently — **8/8**, exit 0.
- **Conclusion: the audit-log design (pre-created `640 root:adm`, mode propagated through every
  lumberjack rotation) holds under a real rotation, both restart-triggered and size-triggered, with
  no cluster impact.**

**Observed growth rate and effective retention (2026-09-26, informational — not part of the design
decision above):** the pre-test `audit.log` reached ~29 MB over the roughly 7.3 hours since the
from-empty rebuild created it — **~4 MB/h** at the audit volume this session generated (bootstrap
itself, repeated `verify-state.sh` runs including their dry-run audit probes, and normal API
traffic; not a measurement of steady-state idle load). At that rate, `audit-log-maxsize=100`
fills roughly every ~25h, and with `audit-log-maxbackup=10` the oldest backup is evicted after
roughly **10 days** of accumulated volume — well short of `audit-log-maxage=30` (days), which in
practice never binds at this volume because `maxbackup` deletes older files first. This is an M1
decision, not resolved here: raise `maxbackup` for longer retention, reduce audit volume via a
narrower policy, or accept the ~10-day effective window.

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
- The rotation test (above) has been run against the from-empty M0-5 rebuild cluster (2026-09-26):
  both a restart-triggered and a size-triggered rotation preserved `640 root:adm`, and non-sudo
  reads succeeded on every file. The design is no longer resting on source-code reading and
  documented POSIX semantics alone.

## Addendum (2026-09-27, GATE M1-4): k3s Secrets encryption at rest

- **Found:** encryption at rest was never configured. `/etc/rancher/k3s/config.yaml` has no
  `secrets-encryption` key, the k3s unit passes no flags, and the owner's
  `sudo k3s secrets-encrypt status`, run on 2026-09-27, reported verbatim
  `Encryption Status: Disabled, no configuration file found` (k3s's default). Every
  Secret on the live cluster, `grafana-admin` and the three dependency-db Secrets included, is
  stored unencrypted in the k3s datastore.
- **Decision:** `bootstrap.sh` writes `secrets-encryption: true` into the k3s config it creates
  before the install, so encryption is on from the first server start of the M1 exit rebuild. The
  live cluster is not changed: the rebuild replaces it, and rotating it in place would need `sudo`
  and a k3s restart for no lasting gain.
- **Check:** after the M1 exit rebuild (M1-6), the owner runs `sudo k3s secrets-encrypt status`
  and expects `Encryption Status: Enabled`. The agent cannot run it (root-only).
- **Result (2026-09-28, GATE M1-6 b6):** after the from-empty rebuild, the owner's
  `sudo k3s secrets-encrypt status` reported `Encryption Status: Enabled`, `Current Rotation
  Stage: start`, `Server Encryption Hashes: All hashes match`, active key `AES-CBC` `aescbckey`.
  k3s's default provider is AES-CBC; the Kubernetes documentation prefers secretbox or a KMS
  provider (Later item).

## Addendum (2026-09-28, M1-6): measured audit retention (changes 7 and 14)

**Question.** Change 7 accepted "about 10 days" of effective retention (the 2026-09-26 estimate
above, ~4 MB/h). Change 14: a 24 h measurement is valid only with an unchanged boot ID and k3s
start time, and no VM pause.

**Earlier evidence, before the window (old cluster, informational).** The rotated file names
give the time each 100 MiB file took to fill: 5.0 h (21:02 → 02:03Z, overnight), 6.0 h, 5.8 h,
4.8 h and 4.9 h (2026-09-26/27, including the M1-4 and M1-5 gates). That is about 400–500 MiB per
day, or about 2.2–2.8 days of retention, not 10.

**Method.** A 24 h window on the from-empty M1-6 cluster, opened after S5, with no dashboards, no
port-forwards and no agent `kubectl`:
- start (d1) 2026-09-28T06:11:24.709Z and end (d2) 2026-09-29T06:11:54.837Z: wall clock, `/proc/uptime`,
  `boot_id`, k3s `MainPID`/`ActiveEnterTimestamp`/`NRestarts`, the `/proc/mounts` field check, and
  `stat` of every file in `/var/log/nexus-audit/`;
- growth = Σ end sizes − Σ start sizes + Σ start sizes of files evicted in the window;
- daily growth = growth × 86,400 ÷ Δwall (as planned), and ÷ Δuptime (owner override D1, below);
- retention = (`maxbackup` 10 + the active file) × `maxsize` 100 MiB = 1,153,433,600 B ÷ daily
  growth.

**Validity.**
- `boot_id` unchanged (`e059f2de…`); k3s `MainPID` 598820, `ActiveEnterTimestamp` and
  `NRestarts` 0 unchanged; `/proc/mounts` field check 0 at both ends; Δwall = 86,430.1 s
  (≥ 86,400); DB pod uid and restartCount unchanged; 7/7 Applications Synced/Healthy at the end.
- **VM-pause rule failed:** \|Δwall − Δuptime\| = 86,430.1 − 83,508.2 = **2,921.9 s** (limit 60).
  The guard first found it on 2026-09-29 before d2: 2,858.3 s at 04:05:38Z, 2,883.2 s at
  05:03:23Z, then 2,921.9 s at d2, growing about 25–34 s/h. `boot_id` never changed. When the
  pauses happened is unknown (S5, just before the window, had 3.71 s).
- **Owner statement (check 7):** "no dashboards or port-forwards were open during the window.
  The sleep setting was off, but the VM paused anyway (about 2,922 s, still growing about
  34 s/h); covered by D1."

**Owner override D1 (2026-09-29): the window is kept, uptime-normalized.** Under the planned
rule the window would be discarded. The owner kept it instead, with no new window:
- A paused VM runs nothing, so the API server writes no audit events while it is frozen. The
  bytes counted are the bytes written in 83,508.2 s of running time, so growth ÷ Δuptime is the
  true write rate, and growth ÷ Δwall understates it by the pause share (3.4 %).
- The uptime rate is therefore valid and conservative (it gives the shorter retention).
  Retention uses it; both are reported.
- Every other validity check still applies and passed.

**Result** (`~/nexus-evidence/m1-6/audit-window-result.txt`, `audit-window-result-d1.txt`).
- Growth **509,060,759 B**: end 1,076,382,232 − start 598,632,643 + 31,311,170 evicted (the two
  2026-09-26 backups). The oldest file at the end existed at the start, so no file created in the
  window was evicted. `maxbackup=10` was reached (11 files).

| Rate | B/s | Daily growth | Retention |
|---|---|---|---|
| per wall-clock second (planned) | 5,889.9 | 485.3 MiB/day | 2.27 days |
| **per uptime second (D1, used)** | **6,095.9** | **502.3 MiB/day** | **2.19 days** |

- **CONTRADICTED:** change 7's "about 10 days". The measured 2.19 days agrees with the
  pre-window estimate of 2.2–2.8 days. The window was an idle cluster (no agent `kubectl`, no
  dashboards), so load shortens it further.

**Decision (owner, M1-6 plan gate, G2 option a).** The cluster was rebuilt unchanged; this
addendum records the measured retention. Raising `maxbackup` (or narrowing the policy) is a Later
item: at about 500 MiB per idle day, 7 days needs `maxbackup` of about 35, more under load; it is
changed at a planned k3s restart. §14 keeps the per-run extract, not the raw log, so retention bounds how long after a run the
extract can still be taken, not what is kept. M4 re-measures under experiment load.

**Also found at M1-6.** Step a of `bootstrap.sh` runs `install -m 0640 -o root -g adm /dev/null`
on `audit.log` unconditionally, so a rebuild empties the previous cluster's active audit log.
Rotated backups survive, and the new cluster's rotations evict them. The M1-6 rebuild archived
the directory first (`~/nexus-evidence/m1-6/audit-pre-rebuild/`, byte-identical).
