"""End-to-end demo: generate synthetic data, evaluate it in every regime,
open the report.

    python run_demo.py                 # 3000 docs, opens out/report.html
    python run_demo.py --docs 500 --no-open

The evaluation runs three times over, which is the point of the demo:
  - vs the FILED records (the proxy: what production actually has),
  - vs the GOLD truth (only possible because the data is synthetic;
    in production this is a human-graded audit sample),
  - and BLIND (validity rules + confidence, no reference at all),
so the report can show what each regime sees and what each one misses.
An additional synthetic-only sensitivity demonstration copies some known
wrong header values into a hypothetical filing.  It illustrates reference
contamination, not observed reviewer behaviour or workflow time savings.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import webbrowser
from dataclasses import replace
from pathlib import Path

import pandas as pd

import comparators
import confidence_quality
import evaluate
import generate_synthetic
import report
import uncertainty
import validity
from canonical import CanonicalRecord
from readers import READERS

HERE = Path(__file__).parent

AUDIT_SAMPLE_SIZES = [50, 150, 500]

# This version describes the synthetic assumptions, not a comparator or
# aligner.  Keep it with every decision when the simulation changes.
SYNTHETIC_REVIEW_VERSION = "1.0"
DEFAULT_SYNTHETIC_REVIEW_ACCEPTANCE = 0.5
SYNTHETIC_REVIEW_COLUMNS = [
    "data_kind", "doc_id", "field", "tier", "extracted_value", "gold_value",
    "baseline_filed_value", "confidence", "comparator", "comparator_version",
    "gold_reason", "baseline_reference_status", "baseline_reference_reason",
    "eligible", "acceptance_probability", "random_draw", "decision",
    "decision_reason", "simulation_seed", "simulation_version",
]

# Which validity rule guards which canonical field -- used ONLY by the
# synthetic-only diagnostic below, to check per error style whether any
# blind signal pointed at the injected error.
RULE_FIELDS = {
    "hs_code_format": {"hs_code"},
    "hs_code_in_tariff": {"hs_code"},
    "origin_country_is_iso": {"origin_country"},
    "quantity_unit_expected": {"quantity_unit", "hs_code"},
    "net_weight_not_exceeding_gross": {"net_weight", "gross_weight"},
    "eori_checksum": {"importer_eori"},
    "currency_is_iso": {"currency"},
    "country_is_iso": {"country"},
    "invoice_date_plausible": {"invoice_date"},
    "item_values_sum_to_invoice_total": {"item_value", "declared_value", "(whole item)"},
}


def run_validity(extracted, data_dir: Path) -> pd.DataFrame:
    """Header rules per record and item rules per goods item, on the
    extracted side only -- no ground truth involved."""
    tariff = validity.load_tariff_codes(data_dir / "tariff_codes.csv")
    rows = []
    for record in extracted:
        for result in validity.run_record_rules(record, tariff):
            rows.append(
                {
                    "doc_id": record.doc_id,
                    "item_number": result.item_number,
                    "country": record.country,
                    "source_system": record.source_system,
                    "rule": result.rule,
                    "passed": result.passed,
                    "reason": result.reason,
                }
            )
    return pd.DataFrame(rows)


def blind_catch_rates(errors: pd.DataFrame, validity_df: pd.DataFrame) -> dict[str, float]:
    """SYNTHETIC-ONLY diagnostic: per error style, the share of injected
    errors that at least one validity failure actually pointed at (same
    document, a rule guarding that field, and -- for item-level rules --
    the same goods item).  The harness proper never reads the truth file;
    this is the demo checking the blind regime's reach against it."""
    failures_by_doc: dict[str, list] = {}
    for row in validity_df[~validity_df["passed"]].itertuples():
        failures_by_doc.setdefault(row.doc_id, []).append((row.rule, row.item_number))

    def flagged(error) -> bool:
        for rule, failure_item in failures_by_doc.get(error.doc_id, ()):
            if error.field not in RULE_FIELDS.get(rule, ()):
                continue
            if pd.isna(failure_item):  # header/document-level rule
                return True
            if not pd.isna(error.item_number) and int(failure_item) == int(error.item_number):
                return True
        return False

    rates = {}
    for style, group in errors.groupby("style"):
        rates[style] = sum(flagged(error) for error in group.itertuples()) / len(group)
    return rates


def simulate_reviewed_filings(
    filed: list[CanonicalRecord],
    gold_comparisons: pd.DataFrame,
    tiers: evaluate.TiersConfig,
    acceptance_probability: float,
    seed: int,
) -> tuple[list[CanonicalRecord], pd.DataFrame]:
    """SYNTHETIC-ONLY sensitivity demonstration, not a model of real people.

    For comparable wrong header values, the baseline filing is copied and
    some extracted values are hypothetically accepted.  Fields whose filed
    value differs from synthetic truth are left alone, even if a configured
    tolerance would consider them a match.  This preserves possible
    amendments.  Missing values and item/structural errors are not simulated.
    The fixed, separate random stream keeps the source documents unchanged and
    makes acceptance probabilities comparable at one seed.
    """
    if not math.isfinite(acceptance_probability) or not 0 <= acceptance_probability <= 1:
        raise ValueError("synthetic review acceptance probability must be between 0 and 1")

    baseline_by_id = {record.doc_id: record for record in filed}
    if len(baseline_by_id) != len(filed):
        raise ValueError("synthetic review requires unique filed document IDs")
    simulated_by_id = baseline_by_id.copy()
    candidates = gold_comparisons[
        (gold_comparisons["level"] == evaluate.HEADER)
        & (gold_comparisons["status"] == comparators.MISMATCH)
    ].sort_values(["doc_id", "field"])
    if candidates.duplicated(["doc_id", "field"]).any():
        raise ValueError("synthetic review requires one gold comparison per header field")

    rng = random.Random(f"{seed}/synthetic-review/{SYNTHETIC_REVIEW_VERSION}")
    decisions = []
    for row in candidates.itertuples(index=False):
        baseline = baseline_by_id.get(row.doc_id)
        if baseline is None:
            raise ValueError(f"no filed record for synthetic gold comparison {row.doc_id}")
        baseline_value = getattr(baseline, row.field)
        reference_result = tiers.header_fields[row.field].comparator.compare(
            baseline_value, row.filed_value
        )
        eligible = (
            reference_result.status == comparators.MATCH and baseline_value == row.filed_value
        )
        if not eligible:
            draw = None
            decision = "not_simulated"
            reason = (
                "Baseline filing differs from synthetic truth or is not comparable; "
                "left unchanged even if the comparator tolerates the difference."
            )
        else:
            draw = rng.random()
            if draw < acceptance_probability:
                decision = "accepted_wrong_prefill"
                reason = "Hypothetically accepted the wrong pre-filled value."
                simulated_by_id[row.doc_id] = replace(
                    simulated_by_id[row.doc_id], **{row.field: row.extracted_value}
                )
            else:
                decision = "corrected_wrong_prefill"
                reason = "Hypothetically corrected the wrong pre-filled value."
        decisions.append(
            {
                "data_kind": "synthetic",
                "doc_id": row.doc_id,
                "field": row.field,
                "tier": row.tier,
                "extracted_value": row.extracted_value,
                "gold_value": row.filed_value,
                "baseline_filed_value": baseline_value,
                "confidence": row.confidence,
                "comparator": row.comparator,
                "comparator_version": row.comparator_version,
                "gold_reason": row.reason,
                "baseline_reference_status": reference_result.status,
                "baseline_reference_reason": reference_result.reason,
                "eligible": eligible,
                "acceptance_probability": acceptance_probability,
                "random_draw": draw,
                "decision": decision,
                "decision_reason": reason,
                "simulation_seed": seed,
                "simulation_version": SYNTHETIC_REVIEW_VERSION,
            }
        )
    return (
        [simulated_by_id[record.doc_id] for record in filed],
        pd.DataFrame(decisions, columns=SYNTHETIC_REVIEW_COLUMNS),
    )


def _probability_arg(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1") from error
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-dir", default=str(HERE / "data"))
    parser.add_argument("--out-dir", default=str(HERE / "out"))
    parser.add_argument(
        "--synthetic-review-acceptance",
        type=_probability_arg,
        default=DEFAULT_SYNTHETIC_REVIEW_ACCEPTANCE,
        metavar="PROBABILITY",
        help=(
            "illustrative probability of accepting an eligible wrong header value "
            "(0 to 1; default: %(default)s); not measured human behaviour"
        ),
    )
    parser.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    args = parser.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)

    print("[1/8] Generating synthetic data")
    summary = generate_synthetic.generate(n_docs=args.docs, seed=args.seed, data_dir=data_dir)
    print(
        f"      {summary['n_acme']} Acme CSV + {summary['n_globex']} Globex XML documents "
        f"({summary['n_items']} goods items); "
        f"{summary['n_amended_docs']} legitimately amended after filing"
    )
    print(
        f"      injected errors: {summary['n_noise_errors']} noise, "
        f"{summary['n_fabrications']} fabrications, {summary['n_structural_errors']} structural"
    )

    print("[2/8] Reading source systems into canonical records")
    extracted = READERS["acme_extract"](data_dir / "extracted_acme.csv")
    extracted += READERS["globex_capture"](data_dir / "extracted_globex.xml")
    filed = READERS["customs_ledger"](data_dir / "filed_customs_ledger.csv")
    gold = READERS["customs_ledger"](data_dir / "gold_truth.csv")
    n_items = sum(len(r.items) for r in extracted)
    print(
        f"      {len(extracted)} extracted records ({n_items} items), "
        f"{len(filed)} filed, {len(gold)} gold"
    )

    print("[3/8] Running validity rules (the blind regime)")
    validity_df = run_validity(extracted, data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = validity_df[~validity_df["passed"]]
    failures.to_csv(out_dir / "validity_failures.csv", index=False)
    print(f"      {len(validity_df)} checks run, {len(failures)} failures")

    print("[4/8] Evaluating vs filed (proxy) and vs gold (truth)")
    tiers = evaluate.load_field_tiers(HERE / "field_tiers.yaml")
    proxy_ev = evaluate.evaluate(extracted, filed, tiers)
    gold_ev = evaluate.evaluate(extracted, gold, tiers)
    evaluate.write_outputs(proxy_ev, out_dir)
    evaluate.write_outputs(gold_ev, out_dir / "gold")
    print(
        f"      clean document rate: {gold_ev.clean_document_rate * 100:.1f}% vs gold, "
        f"{proxy_ev.clean_document_rate * 100:.1f}% vs filed "
        f"({proxy_ev.n_joined} documents)"
    )
    align = proxy_ev.alignment_summary
    print(
        f"      item alignment: {int(align['paired_items'].sum())} paired, "
        f"{int(align['missed_items'].sum())} missed, "
        f"{int(align['spurious_items'].sum())} spurious"
    )

    print("[5/8] Statistics: error bars, audit samples, blind signals, confidence quality")
    clean_ci = uncertainty.bootstrap_rate_ci(proxy_ev.doc_summary["clean"], seed=args.seed)
    gold_clean_ci = uncertainty.bootstrap_rate_ci(gold_ev.doc_summary["clean"], seed=args.seed)
    breakdown_ci = uncertainty.breakdown_clean_rate_cis(proxy_ev.doc_summary)
    audits = [
        # seed varies per size so the three draws are independent samples,
        # not nested cuts of one draw
        uncertainty.audit_sample_estimate(gold_ev.doc_summary, size, seed=args.seed + size)
        for size in AUDIT_SAMPLE_SIZES
    ]
    leading = evaluate.leading_indicators(proxy_ev.doc_summary, validity_df)
    false_alarms, hidden_errors = evaluate.proxy_disagreement(
        proxy_ev.comparisons, gold_ev.comparisons
    )
    summaries = [
        confidence_quality.summarise(gold_ev.comparisons, "vs gold (truth)"),
        confidence_quality.summarise(proxy_ev.comparisons, "vs filed (proxy)"),
    ]
    per_field_conf = confidence_quality.per_field_quality(gold_ev.comparisons)

    leading.frame.to_csv(out_dir / "leading_indicators.csv", index=False)
    pd.DataFrame([s.__dict__ for s in summaries]).to_csv(
        out_dir / "confidence_summary.csv", index=False
    )
    per_field_conf.to_csv(out_dir / "confidence_per_field.csv", index=False)
    pd.DataFrame(
        [
            {
                "sample_size": a.sample_size,
                "clean_in_sample": a.clean_in_sample,
                "estimate": a.estimate,
                "ci_low": a.ci_low,
                "ci_high": a.ci_high,
            }
            for a in audits
        ]
    ).to_csv(out_dir / "audit_estimates.csv", index=False)

    truth_gap = (proxy_ev.clean_document_rate - gold_ev.clean_document_rate) * 100
    print(
        f"      proxy bias on clean rate: {truth_gap:+.1f} pp; "
        f"false alarms: {len(false_alarms)}, hidden errors: {len(hidden_errors)}"
    )
    gold_quality = summaries[0]
    print(
        f"      confidence vs truth: ranks a right field above a wrong one "
        f"{gold_quality.auroc * 100:.1f}% of the time (AUROC); "
        f"honesty gap {gold_quality.ece:.3f} (ECE)"
    )

    print("[6/8] Synthetic-only diagnostic: hypothetical acceptance of wrong pre-fill")
    simulated_filed, review_decisions = simulate_reviewed_filings(
        filed, gold_ev.comparisons, tiers, args.synthetic_review_acceptance, args.seed
    )
    simulated_ev = evaluate.evaluate(extracted, simulated_filed, tiers)
    _, simulated_hidden = evaluate.proxy_disagreement(
        simulated_ev.comparisons, gold_ev.comparisons
    )
    review_dir = out_dir / "synthetic_review"
    evaluate.write_outputs(simulated_ev, review_dir)
    review_decisions.to_csv(review_dir / "decisions.csv", index=False)
    (review_dir / "metadata.json").write_text(
        json.dumps(
            {
                "data_kind": "synthetic",
                "purpose": "sensitivity demonstration, not observed reviewer behaviour",
                "simulation_version": SYNTHETIC_REVIEW_VERSION,
                "seed": args.seed,
                "acceptance_probability": args.synthetic_review_acceptance,
                "scope": "comparable wrong header values with unchanged baseline filing",
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    hidden_errors.to_csv(review_dir / "baseline_hidden_errors.csv", index=False)
    simulated_hidden.to_csv(review_dir / "simulated_hidden_errors.csv", index=False)
    baseline_hidden_headers = int((hidden_errors["level"] == evaluate.HEADER).sum())
    simulated_hidden_headers = int((simulated_hidden["level"] == evaluate.HEADER).sum())
    print(
        f"      assumed acceptance probability: {args.synthetic_review_acceptance:.2f}; "
        f"hidden header errors: {baseline_hidden_headers} -> {simulated_hidden_headers}"
    )
    print(
        f"      clean vs filed: {proxy_ev.clean_document_rate * 100:.1f}% -> "
        f"{simulated_ev.clean_document_rate * 100:.1f}%; "
        "extraction itself is unchanged"
    )

    print("[7/8] Synthetic-only diagnostic: which error styles do blind signals catch?")
    injected = pd.read_csv(data_dir / "injected_errors_truth.csv")
    catch = blind_catch_rates(injected, validity_df)
    catch_line = ", ".join(
        f"{style}: {rate * 100:.0f}%" for style, rate in sorted(catch.items())
    )
    print(f"      share of injected errors any validity rule pointed at -- {catch_line}")
    coverage = [
        f"{style}: {catch[style] * 100:.0f}%"
        if style in catch else f"{style}: not measured (no injected errors)"
        for style in ("noise", "structural", "fabrication")
    ]
    reconciliation_note = (
        "Synthetic-only diagnostic (uses generator truth the harness never reads). "
        "Share of injected errors pointed at by a validity rule: "
        + "; ".join(coverage)
        + ". Plausible-but-wrong values may defeat blind monitoring. "
        "A filing comparison finds them only when the filing differs; "
        "an independent audit can reveal errors shared by both."
    )
    amendment_note = (
        f"This synthetic run amended {summary['n_amended_docs']} documents after filing "
        "(listed in data/amendments_truth.csv, which the harness never reads). "
        "Those can appear above as extraction mismatches; the harness cannot tell them apart."
    )

    print("[8/8] Building report")
    extras = report.ReportExtras(
        gold_evaluation=gold_ev,
        false_alarms=false_alarms,
        hidden_errors=hidden_errors,
        audit_estimates=audits,
        leading=leading,
        confidence_summaries=summaries,
        per_field_confidence=per_field_conf,
        clean_rate_ci=clean_ci,
        gold_clean_rate_ci=gold_clean_ci,
        breakdown_ci=breakdown_ci,
        extra_notes=[amendment_note, reconciliation_note],
        synthetic_review=report.SyntheticReviewScenario(
            acceptance_probability=args.synthetic_review_acceptance,
            simulation_version=SYNTHETIC_REVIEW_VERSION,
            evaluation=simulated_ev,
            hidden_errors=simulated_hidden,
        ),
    )
    report_path = report.build_report(proxy_ev, validity_df, out_dir / "report.html", extras)
    print(f"      {report_path}")

    if not args.no_open:
        try:
            webbrowser.open(report_path.resolve().as_uri())
        except Exception as error:  # headless environment etc.
            print(f"      (could not open a browser: {error})")


if __name__ == "__main__":
    main()
