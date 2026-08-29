"""Error bars: how much could each percentage wobble by pure chance?

A rate computed from 500 documents is not a fact, it is an estimate, and
the honest way to present it is with the range of values it could
plausibly stand for.  Standard library only, on purpose.  Two tools, two
lessons:

- The bootstrap: re-draw the same number of documents at random (allowing
  repeats) hundreds of times, recompute the rate each time, and read the
  range off the spread.  Crude, general and honest -- it works for any
  statistic, not just percentages -- shown here in its plainest form so
  the mechanics are visible.  Used for the headline clean document rate.
- The Wilson score interval: a ready-made formula for "how sure can you
  be about a percentage based on n cases".  Instant, and it behaves well
  for small slices and rates near 0% or 100%, so it is what we use for
  every per-slice rate and for the audit-sample estimate.

The audit-sample estimator is how the gold regime survives contact with
production: nobody has the truth for the whole population, but you can
have a person grade a random sample of K documents and let the interval
say what that sample is worth.  The interval's width is the price list
for human checking: to halve it, grade four times as many documents.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import pandas as pd

Z_95 = 1.96  # two-sided 95%


def bootstrap_rate_ci(
    flags, n_boot: int = 500, seed: int = 1, alpha: float = 0.05
) -> tuple[float, float]:
    """The likely range for a yes/no rate: re-draw n documents at random
    (allowing repeats) n_boot times, recompute the rate each time, and
    take the middle 95% of the results.  Deterministic for a given seed."""
    values = [bool(v) for v in flags]
    n = len(values)
    if n == 0:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    rates = sorted(sum(rng.choices(values, k=n)) / n for _ in range(n_boot))
    low_index = int((alpha / 2) * n_boot)
    high_index = min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    return (rates[low_index], rates[high_index])


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """The likely range for a percentage seen `successes` times out of `n`
    (the Wilson score interval -- a standard closed-form formula)."""
    if n == 0:
        return (math.nan, math.nan)
    p = successes / n
    z2 = z * z
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    half_width = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return (max(0.0, centre - half_width), min(1.0, centre + half_width))


def breakdown_clean_rate_cis(doc_summary: pd.DataFrame) -> pd.DataFrame:
    """Wilson 95% CI on the clean document rate of every country x source
    system slice.  Small slices get honest, wide intervals -- which is the
    point: the breakdown table should not be over-read."""
    rows = []
    for (country, system), group in doc_summary.groupby(["country", "source_system"]):
        low, high = wilson_interval(int(group["clean"].sum()), len(group))
        rows.append(
            {
                "country": country,
                "source_system": system,
                "documents": len(group),
                "clean_ci_low": low,
                "clean_ci_high": high,
            }
        )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class AuditEstimate:
    """A clean-document-rate estimate from a small graded sample."""

    sample_size: int
    clean_in_sample: int
    estimate: float
    ci_low: float
    ci_high: float
    sampled_doc_ids: tuple[str, ...]

    @property
    def ci_width(self) -> float:
        return self.ci_high - self.ci_low


def audit_sample_estimate(
    gold_doc_summary: pd.DataFrame, sample_size: int, seed: int = 7
) -> AuditEstimate:
    """Estimate the true clean document rate from a random audit sample.

    `gold_doc_summary` is the per-document summary of the gold-regime
    evaluation; in production the sampled documents are the ones a human
    grades, and this frame is only known for those K documents."""
    population = len(gold_doc_summary)
    size = min(sample_size, population)
    rng = random.Random(seed)
    sample = gold_doc_summary.iloc[sorted(rng.sample(range(population), size))]
    clean = int(sample["clean"].sum())
    low, high = wilson_interval(clean, size)
    return AuditEstimate(
        sample_size=size,
        clean_in_sample=clean,
        estimate=clean / size if size else math.nan,
        ci_low=low,
        ci_high=high,
        sampled_doc_ids=tuple(sample["doc_id"]),
    )
