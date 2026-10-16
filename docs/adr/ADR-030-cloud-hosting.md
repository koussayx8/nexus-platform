# ADR-030: Cloud Hosting for M2, the Defense Demo and an x86 Replication

## Status: Proposed (owner gate decisions (a)–(d) and the billing guard, 2026-10-16; revised at the #130 review, 2026-10-16: the owner's account facts, v0.3.0 for (b), step 0, host topology by addendum; docs only: this ADR creates no cloud resource and changes no cluster)

## Context
- M1b closed at `v0.3.0` (`86cea844`) on a single-node k3s inside WSL2. The node is `koussay`: amd64, 12 CPUs,
  15.5 GiB, k3s `v1.34.6+k3s1`, flannel with the k3s network-policy controller. On 2026-10-16 at about 06:53Z, with the
  load state not checked, it ran at 2.16 cores and 3.8 GiB (`kubectl top node`). Pod requests summed to 1.86 CPU and
  2.70 GiB; memory limits summed to 6.04 GiB.
- Every M1b parameter was measured on that host:
  - C ≥ 200 and B = 80 req/s per namespace (ADR-026, ADR-027);
  - sample-api limits 1000m / 128Mi (ADR-027);
  - dependency DB limits 2000m / 512Mi (ADR-028);
  - "quiet when healthy" is still open: checkpoint B failed twice (ADR-026, ADR-028).
- **The owner's accounts (owner, 2026-10-15):**
  - OCI Free Trial: **€250**, usable from 2026-10-11 to 2026-11-09;
  - AWS credits: **US$120**, until 2026-11-03.
- M2's starting list (handoff, 2026-10-14) includes this ADR.

## Decision

### (a) M2 and the defense demo run on an OCI Always Free Ampere A1 node, as a new calibration
- **Host:** one `VM.Standard.A1.Flex` instance, within the Always Free limit (2 OCPUs and 12 GB in total, below),
  single-node k3s, same Git stack. Nothing measured on WSL2 carries over.
- **Step 0, a feasibility screen on WSL2, before any arm64 work** (the multi-arch images in Consequences):
  - with Locust stopped, record the node's CPU and memory use and the summed pod requests and limits (CPU and
    memory);
  - if that leaves no room on 2 OCPUs / 12 GB, a decision gate chooses a reduced stack before anything else;
  - this is a screen only: none of its numbers carry over to the A1 calibration.
- **Host topology:** where the Experiment Runner, Fault Injector, Evaluation Harness and K8sGPT CLI run is
  decided by an ADR-030 addendum before (a)'s first calibration.
- **Calibration order, before any detector run on that host:**
  1. Fix the CNI (section d).
  2. **Measure the idle footprint:** the full stack Synced and Healthy, Locust idle; node and per-namespace CPU and
     memory over a window fixed in the pre-registration.
  3. **Re-derive C and B:**
     - C by the R1 ramp (ADR-026);
     - B as 40 % of two-replica capacity (spec §25, ADR-026).
  4. **Re-derive the sample-api and DB limits** by the ADR-027 and ADR-028 principle. That principle: the limit is
     the most CPU normal traffic uses at once, from measured per-request cost and the DB slots.
  5. **Pre-register** the host facts and the measured and derived values, with the pass, void and discard rules,
     in a decision-gate PR. Only then may a detector run start.
- **If a derived value does not fit the node, that is a decision gate.** One example: ADR-028's 2000m DB limit,
  which equals the whole node. The agent reports it and does not adapt the stack or the load to fit.
- **The load is not shaped to the detector.** The fault semantics and the 4 : 1 mix stay as ADR-024 and ADR-026
  define them.
- **Demo fallback:** a recording of a valid pre-registered run on this host (screen capture plus its evidence
  bundle), made before the defense. If the live demo fails, the recording is shown. It is labelled as a recording.

### (b) Replicate the M1b protocol on an OCI x86 trial shape by 2026-11-09
- **Host:** one x86 (AMD or Intel) Flex VM paid from the OCI Free Trial credit. It runs the same stack: the same
  k3s `v1.34.6+k3s1` and flannel. It deploys **`v0.3.0` (`86cea844dff5dac8cbd3f61f57a3ffc94aac2838`)**,
  the revision M1b closed on. Any other revision is a decision, pre-registered. The shape and its size (OCPUs,
  memory) are fixed in the pre-registration. On x86 shapes, 1 OCPU is 2 vCPUs.
- **The protocol, as M1b ran it:**
  1. the R1 ramp (ADR-026);
  2. C, B and the sample-api and DB limits, re-derived on that host (ADR-026, ADR-027, ADR-028);
  3. the clean-baseline checkpoints (ADR-026, ADR-027, ADR-028);
  4. the S5 run (ADR-025 addendum).
- **Pre-registered** before the first run: every parameter above, the pass and void rules, and the **number of
  attempts, fixed in advance**. The result is reported whatever it is. No attempt beyond that count.
- **Deadline:** 2026-11-09, the trial's last day (billing guard).

### (c) Bedrock only for a pre-registered evaluation that finishes by 2026-11-03
- One evaluation, pre-registered in a decision gate: models and ids, prompts, evidence set, metrics and the stop
  rule. Its last call is before the AWS credit ends.
- **Nothing in the demo depends on Bedrock.** The Reasoner arms R2 and R3 stay on Groq (spec §16, "Reasoner
  arms"). The Bedrock evaluation is not an experimental arm unless the owner amends the spec.
- The Reasoner stays untrusted, and the provider receives only redacted evidence (spec). The AWS credential is a
  provider secret, held by the owner like the other provider keys. The agent never reads or prints it
  (CLAUDE.md rule 5).

### (d) Each host's CNI is fixed before its first calibration
- A CNI change after calibration voids that host's calibration.
- **Replication host (b): flannel**, with the k3s network-policy controller.
- **A1 host (a): flannel with k3s NetworkPolicy**, unless all three of these hold:
  1. an M2 deliverable needs L7 rules or FQDN egress;
  2. a read-only eBPF/BTF check passes on that host: kernel ≥ 5.10, `/sys/kernel/btf/vmlinux` present, and
     `CONFIG_BPF`, `CONFIG_BPF_SYSCALL`, `CONFIG_BPF_JIT` and `CONFIG_DEBUG_INFO_BTF` set to `y` in
     `/boot/config-$(uname -r)`. These are Cilium's stated requirements;
  3. Cilium's measured CPU and memory fit the idle-footprint budget from (a).

  Only then Cilium. The spec lists FQDN egress as DEFERRED with a stated limitation, and no M2 deliverable is listed
  yet (TASKS.md has no M2 section), so flannel is the expected outcome.

### Billing guard
- **Budget alerts on both accounts** (the owner creates them).
  - AWS Budgets only alert unless a budget action is configured. The data updates up to three times a day,
    8–12 hours apart, and charges can exceed the threshold before the alert.
  - OCI budgets are "soft limits", evaluated every 24 hours.
  - So set the thresholds low, at the first dollar of actual cost, and treat them as late signals, not caps.
- **Every paid resource is deleted before its credit ends:**
  - **OCI trial:** the x86 instance, its boot and block volumes, any reserved public IP and any paid load balancer.
    The A1 node stays within Always Free.
  - **AWS:** on-demand Bedrock inference leaves no standing resource. Delete everything else the evaluation creates
    (no provisioned throughput, knowledge bases or endpoints are planned). After the last call, deactivate the
    access key and deny `bedrock:InvokeModel` in IAM.
- **What each provider does when the credit ends** (public docs, read 2026-10-16):

| Provider | When the credit ends |
|---|---|
| **OCI** | The trial ends after 30 days or when the credit is spent, whichever comes first (the docs say US$300; this account has €250, ending 2026-11-09). A 30-day grace follows: no new paid resources can be created; paid trial resources are then reclaimed, permanently deleted. Always Free resources stay. A1 use above the Always Free limit is disabled, then deleted after 30 days. "Your credit card will not be charged unless you upgrade your account." Some special trial offers suspend the account immediately instead. |
| **AWS** | Unredeemed credit is forfeited at expiry. Use beyond the available credit, and all use after expiry, is billed to the payment method. Resources are not stopped. AWS Marketplace charges are ineligible "unless authorized by us", so they are billed from the first call. |

## Fact-check (public docs, read 2026-10-16)

| Question | Finding | Source |
|---|---|---|
| Always Free A1 limits | "the first 1,500 OCPU hours and 9,000 GB hours per month … equivalent to 2 OCPUs and 12 GB of memory", as one VM or two of 1 OCPU. Home region only; "out of host capacity" means a temporary shortage there. 200 GB block storage in total; boot volume minimum 47 GB. Page modified 2026-06-12. | [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) |
| Idle reclamation | Idle if, over 7 days, p95 CPU < 20 %, network < 20 % and memory < 20 % (memory for A1 only). Reclaimed only when all three hold. Separately, accounts idle for 30 days or more "may be deemed abandoned". | same page; [Free Tier FAQ](https://www.oracle.com/cloud/free/faq/) |
| Does the trial credit cover x86 shapes? | The credit is for "all eligible Oracle Cloud Infrastructure services". The trial lists Compute as "High-performance VMs and bare metal servers", and credits can create "more instances or larger instances than allowed for Always Free". No page names x86 shapes or the trial's per-shape service limits. **Likely yes; owner to confirm** in the Console (Limits, Quotas and Usage, for the chosen E-series or Intel Flex shape). | [Free Tier overview](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm); [Free Tier FAQ](https://www.oracle.com/cloud/free/faq/); [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) |
| Do the AWS credits cover Bedrock? | Credits apply to the services "designated by your AWS contracting entity". AWS Marketplace is ineligible "unless authorized by us". On Bedrock, Anthropic models are served through AWS Marketplace. The OpenAI open-weight models `gpt-oss-120b` and `gpt-oss-20b` have no Marketplace product key and need no Marketplace subscription. **Model by model: gpt-oss likely covered, Anthropic likely not; owner to confirm** on the account's Credits page (eligible services) before the evaluation is pre-registered. | [AWS Promotional Credit terms](https://aws.amazon.com/awscredits/); [Bedrock model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) |
| Which project images lack arm64? | **`ghcr.io/koussayx8/nexus-platform/sample-api` and `…/nexus-operator`**: their pinned indexes list `linux/amd64` only. `ci.yml` and `operator.yml` build without `platforms:`. The 21 other images running in the cluster publish `linux/arm64`: Argo CD, Dex, Redis, Kyverno (5), the kube-prometheus-stack images (7), Grafana, Locust (pinned digest), Postgres (pinned digest) and the k3s add-ons (3). The k3s `v1.34.6+k3s1` release ships `k3s-arm64`. The Reasoner image does not exist yet. | registry manifests read 2026-10-16T06:55Z (anonymous `GET /v2/<repo>/manifests/<ref>`); [k3s v1.34.6+k3s1 assets](https://github.com/k3s-io/k3s/releases/tag/v1.34.6%2Bk3s1) |
| Cilium kernel needs | Kernel ≥ 5.10; `CONFIG_BPF`, `CONFIG_BPF_SYSCALL`, `CONFIG_BPF_JIT`, `CONFIG_DEBUG_INFO_BTF` and others; AMD64 and AArch64 supported (docs for 1.20.3). | [Cilium system requirements](https://docs.cilium.io/en/stable/operations/system_requirements/); [k3s network options](https://docs.k3s.io/networking/basic-network-options) |
| Budgets | AWS: alert or action, updated up to three times a day, delayed. OCI: soft limits, evaluated every 24 hours. | [AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html); [OCI Budgets](https://docs.oracle.com/en-us/iaas/Content/Billing/Concepts/budgetsoverview.htm) |

**Owner facts (2026-10-15):** OCI trial €250, usable 2026-10-11 to 2026-11-09; AWS credits US$120, until
2026-11-03. One Free Tier account per person, so the A1 node and the x86 trial share one OCI account.

**Owner to confirm (account-specific):**
1. The OCI home region, and whether A1 capacity is available there.
2. The trial's service limit for the chosen x86 shape.
3. The AWS credit's eligible services, and whether they cover the chosen Bedrock model.
4. That no OCI upgrade to Pay As You Go is made. Without an upgrade the card is not charged.

## Relation to the frozen spec (contradictions reported, not resolved here)
Items 1–3 are in `TASKS.md`'s Spec v1.1 notes. ADR-030 governs until spec v1.1.

1. **The host:**
   - The spec places the cluster under WSL2, and the Experiment Runner, Fault Injector, Evaluation Harness, K8sGPT
     CLI and Ollama on the "Host (WSL2)" (spec §3, D2 deployment view; "Implementation to verify", "Node CPU and memory
     of the WSL2 VM").
   - On OCI, where the host components run, on the VM or on WSL2 against a remote API server, is **UNKNOWN**.
     An ADR-030 addendum decides it for the Experiment Runner, Fault Injector, Evaluation Harness and K8sGPT
     CLI, before (a)'s first calibration.
2. **The demo fallback:**
   - The spec's demo fallback is the Local Model via Ollama, "Offline demo only".
   - A cloud-hosted demo is not offline. A 2-OCPU node is unlikely to host a local model next to the stack:
     UNVERIFIED.
   - The recorded demo in (a) is a different fallback. Which of the two the spec keeps is the owner's decision.
3. **Bedrock** does not appear in the spec. (c) keeps it outside the arms.
4. **Cilium** stays DEFERRED in the spec. (d) is consistent with that: flannel is the default, and the conditions
   only bound a possible return.

## Consequences
- **Multi-arch images before (a):**
  - `ci.yml` and `operator.yml` must build `linux/arm64` (a workflow change, in its own PR);
  - the digests pinned in `overlays/` and the operator manifest must be re-pinned to the multi-arch index, with
    signatures re-verified (ADR-017);
  - the Reasoner image is built multi-arch from the start.
- **Bootstrap:** each host is bootstrapped by the owner (`bootstrap.sh` needs sudo; ADR-019). The agent creates no
  cloud resource and runs no Terraform.
- **Three calibrations exist from now on:** WSL2 (M1b), A1 and x86. A value is quoted with its host and never
  carried across.
- The x86 replication tests whether the M1b result depends on the host. Its pre-registered attempt count keeps it
  from turning into tuning.
- With the billing guard, the expected spend is zero. Charges are possible on AWS (Marketplace, after expiry) and on
  OCI only after an upgrade.

## References
- ADR-017 (image pin), ADR-019 (bootstrap), ADR-024 (detection), ADR-025 addendum (S5), ADR-026 (load, R1),
  ADR-027 (sample-api CPU limit), ADR-028 (DB CPU limit), ADR-029 (M2 working agreement).
- Sources in the fact-check table, read 2026-10-16.
