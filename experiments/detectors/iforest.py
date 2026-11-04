"""The Isolation Forest arm of the detector comparison (spec section 21; scikit-learn is MUST, spec 2033).

OFFLINE AND ANALYSIS-ONLY: scikit-learn and numpy are pinned with hashes in
experiments/detectors/requirements.txt, installed by the `detectors` CI job and by whoever runs the
comparison; they are never in the operator, the Reasoner or any image, and nothing outside
experiments/detectors imports this module. sklearn is imported lazily, so the Z-score side and the
tests that do not need a forest run on the standard library alone.

The forest sees the same five signals as the Z-score rules (their raw values at one 30 s point),
is fitted on baseline windows only (the early runs: spec section 21, "calibrate on early baseline
days, test on later ones"), and flags a point when its score falls below a threshold taken from the
baseline itself, `THRESHOLD_QUANTILE` of the training scores, so the decision rule needs no labelled
fault. An alert then needs the same `for` as the Z-score alerts (60 s: three consecutive flagged
points), so the two detectors are compared as alerts, not as raw per-point scores.

PROPOSED parameters (the PR proposes, G16 freezes them in the pre-registration):
  n_estimators=200      enough trees for the score to settle at five features and a few hundred points
  max_samples=256       sklearn's own default cap; the baseline has fewer points, so each tree sees all
  max_features=1.0      all five signals per tree
  bootstrap=False, contamination="auto" (the threshold below replaces contamination)
  random_state=1, n_jobs=1   fixed seed; one job, so the output is reproducible
  THRESHOLD_QUANTILE=0.02    one baseline point in 50 flagged, chosen before looking at any fault: an
                             alert needs three consecutive flagged points, so with independent points a
                             false alert has probability 0.02^3 = 8e-6 per 30 s evaluation (about one in
                             43 days). Real baseline points are correlated, so G16 calibrates this on
                             the early baseline days and freezes it; D4 of the output shows how the
                             comparison moves with it, as information, not for tuning.
"""

from __future__ import annotations

SIGNALS = ("cpu", "latencyP95", "inflight", "errorRatio", "requests")
PARAMS = {
    "n_estimators": 200,
    "max_samples": 256,
    "max_features": 1.0,
    "bootstrap": False,
    "contamination": "auto",
    "random_state": 1,
    "n_jobs": 1,
}
THRESHOLD_QUANTILE = 0.02
SWEEP = (
    0.005,
    0.01,
    0.02,
    0.05,
    0.10,
)  # sensitivity only, never for choosing the value


class MissingDependency(RuntimeError):
    pass


def _sklearn():
    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest
    except ImportError as e:  # pragma: no cover - exercised by the missing-sklearn test
        raise MissingDependency(
            "scikit-learn is analysis-only: pip install --only-binary=:all: --require-hashes "
            "-r experiments/detectors/requirements.txt"
        ) from e
    return np, IsolationForest


class Detector:
    def __init__(
        self, params: dict | None = None, quantile: float = THRESHOLD_QUANTILE
    ):
        self.params = {**PARAMS, **(params or {})}
        self.quantile = quantile
        self.model = None
        self.threshold = None

    def fit(self, rows: list[list[float]]) -> Detector:
        """rows: baseline points, one list of the five signals each."""
        np, IsolationForest = _sklearn()
        if len(rows) < 2:
            raise ValueError("an Isolation Forest needs at least two baseline points")
        x = np.asarray(rows, dtype=float)
        self.model = IsolationForest(**self.params).fit(x)
        self.threshold = float(np.quantile(self.model.score_samples(x), self.quantile))
        return self

    def scores(self, rows: list[list[float]]) -> list[float]:
        np, _ = _sklearn()
        return [
            float(s) for s in self.model.score_samples(np.asarray(rows, dtype=float))
        ]

    def flags(self, rows: list[list[float]]) -> list[bool]:
        """Per point: anomalous when the score is below the baseline threshold."""
        return [s < self.threshold for s in self.scores(rows)]


def persist(
    flags: dict[float, bool], for_s: int = 60, step_s: int = 30
) -> dict[float, bool]:
    """Firing at t when the point was flagged at t and at every step of the `for` before it: the
    same persistence the Z-score alerts have."""
    need = for_s // step_s + 1
    return {
        t: all(flags.get(t - k * step_s, False) for k in range(need)) for t in flags
    }
