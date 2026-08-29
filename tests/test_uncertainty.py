import math

import pandas as pd
import pytest

from uncertainty import (
    audit_sample_estimate,
    bootstrap_rate_ci,
    breakdown_clean_rate_cis,
    chance_split_is_luck,
    paired_bootstrap_diff_ci,
    wilson_interval,
)


class TestWilsonInterval:
    def test_known_value_half_and_half(self):
        low, high = wilson_interval(50, 100)
        assert abs(low - 0.4038) < 0.005
        assert abs(high - 0.5962) < 0.005

    def test_zero_successes_starts_at_zero_but_not_wide_open(self):
        low, high = wilson_interval(0, 10)
        assert low == 0.0
        assert 0.0 < high < 0.35  # "0 out of 10" does not prove 0%

    def test_all_successes_ends_at_one(self):
        low, high = wilson_interval(10, 10)
        assert high == 1.0
        assert 0.65 < low < 1.0

    def test_larger_sample_narrower_interval(self):
        narrow = wilson_interval(500, 1000)
        wide = wilson_interval(50, 100)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])

    def test_empty_is_nan(self):
        low, high = wilson_interval(0, 0)
        assert math.isnan(low) and math.isnan(high)


class TestBootstrapRateCi:
    FLAGS = [True] * 60 + [False] * 40

    def test_interval_brackets_the_sample_rate(self):
        low, high = bootstrap_rate_ci(self.FLAGS, seed=3)
        assert low < 0.6 < high
        assert 0.4 < low and high < 0.8

    def test_deterministic_for_a_seed(self):
        assert bootstrap_rate_ci(self.FLAGS, seed=3) == bootstrap_rate_ci(self.FLAGS, seed=3)
        assert bootstrap_rate_ci(self.FLAGS, seed=3) != bootstrap_rate_ci(self.FLAGS, seed=4)

    def test_degenerate_all_true(self):
        assert bootstrap_rate_ci([True] * 50, seed=1) == (1.0, 1.0)

    def test_empty_is_nan(self):
        low, high = bootstrap_rate_ci([], seed=1)
        assert math.isnan(low) and math.isnan(high)


class TestPairedBootstrapDiffCi:
    def test_identical_models_differ_by_exactly_zero(self):
        flags = [True] * 70 + [False] * 30
        assert paired_bootstrap_diff_ci(flags, flags, seed=2) == (0.0, 0.0)

    def test_interval_brackets_a_real_difference(self):
        # A is clean on 30 documents where B is not; otherwise identical.
        a = [True] * 80 + [False] * 20
        b = [True] * 50 + [False] * 50
        low, high = paired_bootstrap_diff_ci(a, b, seed=2)
        assert low <= 0.30 <= high
        assert low > 0.0  # the winner is clear, zero is outside the range

    def test_deterministic_for_a_seed(self):
        a = [True, False] * 50
        b = [True] * 60 + [False] * 40
        assert paired_bootstrap_diff_ci(a, b, seed=3) == paired_bootstrap_diff_ci(a, b, seed=3)

    def test_mismatched_lengths_are_an_error_not_a_guess(self):
        with pytest.raises(ValueError):
            paired_bootstrap_diff_ci([True, False], [True], seed=1)

    def test_empty_is_nan(self):
        low, high = paired_bootstrap_diff_ci([], [], seed=1)
        assert math.isnan(low) and math.isnan(high)


class TestChanceSplitIsLuck:
    def test_no_disagreements_prove_nothing(self):
        assert chance_split_is_luck(0, 0) == 1.0

    def test_known_binomial_values(self):
        # 8 wins vs 2: two-sided exact = 2 * (C(10,0)+C(10,1)+C(10,2)) / 2^10
        assert abs(chance_split_is_luck(8, 2) - 0.109375) < 1e-9
        # a clean sweep of 10
        assert abs(chance_split_is_luck(10, 0) - 2 / 1024) < 1e-9

    def test_symmetric_in_the_models(self):
        assert chance_split_is_luck(8, 2) == chance_split_is_luck(2, 8)

    def test_even_split_is_pure_luck(self):
        assert chance_split_is_luck(5, 5) == 1.0


def gold_summary(n=100, n_clean=70) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "doc_id": [f"DOC-{i:04d}" for i in range(n)],
            "country": ["DE"] * (n // 2) + ["NL"] * (n - n // 2),
            "source_system": ["acme_extract"] * n,
            "clean": [True] * n_clean + [False] * (n - n_clean),
        }
    )


class TestAuditSampleEstimate:
    def test_estimate_matches_its_own_sample(self):
        estimate = audit_sample_estimate(gold_summary(), sample_size=30, seed=5)
        assert estimate.sample_size == 30
        assert estimate.estimate == estimate.clean_in_sample / 30
        assert estimate.ci_low <= estimate.estimate <= estimate.ci_high

    def test_deterministic_and_ids_come_from_population(self):
        first = audit_sample_estimate(gold_summary(), sample_size=30, seed=5)
        second = audit_sample_estimate(gold_summary(), sample_size=30, seed=5)
        assert first == second
        assert set(first.sampled_doc_ids) <= set(gold_summary()["doc_id"])
        assert len(set(first.sampled_doc_ids)) == 30  # without replacement

    def test_sample_capped_at_population(self):
        estimate = audit_sample_estimate(gold_summary(), sample_size=1000, seed=5)
        assert estimate.sample_size == 100
        assert estimate.estimate == 0.7  # the whole population, exactly

    def test_bigger_sample_tighter_interval(self):
        small = audit_sample_estimate(gold_summary(1000, 700), sample_size=50, seed=5)
        large = audit_sample_estimate(gold_summary(1000, 700), sample_size=500, seed=5)
        assert large.ci_width < small.ci_width


class TestBreakdownCis:
    def test_one_row_per_slice_with_ordered_bounds(self):
        frame = breakdown_clean_rate_cis(gold_summary())
        assert len(frame) == 2  # DE and NL, one source system
        assert (frame["clean_ci_low"] <= frame["clean_ci_high"]).all()
        assert set(frame["country"]) == {"DE", "NL"}
