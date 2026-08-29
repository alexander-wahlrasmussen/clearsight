import math

import pandas as pd

from confidence_quality import (
    auroc,
    brier_score,
    expected_calibration_error,
    per_field_quality,
    scored_rows,
    summarise,
)


def frame(rows) -> pd.DataFrame:
    """rows: (confidence, status) or (confidence, status, level, field)."""
    padded = [r if len(r) == 4 else (*r, "item", "hs_code") for r in rows]
    return pd.DataFrame(padded, columns=["confidence", "status", "level", "field"])


class TestScoredRows:
    def test_drops_unscored_and_undecided_rows(self):
        comparisons = frame(
            [(0.9, "match"), (None, "match"), (0.8, "not_comparable")]
        )
        assert len(scored_rows(comparisons)) == 1


class TestBrierScore:
    def test_perfect_predictions_score_zero(self):
        assert brier_score(frame([(1.0, "match"), (0.0, "mismatch")])) == 0.0

    def test_known_single_value(self):
        assert abs(brier_score(frame([(0.8, "match")])) - 0.04) < 1e-12

    def test_empty_is_nan(self):
        assert math.isnan(brier_score(frame([])))


class TestExpectedCalibrationError:
    def test_perfectly_calibrated_bucket_scores_zero(self):
        rows = [(0.7, "match")] * 7 + [(0.7, "mismatch")] * 3
        assert expected_calibration_error(frame(rows)) < 1e-12

    def test_overconfidence_shows_up(self):
        rows = [(0.95, "match")] * 5 + [(0.95, "mismatch")] * 5  # claims 95%, is 50%
        assert abs(expected_calibration_error(frame(rows)) - 0.45) < 1e-12


class TestAuroc:
    def test_perfect_separation(self):
        rows = [(0.9, "match"), (0.8, "match"), (0.2, "mismatch"), (0.1, "mismatch")]
        assert auroc(frame(rows)) == 1.0

    def test_constant_score_is_a_coin_flip(self):
        rows = [(0.7, "match"), (0.7, "match"), (0.7, "mismatch")]
        assert auroc(frame(rows)) == 0.5

    def test_inverted_score_is_zero(self):
        rows = [(0.1, "match"), (0.9, "mismatch")]
        assert auroc(frame(rows)) == 0.0

    def test_degenerate_without_both_outcomes_is_nan(self):
        assert math.isnan(auroc(frame([(0.9, "match"), (0.8, "match")])))


class TestSummarise:
    def test_reports_the_regime_label_and_counts(self):
        summary = summarise(frame([(0.9, "match"), (0.4, "mismatch")]), "vs gold")
        assert summary.label == "vs gold"
        assert summary.n_scored == 2
        assert summary.accuracy == 0.5
        assert summary.auroc == 1.0

    def test_empty_frame_yields_nans_not_crashes(self):
        summary = summarise(frame([]), "vs gold")
        assert summary.n_scored == 0
        assert math.isnan(summary.ece) and math.isnan(summary.auroc)


class TestPerFieldQuality:
    def test_gap_is_signed_overconfidence(self):
        rows = [
            (0.9, "match", "item", "hs_code"),
            (0.9, "mismatch", "item", "hs_code"),      # claims 0.9, is 0.5 -> gap +0.4
            (0.6, "match", "header", "currency"),
            (0.6, "match", "header", "currency"),      # claims 0.6, is 1.0 -> gap -0.4
        ]
        result = per_field_quality(frame(rows)).set_index("field")
        assert abs(result.loc["hs_code", "calibration_gap"] - 0.4) < 1e-12
        assert abs(result.loc["currency", "calibration_gap"] + 0.4) < 1e-12
        # sorted most-overconfident first
        assert per_field_quality(frame(rows))["field"].iloc[0] == "hs_code"

    def test_degenerate_field_gets_nan_auroc(self):
        rows = [(0.9, "match", "item", "hs_code"), (0.8, "match", "item", "hs_code")]
        result = per_field_quality(frame(rows))
        assert math.isnan(result["auroc"].iloc[0])
