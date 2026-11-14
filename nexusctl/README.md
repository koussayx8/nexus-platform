# nexusctl

The human interface to NEXUS (spec §16A). Untrusted client software that **adds no authority**:
it runs with your kubeconfig, has no ServiceAccount, and never talks to the operator or the
Reasoner. Every write is authorised by RBAC, Kyverno K5/K6 and the CRD rules as *you*.

| Command | Status in this change |
| --- | --- |
| `list [--active] [--phase] [--ns] [--since] [--limit]` | built |
| `explain <incident>` | built, from the Incident CR; Flight Recorder and API Audit Log sections are named as unavailable until their readers land (`--evidence`, `--raw` exit 9) |
| `approve <incident> [--ttl 15m]` | built; no non-interactive mode |
| `reject <incident> --reason TEXT` | built |
| `watch`, `status`, `request`, `audit`, `killswitch`, `level`, `version`, `config`, `completion` | SHOULD, M6 (spec §16A "Delivery") |

Exit codes: 0 success, 1 unexpected, 2 denied, 3 not found, 4 stale proposal, 5 wrong phase or
already decided, 9 data source unreachable (6, 7, 8 belong to commands not built yet).

```bash
pip install -r nexusctl/requirements-dev.txt -e libs/nexus-client -e nexusctl
pytest libs/nexus-client nexusctl
nexusctl list --active
nexusctl approve inc-s2-run0042
```

Approval flow: re-reads the Incident, shows action, target, parameters, proposal hash, gate
verdicts and expiry (the hypothesis in an untrusted box), asks you to type the target name, re-reads
once more (a changed hash aborts with exit 4), then patches `spec.approval` with the pinned values
and the Incident's `resourceVersion`. Kyverno K5 stamps `approvedBy`/`approvedAt`; the Audit-Id of
the write is printed. The expiry is `now + ttl - 15 s`, inside K5's 15-minute bound, to absorb
clock skew with the API server.

Identity: nexusctl acts as your kubeconfig user. To act as the approver without a new credential,
impersonate it, as with kubectl: `nexusctl --as koussay-approver --as-group nexus-approvers approve
<incident>` (the admin user may impersonate; RBAC and K5 then judge `koussay-approver`, and K5 stamps
it as `approvedBy`). The official Python client ignores a kubeconfig user's `as:` / `as-groups:`
fields, so a kubeconfig context that impersonates works for kubectl but not for nexusctl: use the
flags. `--as-group` takes one group and needs `--as`.
