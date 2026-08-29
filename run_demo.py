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
"""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import pandas as pd

import confidence_quality
import evaluate
import generate_synthetic
import report
import uncertainty
import validity
from readers import READERS

HERE = Path(__file__).parent

AUDIT_SAMPLE_SIZES = [50, 150, 500]

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-dir", default=str(HERE / "data"))
    parser.add_argument("--out-dir", default=str(HERE / "out"))
    parser.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    args = parser.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)

    print("[1/7] Generating synthetic data")
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

    print("[2/7] Reading source systems into canonical records")
    extracted = READERS["acme_extract"](data_dir / "extracted_acme.csv")
    extracted += READERS["globex_capture"](data_dir / "extracted_globex.xml")
    filed = READERS["customs_ledger"](data_dir / "filed_customs_ledger.csv")
    gold = READERS["customs_ledger"](data_dir / "gold_truth.csv")
    n_items = sum(len(r.items) for r in extracted)
    print(
        f"      {len(extracted)} extracted records ({n_items} items), "
        f"{len(filed)} filed, {len(gold)} gold"
    )

    print("[3/7] Running validity rules (the blind regime)")
    validity_df = run_validity(extracted, data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = validity_df[~validity_df["passed"]]
    failures.to_csv(out_dir / "validity_failures.csv", index=False)
    print(f"      {len(validity_df)} checks run, {len(failures)} failures")

    print("[4/7] Evaluating vs filed (proxy) and vs gold (truth)")
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

    print("[5/7] Statistics: error bars, audit samples, blind signals, confidence quality")
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

    print("[6/7] Synthetic-only diagnostic: which error styles do blind signals catch?")
    injected = pd.read_csv(data_dir / "injected_errors_truth.csv")
    catch = blind_catch_rates(injected, validity_df)
    catch_line = ", ".join(
        f"{style}: {rate * 100:.0f}%" for style, rate in sorted(catch.items())
    )
    print(f"      share of injected errors any validity rule pointed at -- {catch_line}")
    reconciliation_note = (
        "Synthetic-only diagnostic (uses generator truth the harness never reads): "
        f"validity rules pointed at {catch.get('noise', 0) * 100:.0f}% of injected "
        f"OCR-style noise errors and {catch.get('structural', 0) * 100:.0f}% of "
        f"structural errors, but only {catch.get('fabrication', 0) * 100:.0f}% of "
        "fabricated plausible-but-wrong values (a valid EORI, an in-tariff HS "
        "sibling, a real-but-wrong origin). Fluent garbage defeats blind "
        "monitoring; only the proxy or an audited gold sample finds it."
    )
    amendment_note = (
        f"This synthetic run amended {summary['n_amended_docs']} documents after filing "
        "(listed in data/amendments_truth.csv, which the harness never reads). "
        "Those appear above as extraction mismatches; the harness cannot tell them apart."
    )

    print("[7/7] Building report")
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
