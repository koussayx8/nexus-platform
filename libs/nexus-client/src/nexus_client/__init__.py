"""nexus-client: the typed Incident, approval pinning and Kubernetes access behind nexusctl.

It adds no authority: every write is made with the human's own kubeconfig and is authorised by
RBAC, Kyverno K5/K6 and the CRD rules, exactly like any other client's (spec §16A).
"""
