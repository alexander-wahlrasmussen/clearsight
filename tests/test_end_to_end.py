"""One test that runs the whole pipeline on a small population.

The unit tests prove each layer in isolation; this proves the layers still
fit together -- generator -> readers -> validity -> both evaluations ->
statistics -> report.  If someone reshapes a reader or a frame column and
only runs pytest, this is the test that catches it.
"""

from pathlib import Path

import pytest

import confidence_quality
import evaluate
import generate_synthetic
import report
import uncertainty
from readers import READERS
from run_demo import run_validity

N_DOCS = 150
SEED = 7


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    summary = generate_synthetic.generate(n_docs=N_DOCS, seed=SEED, data_dir=data_dir)

    extracted = READERS["acme_extract"](data_dir / "extracted_acme.csv")
    extracted += READERS["globex_capture"](data_dir / "extracted_globex.xml")
    filed = READERS["customs_ledger"](data_dir / "filed_customs_ledger.csv")
    gold = READERS["customs_ledger"](data_dir / "gold_truth.csv")

    validity_df = run_validity(extracted, data_dir)
    tiers = evaluate.load_field_tiers(Path(__file__).parent.parent / "field_tiers.yaml")
    proxy_ev = evaluate.evaluate(extracted, filed, tiers)
    gold_ev = evaluate.evaluate(extracted, gold, tiers)
    return summary, extracted, filed, gold, validity_df, proxy_ev, gold_ev


def test_every_document_round_trips(pipeline):
    summary, extracted, filed, gold, _, proxy_ev, _ = pipeline
    assert summary["n_docs"] == N_DOCS
    assert len(extracted) == len(filed) == len(gold) == N_DOCS
    assert proxy_ev.n_joined == N_DOCS
    assert proxy_ev.unmatched_extracted == [] and proxy_ev.unmatched_filed == []


def test_rates_are_sane(pipeline):
    *_, proxy_ev, gold_ev = pipeline
    assert 0.2 < proxy_ev.clean_document_rate < 1.0
    assert 0.2 < gold_ev.clean_document_rate < 1.0
    # A perfect extraction of the true document can never look WORSE
    # against the truth than against an amended filing on average, but a
    # single small run can wobble -- so only pin the plausible band.
    assert abs(proxy_ev.clean_document_rate - gold_ev.clean_document_rate) < 0.2


def test_validity_runs_every_rule(pipeline):
    _, extracted, *_, validity_df, _, _ = pipeline
    n_items = sum(len(record.items) for record in extracted)
    # 5 header rules per document + 5 item rules per goods item.
    assert len(validity_df) == 5 * N_DOCS + 5 * n_items
    assert set(validity_df["passed"].unique()) == {True, False}


def test_comparisons_frame_supports_every_aggregation(pipeline):
    *_, validity_df, proxy_ev, gold_ev = pipeline
    for column in ("doc_id", "level", "field", "tier", "status", "reason",
                   "confidence", "country", "source_system"):
        assert column in proxy_ev.comparisons.columns
    assert set(proxy_ev.comparisons["level"].unique()) <= {"header", "item", "structure"}
    # the statistics layer accepts the frames it is given
    leading = evaluate.leading_indicators(proxy_ev.doc_summary, validity_df)
    assert len(leading.frame) == 5
    false_alarms, hidden = evaluate.proxy_disagreement(
        proxy_ev.comparisons, gold_ev.comparisons
    )
    assert len(false_alarms) >= 0 and len(hidden) >= 0
    summary = confidence_quality.summarise(gold_ev.comparisons, "vs gold")
    assert summary.n_scored > 0 and 0.0 <= summary.auroc <= 1.0


def test_report_builds_with_all_extras(pipeline, tmp_path):
    *_, validity_df, proxy_ev, gold_ev = pipeline
    false_alarms, hidden = evaluate.proxy_disagreement(
        proxy_ev.comparisons, gold_ev.comparisons
    )
    extras = report.ReportExtras(
        gold_evaluation=gold_ev,
        false_alarms=false_alarms,
        hidden_errors=hidden,
        audit_estimates=[uncertainty.audit_sample_estimate(gold_ev.doc_summary, 50)],
        leading=evaluate.leading_indicators(proxy_ev.doc_summary, validity_df),
        confidence_summaries=[confidence_quality.summarise(gold_ev.comparisons, "vs gold")],
        per_field_confidence=confidence_quality.per_field_quality(gold_ev.comparisons),
        clean_rate_ci=uncertainty.bootstrap_rate_ci(proxy_ev.doc_summary["clean"]),
        breakdown_ci=uncertainty.breakdown_clean_rate_cis(proxy_ev.doc_summary),
        extra_notes=["smoke test note"],
    )
    path = report.build_report(proxy_ev, validity_df, tmp_path / "report.html", extras)
    html = path.read_text(encoding="utf-8")
    assert "Three ways to score the same extraction" in html
    assert "Is the confidence score any good?" in html
    assert html.count('class="drilldown"') == html.count('class="drill"')


def test_report_builds_bare_without_extras(pipeline, tmp_path):
    *_, validity_df, proxy_ev, _ = pipeline
    path = report.build_report(proxy_ev, validity_df, tmp_path / "bare.html")
    assert "clean document rate" in path.read_text(encoding="utf-8")
