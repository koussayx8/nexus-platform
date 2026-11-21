import os

# Offline only: no test may reach a cluster.
os.environ["KUBECONFIG"] = "/nonexistent"
