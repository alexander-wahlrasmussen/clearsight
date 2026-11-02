"""The synthetic review scenario is illustrative, deterministic, and isolated."""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

import evaluate
from canonical import CanonicalItem, CanonicalRecord
from run_demo import SYNTHETIC_REVIEW_COLUMNS, simulate_reviewed_filings


def _record(doc_id: str) -> CanonicalRecord:
    return CanonicalRecord(
        shipment_id="SHP",
        doc_id=doc_id,
        country="DE",
        source_system="test",
        declared_value=1000.0,
        currency="EUR",
        incoterm="CIF",
        importer_eori="DE123456789012345",
        bl_reference="MSCU1234567",
        invoice_date="2026-03-04",
        items=[
            CanonicalItem(
                item_number=1,
                hs_code="8471300000",
                origin_country="CN",
                quantity=10.0,
                quantity_unit="NAR",
                item_value=1000.0,
                gross_weight=100.0,
                net_weight=90.0,
                package_count=2,
            )
        ],
        extraction_confidence={"currency": 0.99},
    )


@pytest.fixture
def review_inputs():
    tiers = evaluate.load_field_tiers(Path(__file__).parent.parent / "field_tiers.yaml")
    gold = [_record(doc_id) for doc_id in "abcdefgh"]
    extracted = [
        replace(gold[0], currency="USD"),
        replace(
            gold[1],
            incoterm="FOB",
            items=[replace(gold[1].items[0], origin_country="US")],
        ),
        replace(gold[2], currency=None, invoice_date="not a date"),
        replace(gold[3], currency="USD"),
        replace(gold[4], invoice_date="05/04/2026"),
        replace(gold[5], currency="USD"),
        replace(gold[6], declared_value=1100.0),
        replace(gold[7], declared_value=1100.0),
    ]
    filed = [
        gold[0], gold[1], gold[2],
        replace(gold[3], currency="GBP"),
        gold[4],
        replace(gold[5], currency="USD"),
        replace(gold[6], declared_value=None),
        replace(gold[7], declared_value=1001.0),
    ]
    gold_ev = evaluate.evaluate(extracted, gold, tiers)
    baseline_ev = evaluate.evaluate(extracted, filed, tiers)
    return tiers, extracted, filed, gold_ev, baseline_ev


def test_zero_acceptance_preserves_filings_and_comparisons(review_inputs):
    tiers, extracted, filed, gold_ev, baseline_ev = review_inputs
    before = [replace(record) for record in filed]

    simulated, decisions = simulate_reviewed_filings(filed, gold_ev.comparisons, tiers, 0, 42)

    assert filed == before
    assert simulated == filed
    assert set(decisions.loc[decisions["eligible"], "decision"]) == {"corrected_wrong_prefill"}
    assert set(decisions.loc[~decisions["eligible"], "doc_id"]) == {"d", "f", "g", "h"}
    assert set(decisions.loc[~decisions["eligible"], "decision"]) == {"not_simulated"}
    assert decisions.loc[~decisions["eligible"], "random_draw"].isna().all()
    assert "c" not in set(decisions["doc_id"])  # missing and unparseable are not mismatches
    assert "origin_country" not in set(decisions["field"])  # item errors are not simulated
    pd.testing.assert_frame_equal(
        evaluate.evaluate(extracted, simulated, tiers).comparisons,
        baseline_ev.comparisons,
    )


def test_full_acceptance_hides_wrong_values_but_not_gold_errors(review_inputs):
    tiers, extracted, filed, gold_ev, baseline_ev = review_inputs
    simulated, decisions = simulate_reviewed_filings(filed, gold_ev.comparisons, tiers, 1, 42)
    simulated_ev = evaluate.evaluate(extracted, simulated, tiers)
    _, baseline_hidden = evaluate.proxy_disagreement(baseline_ev.comparisons, gold_ev.comparisons)
    _, simulated_hidden = evaluate.proxy_disagreement(simulated_ev.comparisons, gold_ev.comparisons)

    accepted = decisions[decisions["decision"] == "accepted_wrong_prefill"]
    accepted_keys = set(zip(accepted["doc_id"], accepted["field"]))
    assert accepted_keys == {("a", "currency"), ("b", "incoterm"), ("e", "invoice_date")}
    baseline_keys = set(zip(baseline_hidden["doc_id"], baseline_hidden["field"]))
    simulated_keys = set(zip(simulated_hidden["doc_id"], simulated_hidden["field"]))
    assert baseline_keys == {("f", "currency")}
    assert simulated_keys == baseline_keys | accepted_keys

    simulated_by_id = {record.doc_id: record for record in simulated}
    for row in accepted.itertuples():
        assert getattr(simulated_by_id[row.doc_id], row.field) == row.extracted_value
        comparison = simulated_ev.comparisons[
            (simulated_ev.comparisons["doc_id"] == row.doc_id)
            & (simulated_ev.comparisons["level"] == "header")
            & (simulated_ev.comparisons["field"] == row.field)
        ].iloc[0]
        assert comparison["status"] == "match"
    assert simulated_by_id["d"].currency == "GBP"  # preserve an amended reference
    assert simulated_by_id["g"].declared_value is None
    assert simulated_by_id["h"].declared_value == 1001.0  # changed, but within tolerance
    assert decisions.loc[decisions["doc_id"] == "h", "baseline_reference_status"].iloc[0] == "match"
    assert simulated_by_id["b"].items[0].origin_country == "CN"
    assert extracted[0].currency == "USD"
    assert filed[0].currency == "EUR"


def test_fixed_seed_is_reproducible_and_acceptance_scenarios_are_nested(review_inputs):
    tiers, _, filed, gold_ev, _ = review_inputs
    low, low_decisions = simulate_reviewed_filings(filed, gold_ev.comparisons, tiers, 0.25, 7)
    high, high_decisions = simulate_reviewed_filings(filed, gold_ev.comparisons, tiers, 0.75, 7)
    repeated, repeated_decisions = simulate_reviewed_filings(
        list(reversed(filed)), gold_ev.comparisons.sample(frac=1, random_state=5), tiers, 0.25, 7
    )

    pd.testing.assert_frame_equal(low_decisions, repeated_decisions)
    pd.testing.assert_series_equal(low_decisions["random_draw"], high_decisions["random_draw"])
    assert {record.doc_id: record for record in low} == {
        record.doc_id: record for record in repeated
    }
    low_keys = set(zip(
        low_decisions.loc[low_decisions["decision"] == "accepted_wrong_prefill", "doc_id"],
        low_decisions.loc[low_decisions["decision"] == "accepted_wrong_prefill", "field"],
    ))
    high_keys = set(zip(
        high_decisions.loc[high_decisions["decision"] == "accepted_wrong_prefill", "doc_id"],
        high_decisions.loc[high_decisions["decision"] == "accepted_wrong_prefill", "field"],
    ))
    assert low_keys <= high_keys
    assert len(high) == len(low) == len(filed)


@pytest.mark.parametrize("probability", [-0.01, 1.01, float("nan"), float("inf"), -float("inf")])
def test_invalid_probabilities_are_rejected(review_inputs, probability):
    tiers, _, filed, gold_ev, _ = review_inputs
    with pytest.raises(ValueError, match="between 0 and 1"):
        simulate_reviewed_filings(filed, gold_ev.comparisons, tiers, probability, 42)


def test_invalid_or_empty_inputs_are_explicit(review_inputs):
    tiers, _, filed, gold_ev, _ = review_inputs
    with pytest.raises(ValueError, match="unique filed document IDs"):
        simulate_reviewed_filings(filed + [filed[0]], gold_ev.comparisons, tiers, 0.5, 42)
    with pytest.raises(ValueError, match="no filed record"):
        simulate_reviewed_filings(filed[1:], gold_ev.comparisons, tiers, 0.5, 42)
    duplicate = gold_ev.comparisons[
        (gold_ev.comparisons["doc_id"] == "a") & (gold_ev.comparisons["field"] == "currency")
    ]
    with pytest.raises(ValueError, match="one gold comparison"):
        simulate_reviewed_filings(
            filed, pd.concat([gold_ev.comparisons, duplicate]), tiers, 0.5, 42
        )

    simulated, decisions = simulate_reviewed_filings(
        filed, gold_ev.comparisons.iloc[0:0], tiers, 0.5, 42
    )
    assert simulated == filed
    assert decisions.empty
    assert list(decisions.columns) == SYNTHETIC_REVIEW_COLUMNS
