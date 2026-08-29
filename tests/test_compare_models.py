"""The A/B machinery, proven on a small generated A/B population."""

from pathlib import Path

import pytest

import compare_models
import evaluate
import generate_synthetic
import report
from readers import acme_csv, customs_ledger

N_DOCS = 150
SEED = 11
MODELS = ("ocr_model", "llm_model")


@pytest.fixture(scope="module")
def ab(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("ab_data")
    generate_synthetic.generate_ab(n_docs=N_DOCS, seed=SEED, data_dir=data_dir)
    extracted = {
        model: acme_csv.read(data_dir / f"extracted_{model}.csv", source_system=model)
        for model in MODELS
    }
    filed = customs_ledger.read(data_dir / "filed_customs_ledger.csv")
    tiers = evaluate.load_field_tiers(Path(__file__).parent.parent / "field_tiers.yaml")
    evaluations = {m: evaluate.evaluate(extracted[m], filed, tiers) for m in MODELS}
    comparison = compare_models.compare(evaluations, "the filed records", seed=SEED)
    return extracted, filed, tiers, evaluations, comparison


class TestGenerateAb:
    def test_same_documents_for_every_model(self, ab):
        extracted, *_ = ab
        doc_sets = [
            {record.doc_id for record in records} for records in extracted.values()
        ]
        assert all(docs == doc_sets[0] for docs in doc_sets)
        assert len(doc_sets[0]) == N_DOCS

    def test_records_carry_their_model_label(self, ab):
        extracted, *_ = ab
        for model, records in extracted.items():
            assert {record.source_system for record in records} == {model}


class TestCompare:
    def test_summary_has_one_row_per_model_best_first(self, ab):
        *_, comparison = ab
        assert list(comparison.summary["model"].sort_values()) == sorted(MODELS)
        rates = list(comparison.summary["clean_document_rate"])
        assert rates == sorted(rates, reverse=True)

    def test_head_to_head_accounting_adds_up(self, ab):
        *_, comparison = ab
        (h,) = comparison.head_to_head
        assert h.documents == N_DOCS
        assert h.both_clean + h.only_a_clean + h.only_b_clean + h.neither_clean == N_DOCS
        assert h.only_a_clean == len(h.only_a_doc_ids)
        assert set(h.only_a_doc_ids).isdisjoint(h.only_b_doc_ids)
        # the rate difference is exactly the win difference over n
        assert abs(h.clean_rate_diff - (h.only_a_clean - h.only_b_clean) / N_DOCS) < 1e-12
        assert h.diff_ci[0] <= h.clean_rate_diff <= h.diff_ci[1]
        assert 0.0 <= h.chance_split_is_luck <= 1.0

    def test_per_field_has_a_rate_column_per_model(self, ab):
        *_, comparison = ab
        for model in MODELS:
            assert model in comparison.per_field.columns
        assert (comparison.per_field["spread"].dropna() >= 0).all()

    def test_fair_comparison_has_no_warnings(self, ab):
        *_, comparison = ab
        assert comparison.warnings == []

    def test_different_document_sets_are_flagged(self, ab):
        extracted, filed, tiers, evaluations, _ = ab
        partial = evaluate.evaluate(extracted["ocr_model"][:-10], filed, tiers)
        lopsided = compare_models.compare(
            {"ocr_model": partial, "llm_model": evaluations["llm_model"]},
            "the filed records",
        )
        assert any("same documents" in warning for warning in lopsided.warnings)
        # still computed, on the overlap
        assert lopsided.head_to_head[0].documents == N_DOCS - 10

    def test_one_model_is_not_a_comparison(self, ab):
        *_, evaluations, _ = ab
        with pytest.raises(ValueError):
            compare_models.compare(
                {"only": evaluations["ocr_model"]}, "the filed records"
            )


class TestOutputsAndReport:
    def test_csvs_written(self, ab, tmp_path):
        *_, comparison = ab
        paths = compare_models.write_outputs(comparison, tmp_path)
        assert set(paths) == {
            "comparison_summary.csv", "head_to_head.csv", "per_field_comparison.csv"
        }
        for path in paths.values():
            assert Path(path).stat().st_size > 0

    def test_comparison_report_builds(self, ab, tmp_path):
        *_, comparison = ab
        path = report.build_comparison_report(comparison, tmp_path / "comparison.html")
        html = path.read_text(encoding="utf-8")
        for model in MODELS:
            assert model in html
        assert "Head to head" in html
        assert html.count('class="drilldown"') == html.count('class="drill"')
