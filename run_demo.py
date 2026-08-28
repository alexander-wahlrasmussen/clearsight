"""End-to-end demo: generate synthetic data, evaluate it, open the report.

    python run_demo.py                 # 3000 docs, opens out/report.html
    python run_demo.py --docs 500 --no-open
"""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import pandas as pd

import evaluate
import generate_synthetic
import report
import validity
from readers import READERS, globex_xml

HERE = Path(__file__).parent


def run_validity(extracted, data_dir: Path) -> pd.DataFrame:
    """Record-level rules on every extracted record, plus the line-sum rule
    fed from the raw Globex XML (line items are not part of the canonical
    record -- see validity.py)."""
    tariff = validity.load_tariff_codes(data_dir / "tariff_codes.csv")
    by_id = {record.doc_id: record for record in extracted}

    rows = []
    for record in extracted:
        for result in validity.run_record_rules(record, tariff):
            rows.append(
                {
                    "doc_id": record.doc_id,
                    "country": record.country,
                    "source_system": record.source_system,
                    "rule": result.rule,
                    "passed": result.passed,
                    "reason": result.reason,
                }
            )
    lines_by_doc = globex_xml.read_invoice_lines(data_dir / "extracted_globex.xml")
    for doc_id, (amounts, total) in lines_by_doc.items():
        result = validity.invoice_lines_sum_to_total(amounts, total)
        record = by_id.get(doc_id)
        rows.append(
            {
                "doc_id": doc_id,
                "country": record.country if record else None,
                "source_system": globex_xml.SOURCE_SYSTEM,
                "rule": result.rule,
                "passed": result.passed,
                "reason": result.reason,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-dir", default=str(HERE / "data"))
    parser.add_argument("--out-dir", default=str(HERE / "out"))
    parser.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    args = parser.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)

    print("[1/5] Generating synthetic data")
    summary = generate_synthetic.generate(n_docs=args.docs, seed=args.seed, data_dir=data_dir)
    print(
        f"      {summary['n_acme']} Acme CSV + {summary['n_globex']} Globex XML documents; "
        f"{summary['n_amended_docs']} legitimately amended after filing"
    )

    print("[2/5] Reading source systems into canonical records")
    extracted = READERS["acme_extract"](data_dir / "extracted_acme.csv")
    extracted += READERS["globex_capture"](data_dir / "extracted_globex.xml")
    filed = READERS["customs_ledger"](data_dir / "filed_customs_ledger.csv")
    print(f"      {len(extracted)} extracted records, {len(filed)} filed records")

    print("[3/5] Running validity rules (no ground truth needed)")
    validity_df = run_validity(extracted, data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = validity_df[~validity_df["passed"]]
    failures.to_csv(out_dir / "validity_failures.csv", index=False)
    print(f"      {len(validity_df)} checks run, {len(failures)} failures")

    print("[4/5] Evaluating extracted vs filed")
    tiers = evaluate.load_field_tiers(HERE / "field_tiers.yaml")
    evaluation = evaluate.evaluate(extracted, filed, tiers)
    evaluate.write_outputs(evaluation, out_dir)
    print(
        f"      clean document rate: {evaluation.clean_document_rate * 100:.1f}% "
        f"({evaluation.n_joined} documents compared)"
    )

    print("[5/5] Building report")
    amendment_note = (
        f"This synthetic run amended {summary['n_amended_docs']} documents after filing "
        "(listed in data/amendments_truth.csv, which the harness never reads). "
        "Those appear above as extraction mismatches; the harness cannot tell them apart."
    )
    report_path = report.build_report(
        evaluation,
        validity_df,
        out_dir / "report.html",
        extra_notes=[amendment_note],
    )
    print(f"      {report_path}")

    if not args.no_open:
        try:
            webbrowser.open(report_path.resolve().as_uri())
        except Exception as error:  # headless environment etc.
            print(f"      (could not open a browser: {error})")


if __name__ == "__main__":
    main()
