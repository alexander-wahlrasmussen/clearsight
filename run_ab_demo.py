"""A/B demo: two extraction models over the SAME documents, head to head.

    python run_ab_demo.py                 # 3000 docs, opens out/ab/comparison.html
    python run_ab_demo.py --docs 500 --no-open

The generator plays both models: an OCR-style model (noise-heavy, barely
fabricates) and an LLM-style model (reads through the noise, but makes
things up and invents lines) extract the same 3,000 documents, judged
against one shared filed ledger and one shared gold truth.  Because the
documents are shared, the comparison is PAIRED -- scored document by
document -- which is what makes the verdict decisive.

With real data: export each model's output over the same document batch,
read each file with its own label
(readers.acme_csv.read(path, source_system="my_model")), evaluate each
against the same filed records, and hand the evaluations to
compare_models.compare().
"""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import compare_models
import confidence_quality
import evaluate
import generate_synthetic
import report
from readers import acme_csv, customs_ledger

HERE = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-dir", default=str(HERE / "data" / "ab"))
    parser.add_argument("--out-dir", default=str(HERE / "out" / "ab"))
    parser.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    args = parser.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)

    print("[1/5] Generating A/B data: same documents, two models")
    summary = generate_synthetic.generate_ab(
        n_docs=args.docs, seed=args.seed, data_dir=data_dir
    )
    models = summary["models"]
    errors = ", ".join(f"{m}: {n}" for m, n in summary["errors_per_model"].items())
    print(f"      {summary['n_docs']} documents ({summary['n_items']} goods items); "
          f"injected errors -- {errors}")

    print("[2/5] Reading both models and the shared reference")
    extracted = {
        model: acme_csv.read(data_dir / f"extracted_{model}.csv", source_system=model)
        for model in models
    }
    filed = customs_ledger.read(data_dir / "filed_customs_ledger.csv")
    gold = customs_ledger.read(data_dir / "gold_truth.csv")

    print("[3/5] Evaluating each model against the same filed records (and gold)")
    tiers = evaluate.load_field_tiers(HERE / "field_tiers.yaml")
    vs_filed = {m: evaluate.evaluate(extracted[m], filed, tiers) for m in models}
    vs_gold = {m: evaluate.evaluate(extracted[m], gold, tiers) for m in models}
    for model in models:
        print(f"      {model}: clean rate {vs_filed[model].clean_document_rate * 100:.1f}% "
              f"vs filed, {vs_gold[model].clean_document_rate * 100:.1f}% vs gold")

    print("[4/5] Comparing head to head")
    confidence = {
        m: confidence_quality.summarise(vs_filed[m].comparisons, m) for m in models
    }
    comparison = compare_models.compare(
        vs_filed, "the filed records (the proxy)", confidence=confidence, seed=args.seed
    )
    gold_comparison = compare_models.compare(vs_gold, "the gold truth", seed=args.seed)
    compare_models.write_outputs(comparison, out_dir)
    for h in comparison.head_to_head:
        low, high = h.diff_ci
        print(
            f"      {h.model_a} vs {h.model_b}: difference "
            f"{h.clean_rate_diff * 100:+.1f} pp (very likely {low * 100:+.1f} to "
            f"{high * 100:+.1f}); wins {h.only_a_clean} / {h.only_b_clean}, "
            f"chance it's luck {h.chance_split_is_luck:.4f}"
        )
    for warning in comparison.warnings:
        print(f"      WARNING: {warning}")

    print("[5/5] Building comparison report")
    report_path = report.build_comparison_report(
        comparison, out_dir / "comparison.html", gold_comparison=gold_comparison
    )
    print(f"      {report_path}")

    if not args.no_open:
        try:
            webbrowser.open(report_path.resolve().as_uri())
        except Exception as error:  # headless environment etc.
            print(f"      (could not open a browser: {error})")


if __name__ == "__main__":
    main()
