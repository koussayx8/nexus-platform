import os

os.environ["KUBECONFIG"] = "/nonexistent"  # offline only: no test may reach a cluster
