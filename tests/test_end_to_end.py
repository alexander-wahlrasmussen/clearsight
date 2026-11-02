"""One test that runs the whole pipeline on a small population.

The unit tests prove each layer in isolation; this proves the layers still
fit together -- generator -> readers -> validity -> both evaluations ->
statistics -> report.  If someone reshapes a reader or a frame column and
only runs pytest, this is the test that catches it.
"""

import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

import comparators
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
    assert 'class="story-section"' in html
    assert "ONE DOCUMENT" in html
    assert "AFTER FILING: A POLICY TO VALIDATE" in html
    assert "The takeaway" in html
    assert "What does the person reviewing this declaration need to be warned about?" in html
    assert "Agreement is not correctness" in html
    assert "Today a person reviews every declaration" in html
    assert "no mismatch found vs filing (not verified)" in html
    assert "Missing fields are caught; missing lines are not." in html
    assert html.index('class="story-whatif"') > html.index("Future what-if: confident documents")
    assert "Three ways to score the same extraction" in html
    assert "Is the confidence score any good?" in html
    assert html.count('class="drilldown"') == html.count('class="drill"')


def test_report_builds_bare_without_extras(pipeline, tmp_path):
    *_, validity_df, proxy_ev, _ = pipeline
    path = report.build_report(proxy_ev, validity_df, tmp_path / "bare.html")
    html = path.read_text(encoding="utf-8")
    assert "clean document rate" in html
    assert "Synthetic demonstration: when review copies an error" not in html


@pytest.mark.parametrize(
    ("alteration", "message"),
    [
        ("drop_document", "same documents"),
        ("change_header", "same extracted headers"),
        ("change_comparator", "header comparator settings"),
    ],
)
def test_synthetic_report_refuses_unfair_comparisons(pipeline, tmp_path, alteration, message):
    _, extracted, filed, _, validity_df, proxy_ev, gold_ev = pipeline
    modified = list(extracted)
    if alteration == "drop_document":
        modified.pop()
    elif alteration == "change_header":
        modified[0] = replace(modified[0], currency="ZZZ")
    tiers = evaluate.load_field_tiers(Path(__file__).parent.parent / "field_tiers.yaml")
    if alteration == "change_comparator":
        header_fields = dict(tiers.header_fields)
        header_fields["declared_value"] = replace(
            header_fields["declared_value"],
            comparator=comparators.NumericWithTolerance(pct=2),
        )
        tiers = replace(tiers, header_fields=header_fields)
    different_ev = evaluate.evaluate(modified, filed, tiers)
    _, hidden = evaluate.proxy_disagreement(proxy_ev.comparisons, gold_ev.comparisons)
    extras = report.ReportExtras(
        gold_evaluation=gold_ev,
        hidden_errors=hidden,
        synthetic_review=report.SyntheticReviewScenario(0.5, "1.0", different_ev, hidden),
    )
    with pytest.raises(ValueError, match=message):
        report.build_report(proxy_ev, validity_df, tmp_path / "unfair.html", extras)


def test_demo_writes_traceable_synthetic_review_report(tmp_path):
    root = Path(__file__).parent.parent
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable, str(root / "run_demo.py"), "--docs", "100", "--seed", "42",
            "--data-dir", str(data_dir), "--out-dir", str(out_dir),
            "--synthetic-review-acceptance", "1", "--no-open",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Synthetic-only diagnostic: hypothetical acceptance" in result.stdout
    review_dir = out_dir / "synthetic_review"
    decisions = pd.read_csv(review_dir / "decisions.csv")
    metadata = json.loads((review_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "data_kind": "synthetic",
        "purpose": "sensitivity demonstration, not observed reviewer behaviour",
        "simulation_version": "1.0",
        "seed": 42,
        "acceptance_probability": 1.0,
        "scope": "comparable wrong header values with unchanged baseline filing",
    }
    assert set(decisions["data_kind"]) == {"synthetic"}
    simulated_hidden = pd.read_csv(review_dir / "simulated_hidden_errors.csv")
    baseline_hidden = pd.read_csv(review_dir / "baseline_hidden_errors.csv")
    simulated_rows = pd.read_csv(review_dir / "field_comparisons.csv")
    gold_rows = pd.read_csv(out_dir / "gold" / "field_comparisons.csv")
    accepted = decisions[decisions["decision"] == "accepted_wrong_prefill"]
    assert len(accepted) > 0
    assert set(zip(accepted["doc_id"], accepted["field"])) <= set(
        zip(simulated_hidden["doc_id"], simulated_hidden["field"])
    )
    for row in accepted.itertuples():
        simulated = simulated_rows[
            (simulated_rows["doc_id"] == row.doc_id)
            & (simulated_rows["level"] == "header")
            & (simulated_rows["field"] == row.field)
        ].iloc[0]
        gold = gold_rows[
            (gold_rows["doc_id"] == row.doc_id)
            & (gold_rows["level"] == "header")
            & (gold_rows["field"] == row.field)
        ].iloc[0]
        assert simulated["status"] == "match"
        assert gold["status"] == "mismatch"

    html = (out_dir / "report.html").read_text(encoding="utf-8")
    start = html.index("<section><h2>Synthetic demonstration: when review copies an error")
    section = html[start:html.index("</section>", start)]
    labels = re.findall(r'<a class="drill" href="#drill-\d+">([^<]+)</a>', section)
    expected_rates = []
    for path in [
        out_dir / "doc_summary.csv",
        review_dir / "doc_summary.csv",
        out_dir / "gold" / "doc_summary.csv",
    ]:
        expected_rates.append(f"{pd.read_csv(path)['clean'].mean() * 100:.1f}%")
    expected_hidden = [
        str(int((baseline_hidden["level"] == "header").sum())),
        str(int((simulated_hidden["level"] == "header").sum())),
    ]
    assert labels == expected_rates + expected_hidden
    story_start = html.index('<section class="story-section">')
    story = html[story_start:html.index("</section>", story_start)]
    agreement = story[story.index("Agreement is not correctness"):story.index("AFTER FILING")]
    story_labels = re.findall(r'<a class="drill" href="#drill-\d+">([^<]+)</a>', agreement)
    assert story_labels == expected_rates + expected_hidden
    assert "not an observation of reviewers" in section
    assert "not a recommendation to remove current human review" in html
    assert "can only be lower" not in html
    assert html.count('class="drilldown"') == html.count('class="drill"')


def test_demo_records_assumptions_when_there_are_no_review_decisions(tmp_path):
    root = Path(__file__).parent.parent
    out_dir = tmp_path / "out"
    subprocess.run(
        [
            sys.executable, str(root / "run_demo.py"), "--docs", "1", "--seed", "42",
            "--data-dir", str(tmp_path / "data"), "--out-dir", str(out_dir),
            "--synthetic-review-acceptance", "0.2", "--no-open",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    review_dir = out_dir / "synthetic_review"
    assert pd.read_csv(review_dir / "decisions.csv").empty
    metadata = json.loads((review_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["seed"] == 42
    assert metadata["acceptance_probability"] == 0.2
    assert metadata["data_kind"] == "synthetic"
    assert "fabrication: not measured (no injected errors)" in (
        out_dir / "report.html"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("probability", ["-0.1", "1.1", "nan", "inf", "not-a-number"])
def test_demo_rejects_invalid_review_probabilities_before_writing(tmp_path, probability):
    root = Path(__file__).parent.parent
    data_dir = tmp_path / "data"
    result = subprocess.run(
        [
            sys.executable, str(root / "run_demo.py"), "--docs", "5",
            "--data-dir", str(data_dir), "--out-dir", str(tmp_path / "out"),
            "--synthetic-review-acceptance", probability, "--no-open",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "must be a number between 0 and 1" in result.stderr
    assert not data_dir.exists()
