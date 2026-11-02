import os
import sys

import pytest

# Offline: no unit test may reach a cluster.
os.environ["KUBECONFIG"] = "/nonexistent"
sys.path.insert(0, os.path.dirname(__file__))

import pgfixture


@pytest.fixture(scope="session")
def pg():
    """A real PostgreSQL with the Flight Recorder schema and roles; skips without server binaries, except in CI.

    The write-ahead rule is a safety invariant: in CI (the CI variable is set) a missing server
    fails the run instead of skipping the tests.
    """
    server = pgfixture.start()
    if server is None and os.environ.get("CI"):
        pytest.fail(
            "CI must run the PostgreSQL tests: no server binaries (initdb) found"
        )
    if server is None:
        pytest.skip("no PostgreSQL server binaries (initdb) on this machine")
    yield server
    server.stop()
