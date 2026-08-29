"""Error bars for the harness's metrics.  Stdlib only, on purpose.

Two tools, two lessons:

- The bootstrap: resample documents with replacement and recompute the
  rate.  The general-purpose tool -- it works for any statistic, not just
  proportions -- shown here in its plainest form so the mechanics are
  visible.  Used for the headline clean document rate.
- The Wilson score interval: the closed-form confidence interval for a
  proportion.  Instant, well-behaved at small n and near 0%/100%, so it is
  what we use for every per-slice rate and for the audit-sample estimate.

The audit-sample estimator is the production bridge for the gold regime:
you never have ground truth for the whole population, you have a sampled
human audit.  Draw K documents, grade only those against truth, and the
Wilson interval tells you what the sample is worth.  The CI width is the
price list for annotation: precision scales with sqrt(K).
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
    """Percentile bootstrap CI for the mean of a boolean flag, resampling
    documents with replacement.  Deterministic for a given seed."""
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
    """Wilson score interval for a proportion."""
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
