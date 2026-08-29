"""How good are the confidence scores themselves?

Two different questions, routinely conflated:

- CALIBRATION: when the tool says 0.9, is it right 90% of the time?
  Measured by the reliability table (evaluate.py's calibration frame), and
  summarised here by ECE and the Brier score.  Bad calibration can be
  repaired after the fact (recalibration maps claimed scores to observed
  rates) -- IF the ranking underneath is sound.
- DISCRIMINATION: do wrong fields score lower than correct ones at all?
  Measured by AUROC: the probability that a randomly chosen correct field
  outranks a randomly chosen wrong one.  Bad discrimination cannot be
  repaired by any recalibration, and the straight-through gate only ever
  uses the ranking -- so AUROC, not ECE, is what decides whether a
  confidence gate can work.

All metrics run over "scored" rows: a confidence is present and the
comparator reached a definite match/mismatch verdict.  Computed against
the gold regime they describe the score's true quality; computed against
the filed proxy they come out worse than reality, because label noise
(amendments) counts calibrated correct answers as errors.  Showing both is
the lesson.

Plain pandas and stdlib; AUROC is the rank-based Mann-Whitney form.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from comparators import MATCH, MISMATCH


def scored_rows(comparisons: pd.DataFrame) -> pd.DataFrame:
    """Rows that can teach us about the score: confidence present and a
    definite verdict.  Not-comparable rows and structure rows (which carry
    no confidence) are excluded."""
    return comparisons[
        comparisons["confidence"].notna() & comparisons["status"].isin([MATCH, MISMATCH])
    ]


def brier_score(scored: pd.DataFrame) -> float:
    """Mean squared error of the confidence against the 0/1 outcome.
    0 is perfect; 0.25 is what a coin-flip outcome with a constant 0.5
    score earns.  A proper scoring rule: it rewards honesty."""
    if scored.empty:
        return math.nan
    outcome = (scored["status"] == MATCH).astype(float)
    return float(((scored["confidence"] - outcome) ** 2).mean())


def expected_calibration_error(scored: pd.DataFrame, n_bins: int = 10) -> float:
    """ECE: the reliability table collapsed to one number -- the average
    |claimed confidence - observed accuracy| across bins, weighted by how
    many fields land in each bin."""
    if scored.empty:
        return math.nan
    bins = (scored["confidence"] * n_bins).astype(int).clip(upper=n_bins - 1)
    total = len(scored)
    ece = 0.0
    for _, group in scored.groupby(bins):
        accuracy = float((group["status"] == MATCH).mean())
        ece += len(group) / total * abs(float(group["confidence"].mean()) - accuracy)
    return ece


def auroc(scored: pd.DataFrame) -> float:
    """Probability that a random correct field carries a higher confidence
    than a random wrong one (ties count half).  1.0 = the score separates
    right from wrong perfectly; 0.5 = the score is noise.  Rank-based
    (Mann-Whitney), so it only sees the ordering -- exactly what a
    threshold gate sees."""
    is_match = scored["status"] == MATCH
    n_pos = int(is_match.sum())
    n_neg = len(scored) - n_pos
    if n_pos == 0 or n_neg == 0:
        return math.nan
    ranks = scored["confidence"].rank(method="average")
    u = float(ranks[is_match].sum()) - n_pos * (n_pos + 1) / 2
    return u / (n_pos * n_neg)


@dataclass(frozen=True)
class ConfidenceQualitySummary:
    """One regime's confidence scorecard."""

    label: str        # e.g. "vs gold (truth)" / "vs filed (proxy)"
    n_scored: int
    accuracy: float
    mean_confidence: float
    ece: float
    brier: float
    auroc: float


def summarise(comparisons: pd.DataFrame, label: str) -> ConfidenceQualitySummary:
    scored = scored_rows(comparisons)
    if scored.empty:
        nan = math.nan
        return ConfidenceQualitySummary(label, 0, nan, nan, nan, nan, nan)
    return ConfidenceQualitySummary(
        label=label,
        n_scored=len(scored),
        accuracy=float((scored["status"] == MATCH).mean()),
        mean_confidence=float(scored["confidence"].mean()),
        ece=expected_calibration_error(scored),
        brier=brier_score(scored),
        auroc=auroc(scored),
    )


def per_field_quality(comparisons: pd.DataFrame) -> pd.DataFrame:
    """The same scorecard per field, because a pooled 0.9 hides that 0.9
    on an HS code and 0.9 on a date can mean different things.  The
    calibration gap is signed: positive = overconfident."""
    scored = scored_rows(comparisons)
    rows = []
    for (level, field), group in scored.groupby(["level", "field"], sort=False):
        accuracy = float((group["status"] == MATCH).mean())
        mean_confidence = float(group["confidence"].mean())
        rows.append(
            {
                "level": level,
                "field": field,
                "n_scored": len(group),
                "accuracy": accuracy,
                "mean_confidence": mean_confidence,
                "calibration_gap": mean_confidence - accuracy,
                "auroc": auroc(group),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("calibration_gap", ascending=False)
        .reset_index(drop=True)
    )
