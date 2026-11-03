"""The harness reuses the operator's own modules; it keeps no second copy of them.

  nexus_operator.redact     the Evidence Redactor (Q1: the operator redacts, the harness receives)
  nexus_operator.proposal   proposal/v1 validation and the canonical hash
  nexus_operator.catalogue  the frozen Action Catalogue
  nexus_operator.rules      the embedded Rules Engine R1
  nexus_operator.model      Incident group/plural/namespace and the terminal phases

All five import only the standard library, and `nexus_operator/__init__.py` imports nothing, so
putting `operator/` on sys.path is enough: no operator requirement (kopf, aiohttp) is installed
for the harness tests. evidence.py is NOT imported (it pulls aiohttp through kube.py); the harness
consumes its evidence/v1 *format*, which the tests pin with a fixture shaped like its output.
If one of these modules ever imports a third-party package, extract the shared part into a small
stdlib-only module instead of copying it.
"""

import pathlib
import sys

OPERATOR_DIR = pathlib.Path(__file__).resolve().parents[2] / "operator"
if str(OPERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(OPERATOR_DIR))

from nexus_operator import catalogue, model, proposal, redact, rules

__all__ = ["catalogue", "model", "proposal", "redact", "rules"]
