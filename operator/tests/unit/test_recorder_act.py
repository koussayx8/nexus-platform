"""The fakes' fidelity to the real recorder and mutation APIs, and the placeholder recorder.
The PostgreSQL tests of the two extra reads are in test_recorder_act_pg.py."""

import asyncio
import inspect

import pytest

from fakes_act import FakeCluster, FakeRecorder
from nexus_operator.mutations import Mutations
from nexus_operator.recorder import (
    ActRecorder,
    PostgresActRecorder,
    Recorder,
    RecorderUnavailable,
    UnavailableRecorder,
)


def methods(cls) -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(fn)
        for name, fn in inspect.getmembers(cls, inspect.iscoroutinefunction)
        if not name.startswith("_")
    }


def shape(sig: inspect.Signature):
    """Parameter names, kinds and defaults, without `self` and annotations."""
    return [
        (p.name, p.kind, p.default) for p in sig.parameters.values() if p.name != "self"
    ]


def protocol_methods(proto) -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(fn)
        for name, fn in inspect.getmembers(proto, inspect.isfunction)
        if not name.startswith("_")
    }


@pytest.mark.parametrize("cls", [FakeRecorder, PostgresActRecorder])
def test_recorders_match_the_protocol(cls):
    """The fake and the real Postgres recorder have every ActRecorder method, same signature."""
    want = protocol_methods(ActRecorder)
    assert set(want) >= set(protocol_methods(Recorder))
    have = methods(cls)
    for name, sig in want.items():
        assert name in have, f"{cls.__name__} lacks {name}"
        assert shape(have[name]) == shape(sig), name


def test_the_fake_cluster_matches_the_mutation_requests():
    for name in ("patch_scale", "evict"):
        assert shape(inspect.signature(getattr(FakeCluster, name))) == shape(
            inspect.signature(getattr(Mutations, name))
        ), name


def test_the_unavailable_recorder_refuses_every_call_and_covers_the_protocol():
    rec = UnavailableRecorder()
    names = set(protocol_methods(ActRecorder))
    assert names <= {n for n in dir(rec) if not n.startswith("_")}

    async def go():
        for name in sorted(names):
            with pytest.raises(RecorderUnavailable):
                await getattr(rec, name)()

    asyncio.run(go())
