"""Offline tests for the bake-off. The path set-up mirrors bakeoff.py: operator/ and reasoner/
on the import path, so nexus_operator and nexus_reasoner import without their requirements."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for p in (REPO, REPO / "operator", REPO / "reasoner"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
