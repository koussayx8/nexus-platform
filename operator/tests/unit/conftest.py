import os
import sys

# Offline: no unit test may reach a cluster.
os.environ["KUBECONFIG"] = "/nonexistent"
sys.path.insert(0, os.path.dirname(__file__))
