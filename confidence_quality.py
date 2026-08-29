"""How good are the confidence scores themselves?

Two different questions, routinely mixed up:

- IS THE SCORE HONEST?  (the literature says "calibration")  When the
  tool says 0.9, is it right about 9 times in 10?  Shown in full by the
  band-by-band table (evaluate.py's calibration frame) and summarised
  here by the honesty gap (ECE) and the error score (Brier).  A
  dishonest-but-consistent score can be repaired after the fact: once you
  know "0.9 really means 0.8", you relabel it -- IF the ordering
  underneath is sound.
- CAN IT TELL RIGHT FROM WRONG AT ALL?  (the literature says
  "discrimination")  Pick one field the tool got right and one it got
  wrong at random: the sorting power (AUROC) is how often the right one
  carries the higher score.  Poor sorting power cannot be repaired by any
  relabelling, and the straight-through gate relies on the ordering
  alone -- so AUROC, not ECE, decides whether a confidence gate can work.

All metrics run over "scored" rows: a confidence is present and the
comparator reached a definite match/mismatch verdict.  Computed against
the gold truth they describe the score's real quality; computed against
the filed record they read somewhat worse, because a noisy answer key
counts some genuinely correct answers as errors.  Showing both is the
lesson.

Plain pandas and stdlib throughout.
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
    """The "error score": how far each confidence sat from what actually
    happened (1 for a match, 0 for a mismatch), squared and averaged.
    Squaring makes confidently-wrong hurt far more than hesitantly-wrong.
    0 is perfect; a constant 0.5 score on coin-flip outcomes earns 0.25.
    (In the literature: a proper scoring rule -- the best strategy is to
    report what you truly believe.)"""
    if scored.empty:
        return math.nan
    outcome = (scored["status"] == MATCH).astype(float)
    return float(((scored["confidence"] - outcome) ** 2).mean())


def expected_calibration_error(scored: pd.DataFrame, n_bins: int = 10) -> float:
    """The "honesty gap" (ECE): group scores into bands, and in each band
    take the distance between the average claimed confidence and the share
    that were actually right; average those distances, weighting each band
    by how many fields landed in it.  0 means the score keeps its
    promises."""
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
    """The "sorting power" (AUROC): pick one field the tool got right and
    one it got wrong, at random -- how often does the right one carry the
    higher confidence?  (Ties count half.)  1.0 = the score separates
    right from wrong perfectly; 0.5 = coin flip.  Computed from the
    ordering of the scores only (the Mann-Whitney rank form), which is
    exactly what a threshold gate sees."""
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
