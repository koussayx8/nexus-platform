# nexus-bot

The Telegram approval bot (ADR-040; `TASKS.md` D9, D21; Block 7). A **client**, like `nexusctl`: it
authorises nothing. It reads Incidents as its own ServiceAccount and makes one kind of write —
`spec.approval` — by impersonating the approver. RBAC, Kyverno K5 and the CRD rules (C2) decide that
write exactly as they decide a `nexusctl approve`.

| What | Status in this change |
| --- | --- |
| Long polling (`getUpdates`, 50 s), `deleteWebhook` at start, no inbound socket | built |
| Sender check: allowlisted numeric user ID, private chat only, `chat.id == from.id` | built |
| Pushes for `AwaitingApproval`, `Escalated`, `Resolved`, `Blocked` (one per Incident and phase; after a restart, every pending `AwaitingApproval` again, older news only within `catchUpMinutes`; the rest is in `/status`) | built |
| Buttons Approve, Reject, Explain (payload `<a\|r\|e>:<uid16>:<hash32>`, 51 bytes) | built |
| `/status`, `/pods <nexus-dev\|nexus-prod>`, `/incident <name>` | built, read-only |
| Note, `/ask` | Block 8 (a Note press answers "not available") |
| Image, CI image build and signing, manifests, RBAC, NetworkPolicy, Argo CD app, Alertmanager route | not in this change (ADR-040 build checklist steps 1, 7, 8) |

## How a press is decided

1. Sender check; payload regex. Anything else is dropped (logged with IDs only, never text).
2. List Incidents **as the bot**; exactly one UID must start with the payload key.
3. `check_decidable` (`libs/nexus-client`): phase `AwaitingApproval`, a proposal, no decision yet.
4. The live `status.proposal.hash` must start with the payload's 32 hex.
5. `build_approval` from the live Incident: action, parameters, the **full** hash,
   `expiresAt = now + 15 min − 15 s`, `reason: telegram:<your user ID>`. Never `approvedBy` or `approvedAt`.
6. Merge-patch with the `resourceVersion` just read, **as** the allowlisted approver user and group
   `nexus-approvers` (`Impersonate-User`, `Impersonate-Group`). K5 stamps `approvedBy` from that identity.
7. Re-read and report `approved by <approvedBy> at <approvedAt>`, the pinned hash and the Audit-Id; the
   buttons are removed. A stale, repeated or refused press writes nothing and says why.

## Configuration

`/etc/nexus-bot/config.yaml` (a ConfigMap in Git; `NEXUS_BOT_CONFIG` overrides the path):

```yaml
allowlist:            # Telegram numeric user ID -> the approver username impersonated for it
  "123456789": koussay-approver
approverGroup: nexus-approvers
namespaces: [nexus-dev, nexus-prod]   # what /pods and /status may read; no other value is accepted
pollSeconds: 10                        # Incident scan interval for pushes
catchUpMinutes: 15                     # on start, Escalated/Resolved/Blocked entered longer ago are not pushed
```

The token is read from `/var/run/secrets/nexus-bot/telegram/token` (`NEXUS_BOT_TOKEN_FILE`), a Secret
mounted as a file. It is never in Git, an environment variable, argv or a log line.

## Owner steps (only you can do these)

1. **Two-step verification** on your Telegram account (Settings → Privacy and Security). Whoever controls
   the account can approve as you (ADR-040 stated limit 1).
2. **Register the bot** with @BotFather: `/newbot`, a display name, a username ending in `bot`. BotFather
   replies with the token: copy it, do not paste it into a chat, a file in the repository or a terminal
   command line. Then `/setjoingroups` → **Disable** (the bot refuses groups anyway; this stops the invite).
3. **Store the token as a Secret, outside Git**, once the `nexus-bot` namespace exists (a cluster write:
   it needs your approval for the Block 7 live step). `read -rs` takes the token at a silent prompt, so it
   never lands in shell history or in any process's argv (only `printf`, a shell builtin, holds it, and
   kubectl reads it from stdin):

   ```bash
   read -rs -p 'Telegram bot token: ' T && echo && printf '%s' "$T" | kubectl --context default -n nexus-bot create secret generic nexus-bot-telegram --from-file=token=/dev/stdin; unset T
   ```

   Then check it by printing **only its length** (a bot token is about 46 characters), never its value:

   ```bash
   kubectl --context default -n nexus-bot get secret nexus-bot-telegram -o jsonpath='{.data.token}' | base64 -d | wc -c
   ```

   `--from-file=…=/dev/stdin` works with kubectl on Linux; test it once with a dummy value first (then
   `kubectl delete secret` it and create the real one). ADR-040 step 8 later replaces this with
   `scripts/nexus-bot-secrets.sh` (create if absent, value never in argv or output). Rotate the token in
   BotFather (`/revoke`) if it ever leaks; the bot calls `deleteWebhook` at every start.
4. **Find your numeric user ID** without a third-party bot: start the bot with any allowlist, send it
   `/status` from your phone, and read the log line `dropped {"update_id": …, "from_id": <your ID>, …}`.
   Put that number in `allowlist` (in Git, reviewed), mapped to `koussay-approver`. One entry today.
5. Send `/start` to the bot once: Telegram lets a bot message only users who have opened its chat.

## Development (offline)

```bash
pip install -r nexusctl/requirements-dev.txt -e libs/nexus-client -e nexusctl -e bot
export KUBECONFIG=/nonexistent && pytest -q bot
```

Telegram is behind `TelegramClient(Transport)` (tests use a recording fake, and a fake Bot API on
127.0.0.1); Kubernetes is behind `Reader` and `ApproverFactory` (tests use an in-memory cluster whose
approver stamps `approvedBy` from the impersonated user and enforces write-once, as K5 and C2 do).
