"""Statistics of spec section 21: Wilson interval, rule of three, median and IQR.

No significance tests and no p-values (spec section 21)."""

from __future__ import annotations

import math

Z95 = 1.96


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson 95% interval for k successes out of n; None when n == 0."""
    if n <= 0:
        return None
    if not 0 <= k <= n:
        raise ValueError(f"k={k} outside 0..{n}")
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - spread) / denom), min(1.0, (centre + spread) / denom))


def proportion(k: int, n: int) -> dict:
    """A proportion as the report prints it: k, n, estimate, Wilson interval."""
    ci = wilson(k, n)
    return {
        "k": k,
        "n": n,
        "p": (k / n) if n else None,
        "lo": ci[0] if ci else None,
        "hi": ci[1] if ci else None,
    }


def rule_of_three(n: int) -> float | None:
    """Upper 95% bound on the per-trial rate after zero events in n trials."""
    return 3.0 / n if n > 0 else None


def _quantile(sorted_xs: list[float], q: float) -> float:
    # Linear interpolation between closest ranks (the "type 7" definition).
    pos = (len(sorted_xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def median_iqr(xs: list[float]) -> dict | None:
    """Median and interquartile range; never mean and standard deviation."""
    if not xs:
        return None
    s = sorted(xs)
    q1, med, q3 = _quantile(s, 0.25), _quantile(s, 0.5), _quantile(s, 0.75)
    return {"n": len(s), "median": med, "q1": q1, "q3": q3}
