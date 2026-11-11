import io
import json
from pathlib import Path

from nexus_operator import proposal
from nexus_reasoner import config
from nexus_reasoner.retrieval import Retriever
from nexus_reasoner.service import Reasoner

from experiments.harness import bm25

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "experiments/harness/tests/fixtures/evidence-v1.1-raw.json"

CFG = config.parse(
    {
        "indexPath": "x",
        "scoreFloor": 1.0,
        "models": {
            "R2": {"provider": "groq", "model": "openai/gpt-oss-20b", "baseUrl": "https://g.test/openai/v1", "apiKeyEnv": "T_GROQ_KEY"},
            "R3": {"provider": "groq", "model": "openai/gpt-oss-120b", "baseUrl": "https://g.test/openai/v1", "apiKeyEnv": "T_GROQ_KEY"},
            "H1": {"provider": "anthropic", "model": "claude-haiku-5-5", "baseUrl": "https://a.test", "apiKeyEnv": "T_ANT_KEY"},
            "demo": {"provider": "ollama", "model": "llama-demo", "baseUrl": "http://o.test:11434"},
            "nourl": {"provider": "ollama", "model": "no-url", "baseUrl": ""},
        },
    }
)  # fmt: skip


def bundle() -> dict:
    b = json.loads(RAW.read_text())
    b["redaction"] = {"counts": {"token": 0}}
    b.pop("hash", None)
    b["hash"] = proposal.hash_of(b)
    return b


def retriever(floor: float = 1.0) -> Retriever:
    return Retriever(bm25.Index.from_dir(REPO / "runbooks"), floor)


def reasoner(opener=None, floor: float = 1.0) -> Reasoner:
    return Reasoner(CFG, retriever(floor), opener)


def good_proposal(b: dict, refs=("metrics.zscores.latencyP95",), runbooks=()) -> dict:
    return {
        "schema": "proposal/v1",
        "hypothesis": {"faultClass": "gray_failure", "summary": "handlers hang"},
        "evidenceRefs": list(refs),
        "runbookRefs": list(runbooks),
        "action": {
            "id": "restart_workload",
            "target": b["incident"]["target"],
            "parameters": {},
        },
        "expectedEffect": "latency returns",
        "confidence": 0.8,
        "escalation": None,
        "abstain": False,
    }


class FakeResp:
    def __init__(self, body: dict):
        self._b = io.BytesIO(json.dumps(body).encode())

    def read(self):
        return self._b.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Records requests; answers `reply(req_url, body)` or raises."""

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.calls = reply, exc, []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data)
        self.calls.append(
            {
                "url": req.full_url,
                "headers": dict(req.header_items()),
                "body": body,
                "timeout": timeout,
            }
        )
        if self.exc:
            raise self.exc
        return FakeResp(self.reply(req.full_url, body))
