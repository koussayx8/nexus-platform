"""The NEXUS Operator, M1b-8 skeleton (spec §4; plan M1b-8 rev 3; ADR-023, ADR-025).

Two components, and no mutation outside Incidents:
  poller.py     Alert Poller: Alertmanager alerts -> Incident CRs, one per firing episode
  reconcile.py  Incident Reconciler: the 5 s status-only loop, the single writer of the phase
main.py wires them into Kopf (standalone, status-based persistence, no finalizers).
"""
