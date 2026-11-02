"""Instruction-like phrase heuristics (spec §16A explain view).

A display aid: the flag warns the Approver, and it never influences any decision, gate or
policy. Matching is deliberately generous; a false positive costs a warning line.
"""

import re

_PATTERNS = [
    r"\bignore\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,30}\b(instruction|prompt|rule|message)s?\b",
    r"\bdisregard\b.{0,40}\b(instruction|prompt|rule|policy|polic\w+)s?\b",
    r"\b(system|developer)\s+(prompt|message)\b",
    r"\byou\s+(are|must|should)\s+(now|always|only)\b",
    r"\bnew\s+instructions?\b",
    r"\b(scale|set)\b.{0,30}\b(replicas?|to)\b.{0,12}\b\d+\b",
    r"\b(approve|approved|approval)\b.{0,30}\b(this|it|immediately|now)\b",
    r"\b(delete|drain|cordon|exec)\b.{0,30}\b(pod|node|deployment|namespace|secret)s?\b",
    r"\bkill\s*switch\b",
    r"<\|.*?\|>|\[/?INST\]|###\s*(system|instruction)",
    r"\b(assistant|system)\s*:",
]
_RX = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _PATTERNS]


def looks_like_instruction(text: str) -> bool:
    return any(rx.search(text) for rx in _RX)


def count_flagged(lines: list[str]) -> int:
    return sum(1 for line in lines if looks_like_instruction(line))
