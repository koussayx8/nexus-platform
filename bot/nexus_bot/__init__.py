"""nexus-bot: the Telegram approval bot (ADR-040, TASKS.md D9 and D21).

A client, like nexusctl. It reads Incidents as its own ServiceAccount and writes `spec.approval`
only by impersonating the approver; RBAC, Kyverno K5 and the CRD rules decide every write.
"""
