"""NEXUS baseline traffic for sample-api (spec §3 "Locust"; M1b-9; ADR-026).

One user class per environment. Each user sends one request per second
(constant_throughput(1)), so the request rate of a class equals its user count.

ProdUser has fixed_count = PROD_USERS (80): Locust gives it exactly 80 users
whatever the total, and every other user is a DevUser. The baseline (160)
stays 80 + 80; a spike is a plain user-count change on the running swarm
(POST /swarm user_count=N, no /stop, no class change) and lands in nexus-dev
only: N = 80 + the dev rate (S2: 220, so dev 140; S4/S6: 80 + the calibrated
dev rate). Checked on Locust 2.46.7 (master + worker): 160 -> 80/80,
220 -> 140/80, 170 -> 90/80, back to 160 -> 80/80 (Block 5 calibration notes,
2026-11-20).

The mix is fixed, not random: every user cycles "/", "/", "/", "/", "/items",
so /items is exactly 20 % of the requests. That is the S5 error share in the
promtool tests (platform/observability/tests/nexus-detection.test.yaml, case
4): under S5 every /items call fails, so the error ratio is about 20 %. The
load is not shaped to the detector; S5's first firing then checks promtool's
+105 s. /work/cpu is never called: it is S2's load generator, not a path a
scenario breaks (ADR-026).

Users are spread so the traffic stays smooth:
- each user starts its cycle at a different position (user index mod 5), so
  /items calls are not synchronised across users;
- each user starts at a different phase within the second (golden-ratio
  spacing), so requests do not arrive as one burst per second.
Bursts would hit sample-api's 5 DB slots per pod (ADR-020) and turn a load
artefact into db_slots_exhausted 503s.

Hosts default to the in-cluster Services; NEXUS_DEV_HOST and NEXUS_PROD_HOST
override them (the local smoke test points both at 127.0.0.1). Stats are named
"<env>:<path>", e.g. "dev:/items".
"""

import itertools
import os

import gevent
from locust import HttpUser, constant_throughput, task

SEQUENCE = ("/", "/", "/", "/", "/items")
GOLDEN = 0.6180339887498949
TIMEOUT_S = 5.0  # above sample-api's /items budget of 2.8 s (ADR-020)

_index = itertools.count()


class _Mix(HttpUser):
    abstract = True
    wait_time = constant_throughput(1)
    connection_timeout = TIMEOUT_S
    network_timeout = TIMEOUT_S

    def on_start(self):
        index = next(_index)
        self._position = index % len(SEQUENCE)
        gevent.sleep((index * GOLDEN) % 1.0)

    @task
    def request(self):
        path = SEQUENCE[self._position]
        self._position = (self._position + 1) % len(SEQUENCE)
        self.client.get(path, name=f"{self.env}:{path}")


class DevUser(_Mix):
    env = "dev"
    host = os.environ.get(
        "NEXUS_DEV_HOST", "http://sample-api.nexus-dev.svc.cluster.local"
    )


class ProdUser(_Mix):
    fixed_count = 80  # PROD_USERS: nexus-prod stays at the baseline in every spike
    env = "prod"
    host = os.environ.get(
        "NEXUS_PROD_HOST", "http://sample-api.nexus-prod.svc.cluster.local"
    )
