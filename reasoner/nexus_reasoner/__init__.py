"""The NEXUS Reasoner service (spec section 16): untrusted, stateless, no Kubernetes credentials.

Standard library only. It imports three of the operator's stdlib-only modules (catalogue, proposal,
rules) and the harness's bm25.py; the image copies those files at build time, the repository keeps
one copy of each.
"""
