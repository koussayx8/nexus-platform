# ADR-005: Internal Developer Platform (IDP) Design

## Status
Accepted

## Context
As the NEXUS platform matures with GitOps (ArgoCD) and Policy-as-Code (Kyverno) foundations established, we need a centralized hub to reduce cognitive load on developers. An Internal Developer Platform (IDP) is required to provide a single pane of glass for service ownership, infrastructure visibility, and "Golden Path" software templates. 

We evaluated several options for the IDP, primarily focusing on Backstage, Port, and Cortex.

## Decision

### 1. IDP Selection: Backstage
We have decided to adopt **Backstage** over SaaS alternatives like Port and Cortex. 

**Why Backstage over Port/Cortex:**
* **No Vendor Lock-in (Open Source):** Backstage is a CNCF incubating project with immense community backing. Unlike Cortex and Port which are commercial SaaS products, Backstage allows us to completely own our IDP implementation.
* **Extensibility:** Backstage's plugin architecture is unparalleled. If we need a specific integration that doesn't exist, we can build it. We are not artificially constrained by a vendor's roadmap.
* **GitOps Alignment via Scaffolder:** Backstage's Software Templates allow us to encode platform standards (like our required `nexus.io/autonomy-level: "0"` annotation and `platform-contract.yaml`) directly into the repository generation process.

### 2. Hosting Decision
* We will **self-host** Backstage within our existing Kubernetes infrastructure.
* **Rationale:** Self-hosting ensures all platform components are managed via our established GitOps pipelines (ArgoCD) and keeps sensitive infrastructure metadata strictly within our perimeter. We avoid creating an external dependency for core developer workflows.

### 3. Plugin Strategy
To avoid UI bloat and maintain a focused developer experience, our initial plugin strategy is highly targeted:
* **Software Catalog:** The core registry driven by `catalog-info.yaml` files alongside source code.
* **Software Templates (Scaffolder):** For standardizing new service creation (e.g., Golden Path for FastAPI services).
* **ArgoCD Plugin:** To expose immediate deployment feedback (Sync Status, Health) directly on the component's catalog page, bridging the gap between code and runtime state.

## Consequences
* **Positive:** Developers get a unified portal for their services, and new services automatically adhere to platform contracts via templates.
* **Negative:** Backstage introduces a higher operational and maintenance burden compared to managed SaaS solutions, requiring us to manage a Node/React application and its update lifecycle.
