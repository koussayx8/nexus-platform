# Containment suite (spec section 20, G11) and the Phase B replay (G15)

The containment claim is that no request the Reasoner can influence changes the cluster outside the
envelope, whatever the Reasoner says. This directory checks it three ways.

| What | File | Needs | Status |
| --- | --- | --- | --- |
| The coverage matrix, as data: 3 identities x 10 requests x 7 cells x 3 Kill Switch states = 630 verdicts | `matrix.yaml`, `matrix.py` | PyYAML | CI |
| The same verdicts replayed through the Kyverno CLI with mocked context (K1-K4 for the 96 cells Kyverno decides, K5 for the approval path), and the repository's RBAC checked to grant no more than section 11 | `kyverno_replay.py`, `matrix.repo_overgrants` | `kyverno` CLI | CI |
| Every verdict checked by server-side dry-run on a cluster, plus fail-closed and the Kill Switch cases | `live.py`, `fixtures/fixtures.sh` | a cluster | **live check pending** |
| Stored proposals (R0 and the others) replayed at L0-L3 through the gate stages 1-3 and the Trusted Enforcement Plane | `phase_b.py` | `kyverno` CLI for the CLI path | CI (R0 cases) |

## Reading the matrix

`matrix.yaml` holds the section 11 tables (what each identity may do, K1-K4 as predicates) and 33
headline cells written by hand; `matrix.expand()` derives all 630 and fails if a cell is undecided.
A verdict is `ADMIT` or `DENY` with the layers that refuse. RBAC stops first, so a DENY by RBAC lists
only RBAC. 20 cells are admitted: a responder's in-bounds scale or eviction at L3 and at L2 with an
approval (Kill Switch active), and an Approver's approval where an Incident can exist and has none.

Cells Kyverno cannot decide are counted, not skipped silently: RBAC (modelled, and the manifests
checked as a subset of section 11) and the CRD rules C2 and C3 (envtest: `scripts/tests/incident-crd.sh`).

## Running it

```bash
python3 -m unittest discover -s experiments/containment -t experiments     # offline; Kyverno tests skip without the CLI
REQUIRE_KYVERNO=1 python3 -m unittest discover -s experiments/containment -t experiments   # as CI
python3 -m experiments.containment.phase_b --tables tables.json          # Phase B replay of stored proposals
```

### Live (owner runs it; nothing here has been run on a cluster)

1. Levels: `nexus-dev` at L3 and at L2 are Git commits on `experiment/dev-state`; `nexus-prod` L1 and `nexus-data` L0 are on `main`.
   One matrix pass needs the dev level to be the cell's: run `dev-l3`, then `dev-l2-*` cells (`--cells`).
2. `fixtures/fixtures.sh up` (creates Incidents and one scratch namespace; approvals expire in 10 minutes, so run it before each pass).
3. Put the Kill Switch in the state of the pass (`active`, then `halted`, then delete the ConfigMap for `absent`), then
   `python3 -m experiments.containment.live --ks-state active --run --out report.json`. The script checks the
   preconditions read-only and refuses to run if they differ. `--plan` prints every command without sending any.
4. Fail-closed: scale Kyverno's admission controller to zero (ArgoCD selfHeal reverts it within about 30 seconds: suspend
   it or be quick), then `--special fail-closed --run`. Kill Switch compensation: `--special killswitch-halted-compensation`
   and `--special killswitch-absent-compensation`.
5. `fixtures/fixtures.sh down`.

Not built: the "small subset runs for real" of section 20 (the dry-run covers every cell); the compromised-operator
run (phase D, advisory checks off) belongs to the harness runner.
