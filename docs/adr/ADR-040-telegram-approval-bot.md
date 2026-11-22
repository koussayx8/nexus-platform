# ADR-040: The Telegram Approval Bot

**Status:** Proposed (accepted when its pull request, #237, merges). Owner answers of 2026-11-17 are recorded below.
Aligned on 2026-11-21 with the bot core as built in #252 (draft until Block 7); "As built" below lists every point
where the build differs from the first draft, and the decisions now say what #252 does.
**Date:** 2026-11-16
**Spec:** §10 (trust model), §11 (RBAC, K5, K6), §16A (`nexusctl`, approval), §18 (network), §24 (threats).
A second approval interface beside `nexusctl`. Spec v1.1 (#203, ADR-048) states the decision (rows D9 and X14) and
names this ADR as pending; the detail here (namespace, grants, `spec.source.note`) enters spec §3, §11 and §18 when
it is accepted.
**Decisions it implements:** `TASKS.md` D9 (the bot), D21 (read-only commands), D22 (`/ask`).
**Built in:** Block 7 (bot core), Block 8 (Note flow, `/ask`).

Labels: **VERIFIED** = read in a file on `origin/dev` at `4b077fb` in this session, cited. **UNVERIFIED** =
not exercised; the settling command is named. No cluster command was run for this ADR (offline only).

## Context

The owner approves level-2 proposals with `nexusctl approve`, from a terminal, acting as `koussay-approver` in
group `nexus-approvers` through impersonation (`nexusctl/README.md:32-37`). D9 adds a chat channel so that the
same decision can be made from a phone.

The designs are in `~/nexus-reports/fable-review-plan-2026-11-12.md` Part 3 (3.1, 3.2) and in section 5 of
`docs/reviews/2026-11-15-m3-boundary.md` (PR #233, not yet on `dev`).

What the approval path already enforces, outside any client (VERIFIED):

| Layer | What it does | Where |
|---|---|---|
| Role `nexus-approvers` (`nexus-system`) | get, list, watch, create, patch on Incidents; get on two ConfigMaps. No status, no delete | `platform/policies/approvers-rbac.yaml:20-34` |
| K5 `stamp-approver` | `approvedBy` and `approvedAt` come from the request identity and clock | `k5-incident-approval.yaml:28-47` |
| K5 `approvers-only`, `expiry-max-15m`, `bind-proposal-hash`, `bind-action`, `bind-replicas` | Only the group may approve; at most 15 minutes; the approval equals the Incident's own proposal | `k5-incident-approval.yaml:3-14` |
| K6 `human-approvers` | `source.type: human` only from the group | `k6-incident-source.yaml:55-74` |
| CRD C1, C2 | `spec.source` and `spec.target` immutable; `spec.approval` write-once | `platform/crds/incidents.nexus.io.yaml:57-63` |

The bot is therefore a **client**, like `nexusctl`. It authorises nothing. A bot that sends a wrong approval is
refused by K5; a bot that sends a right one adds no power that `nexusctl --as` does not already have.

## Owner answers, 2026-11-17 (#237)

| Point | Answer |
|---|---|
| Namespace `nexus-bot` (decision 1, B1) | Accepted |
| Reads under the bot's own read-only Roles; impersonation only for the three writes (decision 5, B2) | Accepted: least privilege and a clearer audit trail than the plan |
| `/ask` calls Groq directly from the bot (decision 10) | Accepted on two conditions: a **separate Groq API key in its own Secret**, so runs and `/ask` never share a key or its limits; and `/ask` **disabled on run days**. It stays cut-first |
| The CRD needs `spec.source.note` before the Note flow (B4) | Recorded: Block 8, cut-first with the Note flow |

## Decisions

| # | Decision |
|---|---|
| 1 | **One new bounded component, the bot.** One Deployment, one replica, ServiceAccount `nexus-bot`, in a **new namespace `nexus-bot`** (`bot/k8s/`). It holds the Telegram token (Secret `nexus-bot-telegram`, file `/var/run/secrets/nexus-bot/telegram/token`) and, for C8, the GitHub token (Secret `nexus-bot-github`, ADR-041). The operator gains no credential and no network route. |
| 2 | **Outbound-only long polling.** The bot calls `getUpdates` (timeout 50 s, `allowed_updates: ["message", "callback_query"]`). No webhook, no Service, no Ingress, no inbound NetworkPolicy rule. At start it calls `deleteWebhook`, so a webhook set with a stolen token does not silently take the updates. |
| 3 | **Who may press: a Telegram user-ID allowlist.** An update is handled only when `from.id` is in the allowlist **and** the chat is private (`chat.type == "private"`, `chat.id == from.id`). Anything else is dropped and logged (IDs only, no text). Group chats and channels are refused. The allowlist is a ConfigMap in Git mapping one numeric ID to one approver username; today it has one entry. |
| 4 | **Writes are sent as the approver, by impersonation.** The bot's ServiceAccount may impersonate user `koussay-approver` and group `nexus-approvers`, by `resourceNames`, and nothing else by that ClusterRole (YAML below). K5 then stamps `approvedBy: koussay-approver`. No CRD change, no K5 or K6 change, no new grant on Incidents. |
| 5 | **Reads are made as the bot itself.** The push scan (a `list` of Incidents every `pollSeconds`, 10 s; no `watch`), `/status`, `/pods` and `/incident` use the ServiceAccount's own read-only Roles. Impersonation is used for exactly three writes: approve, reject, create a human Incident. Every audit event with `impersonatedUser.username = koussay-approver` and the bot as `user.username` is then a decision made through the chat. |
| 6 | **The approver credential is never in the operator pod.** The only credential that can impersonate the approver is the `nexus-bot` ServiceAccount token, projected into the bot pod in namespace `nexus-bot`. Nothing is mounted in `nexus-system`. The operator's RBAC has no pod create, no `pods/exec` and no `serviceaccounts/token` anywhere (review section 1, VERIFIED there on 2026-11-15), so it cannot mint or read that token. Otherwise review finding F8 would become "a compromised operator approves itself" and level 2 would collapse into level 3. |
| 7 | **Button payload:** action letter + short Incident key + 32 hex of the proposal hash (format below, 51 bytes of Telegram's 64). **Before writing `spec.approval`, the bot re-reads the live Incident** and requires: phase `AwaitingApproval`, no decision yet, and the stored hash starting with those 32 hex. It then writes the **full** hash, with the `resourceVersion` it just read. |
| 8 | **Read-only commands:** `/status`, `/pods <ns>`, `/incident <name>` (D21). Fixed grammar, fixed namespaces. They write nothing. Since 2026-11-22, `/pods` alone offers the two namespaces as buttons and `/incident` alone the five most recent Incidents; those buttons follow the press rules (decision 3) and lead to the same read-only answers. The bot sets its command menu (`setMyCommands`) at start. |
| 9 | **Note flow:** reject the open Incident, then create a new human Incident with `spec.source.note` (D9; Block 8). The note is an order to the Reasoner, never to the cluster. **Not built in #252:** a Note press answers "not available"; the CRD has no `note` property yet (B4), and `nexus-client`'s `Source` model already carries `note`. |
| 10 | **`/ask`:** one fixed read-only snapshot, **one** LLM call, plain-text answer marked "advice only". The bot never parses or acts on the answer. No tool loop (CLAUDE.md rule 8). The call goes to Groq directly from the bot, with a **separate API key in its own Secret**; the Reasoner's key is never mounted in the bot. **Disabled on run days** (D22). Cut first. Since ADR-045 the Reasoner uses NVIDIA NIM only, so a Groq key for /ask shares no key and no limit with the runs; if Groq is not usable at Block 8, /ask is cut (it is cut first in any case). |
| 11 | **Plain text, formatted only by entities the bot computes over its own labels** (amended 2026-11-22; see "Amendment: rich text without a parse mode"). No `parse_mode`, so Telegram interprets no Markdown or HTML; link previews disabled. A message is plain text plus the Bot API's `entities` (bold, italic, code, pre, blockquote, expandable blockquote), offsets in UTF-16 units, computed by `bot/nexus_bot/richtext.py` over the bot's own labels. Every untrusted string (model output, evidence, logs, the Incident's proposal text, reasoning and escalation, error text) goes through `nexusctl`'s `sanitize` (`nexusctl/src/nexusctl/render/sanitize.py`), stays literal, is written only inside a blockquote and gets no entity except that blockquote; instruction-like text is marked ⚠️ (`nexusctl`'s `looks_like_instruction`). Explain shows the model's reasoning excerpt (ADR-035 addendum C) and the escalation's `suggestedChange` and `runbookGap` (ADR-044) in expandable blockquotes titled "Model text: untrusted, never a decision". A message over 4,096 UTF-16 units keeps its start, with every entity clipped inside it and no surrogate pair split, and ends with `(truncated: N of M characters; the full text is in the Flight Recorder)`. |
| 12 | **Plain alerts go through Alertmanager's `telegram_configs`**, not through bot code. Sending with the same token does not conflict with the bot's long poll. **Written, not enabled** in #252: `platform/observability/alertmanager-telegram-values.yaml` is not in the `observability` Application's `valueFiles`. |
| 13 | **The thesis reports both identities:** the Incident's `approvedBy` (`koussay-approver`) and the audit log's impersonator (`system:serviceaccount:nexus-bot:nexus-bot`). The bot also writes `reason: "telegram:<from.id>"` into `spec.approval`, so the Incident records which chat account pressed. |

## RBAC — the exact YAML

File: `platform/rbac/nexus-bot/nexus-bot.yaml`, as built in #252 (with its `kustomization.yaml`). The first two objects are
the whole of the bot's write authority. The reads are narrower than the first draft: Incidents `get` and `list`
(the push is a scan, so no `watch`), only the ConfigMap `nexus-killswitch`, and `list` on pods and Deployments.

```yaml
# The Telegram bot's RBAC (ADR-040 decisions 4 and 5; the YAML of its "RBAC" section, narrowed to
# the verbs the code calls). NOT WIRED: no Application and no parent kustomization references this
# directory yet; the Block 7 live step adds it to platform/rbac/kustomization.yaml.
#
# Write authority: the first two objects, and nothing else. The bot may act as ONE user in ONE
# group, by resourceNames. A second name here is a new approver: review under CODEOWNERS.
# `impersonate` on users and groups is cluster-scoped, so this cannot be a namespaced Role.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: nexus-bot-impersonate-approver
  labels:
    app.kubernetes.io/part-of: nexus-platform
  annotations:
    nexus.io/adr: "ADR-040"
rules:
  - apiGroups: [""]
    resources: [users]
    verbs: [impersonate]
    resourceNames: [koussay-approver]
  - apiGroups: [""]
    resources: [groups]
    verbs: [impersonate]
    resourceNames: [nexus-approvers]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: nexus-bot-impersonate-approver
  labels:
    app.kubernetes.io/part-of: nexus-platform
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: nexus-bot-impersonate-approver
subjects:
  - kind: ServiceAccount
    name: nexus-bot
    namespace: nexus-bot
---
# Reads, as the bot itself (decision 5). Incidents: list (pushes, /status, a press) and get
# (/incident, the re-read after a write); the bot polls, so no watch. The Kill Switch ConfigMap
# only (/status), by name. No write verb anywhere below.
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: nexus-bot-read
  namespace: nexus-system
  labels:
    app.kubernetes.io/part-of: nexus-platform
rules:
  - apiGroups: [nexus.io]
    resources: [incidents]
    verbs: [get, list]
  - apiGroups: [""]
    resources: [configmaps]
    resourceNames: [nexus-killswitch]
    verbs: [get]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: nexus-bot-read
  namespace: nexus-system
  labels:
    app.kubernetes.io/part-of: nexus-platform
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: nexus-bot-read
subjects:
  - kind: ServiceAccount
    name: nexus-bot
    namespace: nexus-bot
---
# /pods <ns>: list pods and Deployments. Bound in nexus-dev and nexus-prod only.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: nexus-bot-read-workloads
  labels:
    app.kubernetes.io/part-of: nexus-platform
rules:
  - apiGroups: [""]
    resources: [pods]
    verbs: [list]
  - apiGroups: [apps]
    resources: [deployments]
    verbs: [list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: nexus-bot-read-workloads
  namespace: nexus-dev
  labels:
    app.kubernetes.io/part-of: nexus-platform
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: nexus-bot-read-workloads
subjects:
  - kind: ServiceAccount
    name: nexus-bot
    namespace: nexus-bot
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: nexus-bot-read-workloads
  namespace: nexus-prod
  labels:
    app.kubernetes.io/part-of: nexus-platform
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: nexus-bot-read-workloads
subjects:
  - kind: ServiceAccount
    name: nexus-bot
    namespace: nexus-bot
---
# /status: the autonomy-level labels of the two namespaces, by name (`list` cannot be limited by
# name, so only `get`).
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: nexus-bot-read-namespaces
  labels:
    app.kubernetes.io/part-of: nexus-platform
rules:
  - apiGroups: [""]
    resources: [namespaces]
    resourceNames: [nexus-dev, nexus-prod]
    verbs: [get]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: nexus-bot-read-namespaces
  labels:
    app.kubernetes.io/part-of: nexus-platform
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: nexus-bot-read-namespaces
subjects:
  - kind: ServiceAccount
    name: nexus-bot
    namespace: nexus-bot
```

What the impersonation ClusterRole gives, precisely:

| Question | Answer |
|---|---|
| What can the bot do as the approver? | Whatever `koussay-approver` in `nexus-approvers` can: the Role `nexus-approvers` and the ClusterRole `nexus-approvers-namespaces` (`approvers-rbac.yaml`), content-limited by K5 and K6 |
| Can it impersonate another user, or another group? | No. `resourceNames` lists one of each. `system:masters`, any ServiceAccount (resource `serviceaccounts`) and extra fields (`userextras`) are not granted |
| Can it send the user without the group? | Yes, and then it holds less: the Role is bound to the group |
| Does it need `system:authenticated`? | No. The API server adds it to an impersonated non-anonymous user itself (UNVERIFIED on this cluster; settled by the first live approve in Block 7) |
| Does it need a pod-log, Secret or exec right? | No, and it has none |

**Review this file under CODEOWNERS.** It is cluster-scoped, and a second name added to `resourceNames` is a
new approver.

## Button payload and the press sequence

Telegram limits `callback_data` to 64 bytes. An Incident name is 31 characters (`inc-<fingerprint>-<epoch>`,
`operator/nexus_operator/poller.py:111-112`) and a proposal hash is `sha256:` plus 64 hex
(`proposal.py:100-103`), so neither fits whole beside the other.

```
<letter>:<key>:<h32>            51 bytes
  letter  a = Approve, r = Reject, e = Explain, n = Note
  key     the first 16 hex of the Incident's metadata.uid, dashes removed
  h32     the first 32 hex of status.proposal.hash, after "sha256:"
regex     ^[aren]:[0-9a-f]{16}:[0-9a-f]{32}$
```

The key is taken from the UID, not the name, so its length does not depend on how an Incident is named (a human
Incident has no alert fingerprint). 128 bits of hash prefix cannot be collided in practice, and K5 compares the
full hash in any case.

On a press of Approve or Reject:

| Step | The bot | If it fails |
|---|---|---|
| 1 | Checks the sender (decision 3) | Drop, log |
| 2 | Matches the payload against the regex | Drop, log |
| 3 | Lists Incidents in `nexus-system` (own identity); keeps those whose UID starts with the key | Not exactly one: "not found", nothing written |
| 4 | Runs `check_decidable` (`libs/nexus-client/src/nexus_client/approval.py:41`): phase `AwaitingApproval`, a proposal, no decision yet | "No longer awaiting approval", buttons removed |
| 5 | Requires the live `status.proposal.hash` to start with `sha256:` + h32 | "The proposal changed since this message; nothing written", buttons removed |
| 6 | Builds the approval with `build_approval` (same file, line 62) from the live Incident: decision, action, parameters, **full** hash, `expiresAt = now + 15 min − 15 s`, `reason: telegram:<from.id>` | — |
| 7 | Patches `spec.approval` **as `koussay-approver` / `nexus-approvers`**, with the `resourceVersion` read in step 3 | A K5, C2 or conflict refusal is shown as text, with the Audit-Id |
| 8 | Re-reads; edits the message to "Approved by `<approvedBy>` at `<approvedAt>`", removes the buttons | — |

The bot keeps no table of what it showed: the payload is the whole memory, so a restart loses nothing. A stale
button fails at step 4 or 5; a second press fails at step 4 and, behind it, at C2.

**The fields on screen above the buttons** — target, action, replicas, hash, level, expiry — are read from the
Incident fields that K5 binds, and are printed outside the "untrusted" box. The Reasoner's hypothesis is inside
the box. Explain and Note never change what Approve sends.

## Commands, Note flow, `/ask`

**Read-only commands (D21).** Sender check as for buttons.

| Command | Shows | Reads |
|---|---|---|
| `/status` | Open Incidents (name, target, phase, age), the level of `nexus-dev` and `nexus-prod`, the Kill Switch | Incidents; two namespaces; ConfigMap `nexus-killswitch` |
| `/pods <ns>` | Per Deployment: ready / desired; per pod: phase, restarts. `<ns>` must be `nexus-dev` or `nexus-prod` | Pods, Deployments |
| `/incident <name>` | The same text as the Explain button. `<name>` must match `^inc-[a-z0-9-]{1,59}$` | One Incident |

No pod logs, no events, no Secrets: the bot has no right to them.

**Note flow (D9; Block 8).**

1. The owner presses Note. The bot asks for the text with a forced reply.
2. The reply is accepted only from the allowlisted sender, as a reply to that question, within 5 minutes, at
   most 500 characters.
3. If the Incident is still `AwaitingApproval` with the hash shown, the bot **rejects** it, as the approver,
   with `reason: superseded by note, telegram:<from.id>`. If it is already terminal, nothing is done to it.
4. The bot **creates a new Incident**, as the approver: `source.type: human`, the same `spec.target`,
   `spec.source.note` = the text. K6 admits it and stamps `source.requester` (`k6-incident-source.yaml:20-30`).
5. The operator diagnoses it; the note reaches the Reasoner inside the evidence, labelled human-supplied
   (spec:1421). The new proposal comes back with buttons and passes the Safety Gate and Kyverno like any other.

"Restart it instead" can yield `restart_workload`. "Delete the namespace" cannot be expressed in the four-entry
catalogue: the result is an escalation or `Blocked / not_in_catalogue`. If step 4 fails after step 3, the first
Incident stays `Rejected` and the bot says so; the owner sends the note again or uses `nexusctl`.

**`/ask <question>` (D22; Block 8, after the Note flow; cut first).**

| Aspect | Rule |
|---|---|
| Snapshot | Fixed: the output of `/status` and of `/pods` for both namespaces, and the Explain text of open Incidents. Collected once, before the call. The model cannot ask for more |
| Call | One request, no tools, no retry loop. The question is at most 300 characters |
| Answer | Sanitised, plain text, first line `ADVICE ONLY — NEXUS will not act on this`. No buttons under it. Never parsed |
| Path | The bot calls Groq directly. The Reasoner's ingress, prompt and version are untouched, because they are part of the frozen measured system |
| Key | A separate Groq API key, in its own Secret `nexus-bot/nexus-bot-ask-groq`, mounted as a file in the bot only. The Reasoner's key stays in `nexus-reasoner`. Runs and `/ask` never share a key or its limits. Whether Groq counts limits per key or per account is UNVERIFIED (U8) |
| When | **Disabled on run days.** `ask.enabled` in the bot's ConfigMap, in Git, default `false`; the run protocol (Block 9) checks that it is `false` before the first run of a day. When it is off, `/ask` answers one fixed line and makes no call |

## Threat model

Assets: the right to write `spec.approval` as the approver; the right to create human Incidents; the text the
owner reads before deciding.

| Threat | What the attacker gets | What still holds | Residual |
|---|---|---|---|
| Stranger messages the bot, or adds it to a group | Nothing: dropped by the allowlist and the private-chat rule | — | None |
| Forged callback from outside Telegram | Not possible with long polling: there is no endpoint to post to | — | None |
| **The owner's Telegram account is taken over** (SIM swap, stolen session) | Approve, reject, send notes, as the owner | Only a proposal the operator made; only the four catalogue actions; K1–K4, the bounds [1, 5], the 15-minute expiry | **Accepted limit 1** |
| **The bot's ServiceAccount token is stolen** (pod compromise, a party that can create pods or tokens in `nexus-bot`) | The same writes as the approver, without Telegram | The same bounds. The token is a projected, expiring token | **Accepted limit 2** |
| The Telegram bot token is stolen | Reads the updates and competes for them (approvals stall); sends messages as the bot, including a misleading text above a button whose payload is a genuine pending proposal | It cannot forge `from.id`, so it cannot press. A press still approves only the proposal named in the payload, and the bot's confirmation is built from the live Incident | **Accepted limit 3**; rotate through BotFather |
| Compromised operator approves itself | Nothing: it has no route to the credential (decision 6); K5 `approvers-only` refuses its identity | — | None found |
| Compromised operator shows a false diagnosis beside a real action | The owner may approve an in-catalogue action for a wrong reason | Target, action and replicas on screen are the K5-bound fields | Review limitation 4 (F4, F8); unchanged by the bot |
| Reasoner text shaped like a button, a link or a trusted field | Displayed as literal text inside a blockquote titled "Model text: untrusted, never a decision" | No parse mode; sanitised; no entity of its own; previews off | Social engineering only |
| A note that tries to command the cluster | It is evidence for an untrusted Reasoner | Safety Gate, catalogue, Kyverno | By design |
| A stale or replayed press (Telegram redelivers after a bot restart) | Nothing: phase and hash are re-checked; C2 is write-once | — | None |
| A poisoned `/ask` answer | Text on the owner's screen | It triggers nothing | Social engineering only |
| The bot is down | No chat approvals | `nexusctl` still works | Availability only |

## Stated limits

1. **Whoever controls the owner's Telegram account can approve as the owner.** The control is outside NEXUS:
   the account's two-step verification. The harm stays inside the bounds.
2. **Whoever holds the `nexus-bot` ServiceAccount token can approve as the owner.** That includes the cluster
   admin, ArgoCD and anyone who can change Git: all three are already trusted (review limitation 8).
3. **A second approver through the bot is indistinguishable in the cluster.** Every chat approval is
   `koussay-approver`. Only the `reason` field and the bot's log name the Telegram account. Per-approver signing
   keys would fix this and do not fit before the defense.
4. **An approval is still a licence for up to 15 minutes, not a single use** (review F5, F6). The bot neither
   widens nor narrows this.
5. **Telegram is not confidential storage.** Bot chats are not end-to-end encrypted; target names, pod names,
   metrics and the Reasoner's text leave the cluster. The messages are built from the redacted bundle, and no
   Secret can be read by the bot.
6. **The bot pod is the most sensitive pod after the operator.** It holds the impersonation right and two
   outside tokens, and it handles untrusted text. It has no ingress, runs non-root with a read-only root
   filesystem, and its egress is DNS, the API server and public TCP 443 — by address range, not by name (the
   spec §18 known limitation).
7. **`/ask` sends the snapshot to the model provider.** It is advice; it has no path to an action.

## Contradictions with the spec and the sources

Reported, not adapted silently. The rows that change the spec are in the spec v1.1 list as X14
(`docs/plans/pfe-replan.md`). Rows are numbered B1–B5 here, to keep them apart from that list's X numbers.

| # | Source | Says | This ADR | Why |
|---|---|---|---|---|
| B1 | Spec §3; `platform/argocd/projects/*.yaml` destinations | Nine namespaces | A tenth, `nexus-bot` | Decision 6: the credential must not be in the operator's namespace |
| B2 | Plan 3.1 | The bot's ClusterRole is impersonate "and nothing else"; reads are made as the approver | Reads under the bot's own read-only Roles | `/pods` needs pod reads the approvers do not have; and the audit trail then separates decisions from polling |
| B3 | Review section 5 | The bot "patches with that hash and never re-reads at click" | It re-reads and requires the 32-hex prefix | A 64-hex hash does not fit in 64 bytes with an Incident key. The prefix check gives the same guarantee: the write succeeds only for the proposal shown |
| B4 | Plan 3.1 | "No CRD change" | True for approval. The **Note flow needs one**: `spec.source` has no `note` property (`incidents.nexus.io.yaml:65-72`), so the API server would prune it | Spec:1421 defines `spec.source.note`; the CRD never got it. Block 8 adds `note: {type: string, maxLength: 500}` **before** any Note-flow code; C1 already makes it immutable. Cut-first with the Note flow: if Block 8 is cut, the CRD is not changed |
| B5 | Review section 5 | A "Role" limited to impersonate | A ClusterRole | `users` and `groups` are cluster-scoped virtual resources |

## Not verified

| # | Item | Settled by |
|---|---|---|
| U1 | No binding names `koussay-approver` beyond the group's (the repository has none: `grep -rn koussay-approver` finds only `nexusctl` and `libs` tests and docs) | `kubectl auth can-i --list --as koussay-approver --as-group nexus-approvers -n nexus-system` (read-only), and the same with `-n kube-system` |
| U2 | The API server accepts the impersonation with only the two `resourceNames` rules | VERIFIED 2026-11-21: `kubectl auth can-i --as system:serviceaccount:nexus-bot:nexus-bot` → `impersonate users/koussay-approver` yes, `impersonate groups/nexus-approvers` yes, `impersonate users/admin` no, `impersonate groups/system:masters` no; `patch incidents.nexus.io -n nexus-system` no |
| U3 | The operator reconciles an Incident it did not create from an alert (plan 3.2) | Block 8, first task: an envtest case that creates a `human` Incident |
| U4 | `evidence.py:161` sets `"note": None`; whether the prompt renders `incident.note` | Read `evidence.py` and `reasoner/nexus_reasoner/prompt.py` in Block 8. A prompt change must land before the Block 9 freeze |
| U5 | The audit policy (ADR-036) records Incident patches with `impersonatedUser` | After the first live approve: `scripts/audit-join.py` output for that Incident |
| U6 | Alertmanager can reach `api.telegram.org` (no NetworkPolicy file exists for `monitoring` under `platform/network/`) | Block 7: one test alert |
| U8 | Groq applies rate limits per API key, not per account or organisation. If they are per account, a separate key does not separate the limits, and only "disabled on run days" protects the runs | Groq's rate-limit documentation and the response headers of one call per key, in Block 8 |
| U7 | Telegram's limits as used here: 64-byte `callback_data`, 4096-character messages, one `getUpdates` consumer per token | The Bot API reference, read at build time; the unit tests pin the first two |

## Build checklist — Block 7 (bot core), 18 hours

| Step | Files | Tests (offline unless marked) | Hours |
|---|---|---|---|
| 1. Package and image | `bot/pyproject.toml`, `bot/Dockerfile`, `bot/nexus_bot/{main,config}.py`; depends on `libs/nexus-client` and `nexusctl` (renderer). `.github/workflows/bot.yml` (lint, tests, build, Cosign, digest-bump PR — the Reasoner's pattern) | CI green; image runs non-root | 2 |
| 2. Telegram client | `bot/nexus_bot/telegram.py`: `getUpdates`, `sendMessage`, `answerCallbackQuery`, `editMessageReplyMarkup`, `deleteWebhook`; no `parse_mode` | Fake HTTP server: offset handling; no request ever carries `parse_mode`; the token never appears in a log line or an exception text | 3 |
| 3. Sender check and payload | `bot/nexus_bot/{auth,payload}.py` | Allowlist: other ID, group chat, channel, missing `from` → dropped. Payload: round trip, 51 bytes, regex rejects every malformed case | 2 |
| 4. Scan and push | `bot/nexus_bot/push.py` (built as a scan, not a watch): push on `AwaitingApproval`, `Escalated`, `Resolved`, `Blocked`; one message per Incident and phase; after a restart every pending `AwaitingApproval` again, older news only within `catchUpMinutes` (15) | Fake Incident list; restart does not resend | 3 |
| 5. Approve, Reject, Explain | `bot/nexus_bot/decide.py`, using `check_decidable` and `build_approval` | Stale hash → no write. Wrong phase → no write. Already decided → no write. Two matches for a key → no write. The patch carries the impersonation headers, the full hash, the `resourceVersion`, an expiry inside 15 min. Reject carries no action. A K5 denial is shown as text | 3 |
| 6. `/status`, `/pods`, `/incident` | `bot/nexus_bot/commands.py` | Namespace outside the two → refused. Name regex. No call uses the impersonation headers | 2 |
| 7. Manifests | `bot/k8s/{namespace,serviceaccount,deployment,configmap,kustomization}.yaml`; `platform/rbac/nexus-bot/nexus-bot.yaml` (above); `platform/network/nexus-bot/nexus-bot.yaml` (default-deny; egress DNS, API server 443 and 6443 as in `system.yaml:36-66`, public 443 as in `reasoner.yaml`); `platform/argocd/applications/nexus-bot.yaml`; the AppProject destination `nexus-bot`, in the same PR | `kustomize build`; `scripts/tests/policies-offline.py` extended: the ClusterRole has exactly two rules, one `resourceNames` entry each, verb `impersonate` only | 2 |
| 8. Secret script and Alertmanager | `scripts/nexus-bot-secrets.sh` (the `flight-recorder-secrets.sh` pattern: create if absent, value never in argv or output); `telegram_configs` in the inline Helm values of `platform/argocd/applications/observability.yaml` with `bot_token_file` | Script dry test with a stub `kubectl`; `helm template` renders the receiver | 1 |

**Live gate (owner approval needed):** U2's two `can-i` checks; one level-2 approval from the phone; the
Incident shows `approvedBy: koussay-approver` and `reason: telegram:<id>`; the audit log shows the bot as
impersonator; a press from a second Telegram account is ignored; a press on a stale message writes nothing.

**Owner-only:** BotFather registration; the bot token; the Telegram user ID; two-step verification on the
Telegram account; running the Secret script.

**Block 8 additions (11–14 hours for the Note flow, then `/ask`):** the CRD `note` property (B4);
`nexusctl request`; the operator path for human Incidents (U3); `bot/nexus_bot/note.py` and `ask.py`; the Secret `nexus-bot-ask-groq` added to `scripts/nexus-bot-secrets.sh`
(owner-only: the second Groq key); tests: `ask.enabled: false` makes no HTTP call,
reply from another sender ignored, reply after 5 minutes ignored, reject-then-create order, create failure
reported, `/ask` makes exactly one HTTP call and its answer never reaches `decide.py`.

## As built in #252 (2026-11-21)

| Point | First draft | Built |
| --- | --- | --- |
| Incident reads | `watch` | A `list` every `pollSeconds` (10 s): `get`, `list` only (decision 5) |
| ConfigMap reads | `nexus-killswitch`, `nexus-operator-config` | `nexus-killswitch` only |
| Pods and Deployments | `get`, `list` | `list` only |
| File paths | `platform/rbac/nexus-bot.yaml`, `platform/network/bot.yaml` | `platform/rbac/nexus-bot/`, `platform/network/nexus-bot/`, each with a `kustomization.yaml` |
| Explain | Cut at 4,096 characters | The reasoning excerpt and the escalation suggestion in their UNTRUSTED boxes; a truncation line (decision 11). Since 2026-11-22: expandable blockquotes (decision 11, amended) |
| Note, `/ask` | Block 8 | Not built; a Note press answers "not available" |
| `spec.source.note` | A CRD property before the Note flow (B4) | Unchanged: the CRD has none; `nexus-client`'s `Source.note` exists |
| Alertmanager route | `telegram_configs` | Written, not enabled (decision 12) |
| Argo CD Application, AppProject destination `nexus-bot`, `scripts/nexus-bot-secrets.sh` | Step 7 and 8 | Not in #252: the Block 7 live step (`bot/README.md`). Until then no Application deploys `bot/k8s/` |
| C8 | ADR-041 | Built separately (#255) as a library the bot's loop calls (`Service.tick`); `git` in the bot image and the GitHub Secret come with Block 6; wired in Block 6 as the bot's third loop (ADR-041 "As wired into the bot") |
| `getUpdates` timeout (decision 2) | 50 s | 25 s, the client's timeout 10 s above it (Block 6): at 50 s the live bot logged a `getUpdates` TimeoutError about once a minute, most likely an idle connection dropped before the server answered |

## Live gate, 2026-11-21 (owner)

Evidence: `~/nexus-evidence/bot-live-2026-11-21/` (`RECORD.md`, `incident.json`, `audit-event-1d1c583b.jsonl`,
`bot.log`), collected read-only; `RECORD.md` holds the source commands and the sha256 of each file.

| Point | Result |
| --- | --- |
| Deployment | The bot `@nexus_pfe_bot`, deployed by promotion 20 (#300, `main` 8a00e97); image `nexus-bot@sha256:37f9a54ec2dfe1fe1381c674af284065a21cb4d6a8560d52d2d7ca966d04dd07`, signed with Cosign keyless |
| Sync lesson | The platform sync that adds the AppProject destination `nexus-bot` and the resources in that namespace cannot pass in one sync: Argo CD validates against the live AppProject. It was unblocked by syncing `AppProject/nexus` alone, then `platform`. A from-empty rebuild is not affected: `bootstrap.sh` applies the AppProject first |
| Level-2 approval | Incident `inc-669532275a7b73d2-1795292289`: `spec.approval` `decision: approved`, `approvedBy: koussay-approver`, `reason: telegram:8998653791`, the pinned `proposalHash`, a 15-minute expiry (`expiresAt` 20:34:59Z) |
| Audit | Event `1d1c583b-f598-4f28-a7ff-4ea450bb5796`: the bot's ServiceAccount impersonating `koussay-approver` and `nexus-approvers`, `patch`, 200, stamped by Kyverno K5 (the mutating webhook reports `mutated: true`) |
| Bot log | `press a from <id>: wrote=True`; the buttons were removed after the write, so an old message cannot be pressed again |
| Outcome | `Escalated` / `verification_failed`, caused by the runner's reset (a rollout restart at 20:23Z) inside the verification window 20:20:34Z–20:25:34Z; not a fault of the bot or the action |
| Second-account test | Skipped by the owner; the unit tests cover it (any sender not on the allowlist is dropped) |

Follow-ups:

- `getUpdates` raises `TimeoutError` about once a minute, likely idle connections dropped; try a 25 s long poll.
- Pushes show `hypothesis: -`: the operator does not copy the hypothesis summary into the Incident status.

## Amendment: rich text without a parse mode (owner, 2026-11-22)

The messages were redesigned for a phone. Decision 11's guarantee is kept: Telegram is never asked to
parse anything, so no text the bot did not write can format itself, link itself or look like a trusted field.

| Point | Before | Now |
| --- | --- | --- |
| Formatting | None: plain text | Plain text plus `entities` the bot computes (`richtext.RichText`): bold labels and titles, italic notes, blockquotes. `parse_mode` is never sent; `TelegramClient._call` refuses a payload that has it, and a test walks every payload the bot builds |
| Untrusted text | Inside `+-- UNTRUSTED … --+` boxes | `sanitize`d, literal, written only inside a blockquote (`RichText.untrusted` refuses otherwise), with no entity of its own; `build` refuses a formatting entity that overlaps it. Model text sits under the title "Model text: untrusted, never a decision"; error and C8 text in a plain blockquote |
| K5-bound fields | Above the box, plain | Unchanged: target, action, parameters, hash and level are facts above the model text and the buttons, sanitized, with no entity |
| `code` entity | — | Only over a value that fully matches an Incident name (`inc-[a-z0-9-]{1,59}`) or a `sha256:` hash (64 hex), in Explain (tap to copy). Any other value stays plain |
| Titles | `NEXUS <phase> (<reason>)` | A status icon and a fixed string from the CRD phase enum: 🟠 Approval needed, ⏳ in progress, 🟢 Resolved, 🔴 Escalated, ⚪ Dismissed (and Rejected, Expired, Recorded, Recommended), ⛔ Blocked. The CRD has no `Failed` phase |
| Layout | Aligned columns | One fact per line, a short bold label; hashes and names shortened in pushes, lists and replies (`sha256:14f167…839f5e`, `inc-6695…2289`), in full in Explain; times `HH:MM UTC` |
| Explain | `nexusctl`'s `explain_text` | The bot's own layout, sections Detection, Evidence, Runbooks, Gate, Approval, Enforcement, Verification, Model; then the proposal and the reasoning excerpt in expandable blockquotes. `sanitize` and the instruction heuristic are still `nexusctl`'s, so there is still one sanitizer |
| C8 | `C8 revert: <line>` | A title picked from C8's own fixed wording at the start of the line (e.g. 🟠 C8: revert PR opened), the Incident, and the whole line in a blockquote. The log line is unchanged |
| Truncation | `telegram.fit` | The same line; entities clipped to the kept start, none past the end; the cut is an exact search over UTF-16 prefix lengths (the earlier loop over-cut text with many characters outside the BMP) |
| URLs | Sent as written | Telegram auto-links URLs even when `entities` is given, so in untrusted text (the proposal, hypothesis, reasoning, escalation fields, error and log text) `richtext.defang` writes `http`/`https` (any case) as `hxxp`/`hxxps`, the host's dots as `[.]` and a bare `www.` as `www[.]` (owner, 2026-11-22); C8's pull request and compare links come from GitHub's API and stay tappable |
| Buttons | Approve, Reject, Explain | Also `p:<nexus-dev\|nexus-prod>` and `i:<uid16>` (at most 18 bytes), read-only, under the same sender check as a press |

UNVERIFIED (no live Telegram in this change): how the official clients draw `expandable_blockquote` and whether
the server still auto-links URLs, `@mentions` and `/commands` in plain text when `entities` is given (it did with
plain text before; previews stay off either way). Settled by a live push in the next bot deployment.

## Consequences

- A level-2 approval can be given from a phone, and the Incident and the audit log both say who and through
  what.
- The set of trusted parties grows by two: the owner's Telegram account and the bot pod. Both are bounded by
  the same admission rules as any approver; neither can leave the four-action envelope.
- The cluster gains one cluster-scoped grant (`impersonate`, two names) and one namespace.
- `nexusctl` stays the reference client and the fallback. The approval rules live in `libs/nexus-client` and
  are used by both.
- Spec v1.1 (ADR-048, merged in #203) names this ADR as pending; these addenda enter spec version 1.2 or an
  amendment of ADR-048 (its decision 4): §3 and the AppProject (namespace), §10 and §24 (component, threat
  rows), §11 (the ClusterRole), §16A (the second interface), §18 (the bot's egress); ADR-033 gets a pointer to
  this ADR.
