"""Offline tests for the Reasoner. Run from the repository root:

    python -m unittest discover -s reasoner/tests -t reasoner -v

The path set-up mirrors the image: nexus_operator/ and experiments/ at the import root.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "reasoner", REPO / "operator", REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
