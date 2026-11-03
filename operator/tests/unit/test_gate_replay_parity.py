"""The Phase B replay (experiments/containment/phase_b.py) judges stages 1-3 with its own pure
function. This test keeps it in step with the real Safety Gate: for the adversarial proposals of R0
and the engine's own, both give the same failing stage and reason code."""

import pathlib
import sys

import pytest

from fakes_act import DEP, NS, Clock, FakeCluster, FakeRecorder
from nexus_operator import rules
from nexus_operator.gate import Gate

ROOT = pathlib.Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "reasoner"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from experiments.containment import phase_b
from nexus_reasoner import adversarial

SPEC_TARGET = {"namespace": NS, "kind": "Deployment", "name": DEP}


def bundle():
    b = {
        "incident": {"uid": "u-1", "target": SPEC_TARGET},
        "workload": {"desired": 2},
        "hash": "sha256:x",
    }
    return b


def real_gate(doc):
    import asyncio

    cluster = FakeCluster(replicas=2, level="3")
    gate = Gate(cluster, cluster, FakeRecorder(), advisory=True, clock=Clock())
    incident = {
        "metadata": {"uid": "u-1", "name": "inc"},
        "spec": {"target": SPEC_TARGET},
    }
    res = asyncio.run(gate.run(incident, doc, upto=3))
    return (None, None) if res.passed else (res.stage, res.reason)


def cases():
    for case in adversarial.CASES:
        for seed in range(8):
            out, _ = adversarial.generate(
                bundle(), {"adversarialCase": case.name, "seed": seed}
            )
            if isinstance(out, dict):
                yield pytest.param(out, id=f"{case.name}-{seed}")
    yield pytest.param(
        rules.propose(
            {**bundle(), "metrics": {"zscores": {"cpu": 5, "latencyP95": 5}}}
        ),
        id="r1",
    )
    yield pytest.param({"schema": "proposal/v2"}, id="not-a-proposal")
    yield pytest.param(
        {
            "schema": "proposal/v1",
            "hypothesis": {"faultClass": "unknown", "summary": "s"},
            "evidenceRefs": [],
            "runbookRefs": [],
            "action": {
                "id": "scale_deployment",
                "target": SPEC_TARGET,
                "parameters": {"replicas": 3},
            },
            "expectedEffect": "e",
            "confidence": 1,
            "escalation": None,
            "abstain": False,
        },
        id="in-envelope-scale",
    )


@pytest.mark.parametrize("doc", list(cases()))
def test_stages_1_to_3_agree_with_the_real_gate(doc):
    stage, reason, _ = phase_b.static_gate(doc, SPEC_TARGET, 2)
    assert (stage, reason) == real_gate(doc)
