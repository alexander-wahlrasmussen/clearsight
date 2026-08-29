import math
from pathlib import Path

import pandas as pd

import evaluate
from canonical import CanonicalItem, CanonicalRecord
from evaluate import leading_indicators, proxy_disagreement


def doc_summary() -> pd.DataFrame:
    # d1: validity failure, high confidence, has a critical mismatch
    # d2: clean validity, LOW confidence, no mismatch
    # d3: clean validity, high confidence, has a mismatch (invisible ex ante)
    # d4: clean validity, MISSING confidence, no mismatch
    return pd.DataFrame(
        {
            "doc_id": ["d1", "d2", "d3", "d4"],
            "country": ["DE", "NL", "DE", "NL"],
            "source_system": ["acme_extract"] * 4,
            "min_critical_confidence": [0.9, 0.5, 0.95, math.nan],
            "critical_mismatches": [2, 0, 1, 0],
        }
    )


def validity_results() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "doc_id": ["d1", "d1", "d2", "d3", "d4"],
            "rule": ["eori_checksum", "hs_code_format", "x", "x", "x"],
            "passed": [False, True, True, True, True],
        }
    )


class TestLeadingIndicators:
    def test_rates_and_lift(self):
        result = leading_indicators(doc_summary(), validity_results(), confidence_floor=0.8)
        frame = result.frame.set_index("signal")
        base = frame.loc["all documents (base rate)"]
        assert base["documents"] == 4
        assert base["critical_mismatch_rate"] == 0.5

        validity_row = frame.loc["any validity failure"]
        assert validity_row["documents"] == 1
        assert validity_row["critical_mismatch_rate"] == 1.0
        assert validity_row["lift_vs_base"] == 2.0

        # low or missing confidence: d2 and d4, neither has an error
        confidence_row = frame.loc["min tier-1 confidence < 0.8 or missing"]
        assert confidence_row["documents"] == 2
        assert confidence_row["critical_mismatch_rate"] == 0.0

    def test_residual_row_is_the_invisible_error(self):
        result = leading_indicators(doc_summary(), validity_results(), confidence_floor=0.8)
        frame = result.frame.set_index("signal")
        residual = frame.loc["neither signal (looks clean ex ante)"]
        assert residual["documents"] == 1  # d3 only
        assert residual["critical_mismatch_rate"] == 1.0

    def test_masks_align_with_the_frame(self):
        result = leading_indicators(doc_summary(), validity_results(), confidence_floor=0.8)
        for _, row in result.frame.iterrows():
            assert int(result.masks[row["signal"]].sum()) == row["documents"]


def _record(doc_id: str) -> CanonicalRecord:
    item = CanonicalItem(
        item_number=1, hs_code="8471300000", origin_country="CN",
        quantity=10.0, quantity_unit="NAR", item_value=1000.0,
        gross_weight=100.0, net_weight=90.0, package_count=2,
        extraction_confidence={},
    )
    return CanonicalRecord(
        shipment_id="SHP", doc_id=doc_id, country="DE", source_system="test",
        declared_value=1000.0, currency="EUR", incoterm="CIF",
        importer_eori="DE123456789012345", bl_reference="MSCU1234567",
        invoice_date="2026-05-04", items=[item],
        extraction_confidence={}, timestamp=None,
    )


class TestEvaluateWithNoJoinedDocuments:
    def test_reports_the_unmatched_lists_instead_of_crashing(self):
        # Wrong file pairing / empty batch: nothing shares a doc_id.
        tiers = evaluate.load_field_tiers(Path(__file__).parent.parent / "field_tiers.yaml")
        result = evaluate.evaluate([_record("DOC-A")], [_record("DOC-B")], tiers)
        assert result.n_joined == 0
        assert result.unmatched_extracted == ["DOC-A"]
        assert result.unmatched_filed == ["DOC-B"]
        assert math.isnan(result.clean_document_rate)
        assert result.comparisons.empty
        assert result.doc_summary.empty
        assert result.per_field.empty and result.breakdown.empty
        assert len(result.straight_through) > 0  # thresholds still listed, all NaN


def comparison_rows(rows) -> pd.DataFrame:
    columns = ["doc_id", "level", "field", "item_no_extracted", "status",
               "extracted_value", "filed_value", "reason", "tier"]
    return pd.DataFrame(rows, columns=columns)


class TestProxyDisagreement:
    def test_splits_verdicts_including_header_rows_with_no_item_number(self):
        proxy = comparison_rows([
            # header row (item_no None): proxy says mismatch, gold says match
            ("d1", "header", "currency", None, "mismatch", "USD", "EUR", "differs", 1),
            # item row: proxy says match, gold says mismatch -> hidden error
            ("d1", "item", "hs_code", 1, "match", "84713000", "84713000", "equal", 1),
            # both regimes agree it is wrong -> in neither list
            ("d1", "item", "quantity", 1, "mismatch", "10", "12", "differs", 2),
        ])
        gold = comparison_rows([
            ("d1", "header", "currency", None, "match", "USD", "USD", "equal", 1),
            ("d1", "item", "hs_code", 1, "mismatch", "84713000", "84714100", "differs", 1),
            ("d1", "item", "quantity", 1, "mismatch", "10", "12", "differs", 2),
        ])
        false_alarms, hidden = proxy_disagreement(proxy, gold)
        assert list(false_alarms["field"]) == ["currency"]  # the NaN-key join worked
        assert list(hidden["field"]) == ["hs_code"]
        assert list(hidden["gold_value"]) == ["84714100"]

    def test_structure_rows_are_excluded(self):
        proxy = comparison_rows([
            ("d1", "structure", "missed_item", None, "mismatch", None, "item 2", "missed", 1),
        ])
        gold = comparison_rows([
            ("d1", "structure", "missed_item", None, "match", None, "item 2", "ok", 1),
        ])
        false_alarms, hidden = proxy_disagreement(proxy, gold)
        assert false_alarms.empty and hidden.empty
