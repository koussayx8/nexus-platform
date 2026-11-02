"""Arm configurations (spec sections 16, 20; owner decisions 2026-11-02).

Reasoner arms R0-R3 as frozen in the spec, plus two additions the owner kept in scope:
  - the Haiku arm (H1): a small hosted model, same prompt, same settings;
  - the effort sweep: R3 at three reasoning-effort levels (the spec's default is "low").
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
    "R0": {"mode": "adversarial", "model": None, "provider": None, "purpose": "worst case, seeded generator"},
    "R1": {"mode": "rules", "model": None, "provider": None, "purpose": "deterministic baseline"},
    "R2": {"mode": "llm", "model": "openai/gpt-oss-20b", "provider": "groq", "reasoning_effort": "low", "purpose": "small model"},
    "R3": {"mode": "llm", "model": "openai/gpt-oss-120b", "provider": "groq", "reasoning_effort": "low", "purpose": "large model"},
    "H1": {"mode": "llm", "model": "claude-haiku-5-5", "provider": "anthropic", "reasoning_effort": "low", "purpose": "Haiku arm (owner decision 2026-11-02)"},
}  # fmt: skip

EFFORTS = ("low", "medium", "high")


def sweep(base: str = "R3") -> dict:
    """Effort sweep: the base arm at every effort level. `<base>-low` equals the base arm."""
    b = ARMS[base]
    if b["mode"] != "llm":
        raise ValueError(f"{base} has no reasoning effort")
    return {
        f"{base}-{e}": {**b, "reasoning_effort": e, "purpose": f"effort sweep, {base}"}
        for e in EFFORTS
    }


def arm_config(name: str) -> dict:
    """Resolved settings for an arm or sweep name, with the common model settings."""
    table = {**ARMS, **sweep("R3")}
    if name not in table:
        raise KeyError(f"unknown arm {name}; known: {sorted(table)}")
    return {"arm": name, **COMMON, **table[name]}


# id -> (reasoner arms, levels, phase). C5 and C6 have no reasoner arm.
CONFIGURATIONS = {
    "C1": (("R1",), ("L3",), "A"),
    "C2": (("R3",), ("L3",), "A"),
    "C3": (("R0", "R1", "R2", "R3", "H1"), ("L0", "L1", "L2", "L3"), "B"),
    "C4": ((), ("L3",), "C"),
    "C5": ((), (), "C"),
    "C6": ((), (), "A"),
    "C7": (("R0",), ("L3",), "D"),
}
