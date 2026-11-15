"""Arm configurations (spec sections 16, 20).

Reasoner arms R0-R3 as frozen in the spec, and nothing else: the Haiku arm (H1) and the effort
sweep, kept in scope on 2026-11-02, were cut on 2026-11-12 (TASKS.md decisions D3 and D10; no new
LLM provider). `phases` lists where each arm may run, and arms_for_phase() is the only selector.
Model ids live here, in configuration, and are recorded with every proposal.
Experiment configurations C1-C9 are listed with their level and phase.
"""

from __future__ import annotations

COMMON = {
    "temperature": 0,
    "max_output_tokens": 1024,
    "timeout_s": 30,
    "attempts": 1,
    "json_schema_output": True,
    "seed": 0,
}

ARMS = {
    "R0": {"mode": "adversarial", "model": None, "provider": None, "phases": ("B", "D"), "purpose": "worst case, seeded generator"},
    "R1": {"mode": "rules", "model": None, "provider": None, "phases": ("A", "B", "C"), "purpose": "deterministic baseline"},
    "R2": {"mode": "llm", "model": "openai/gpt-oss-20b", "provider": "groq", "reasoning_effort": "low", "phases": ("B",), "purpose": "small model"},
    "R3": {"mode": "llm", "model": "openai/gpt-oss-120b", "provider": "groq", "reasoning_effort": "low", "phases": ("A", "B"), "purpose": "large model"},
}  # fmt: skip


def arm_config(name: str) -> dict:
    """Resolved settings for an arm, with the common model settings."""
    if name not in ARMS:
        raise KeyError(f"unknown arm {name}; known: {sorted(ARMS)}")
    return {"arm": name, **COMMON, **ARMS[name]}


# id -> (reasoner arms, levels, phase). C5 and C6 have no reasoner arm.
CONFIGURATIONS = {
    "C1": (("R1",), ("L3",), "A"),
    "C2": (("R3",), ("L3",), "A"),
    "C3": (("R0", "R1", "R2", "R3"), ("L0", "L1", "L2", "L3"), "B"),
    "C4": ((), ("L3",), "C"),
    "C5": ((), (), "C"),
    "C6": ((), (), "A"),
    "C7": (("R0",), ("L3",), "D"),
}


def arms_for_phase(phase: str) -> list[str]:
    """Arm names allowed in a phase (A live, B offline replay, C live baselines, D containment)."""
    return sorted(n for n, c in ARMS.items() if phase in c["phases"])
