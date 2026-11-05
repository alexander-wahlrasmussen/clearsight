# Code architecture

[Back to the project overview](../README.md)

Understand [one comparison](walkthrough.md) before reading the module
map. The implementation is replaceable teaching code, not a proposed
production architecture. Runtime evaluation uses pandas, PyYAML and the
standard library, with no ML libraries.

## Follow the evidence

```text
source exports -> readers -> canonical records
                                  |
                      join documents, pair goods items
                                  |
                       compare fields and structure
                                  |
                     comparison rows -> summaries -> HTML
```

| Module | Responsibility |
|---|---|
| `generate_synthetic.py` | Generate source exports and separate truth sidecars. |
| `readers/` | Map each source format to `CanonicalRecord` and `CanonicalItem`. |
| `canonical.py` | Define the header-plus-goods-items shape used downstream. |
| `alignment.py` | Pair goods items and record the score and written basis. |
| `comparators.py` | Return match, mismatch or not-comparable with reasons and versions. |
| `field_tiers.yaml` | Configure field criticality, comparator parameters, structural severity and alignment score floor. |
| `validity.py` | Check extracted records without reference answers. |
| `evaluate.py` | Join, align, compare and aggregate; support cross-reference analyses. |
| `uncertainty.py` | Bootstrap and Wilson ranges, simple random-audit estimation and paired statistics. |
| `confidence_quality.py` | Measure confidence calibration, probability error and ranking. |
| `compare_models.py` | Compare model evaluations on shared documents and report fairness warnings. |
| `reviewer_events.py` | Validate a proposed pilot event contract and summarize review activity. |
| `report.py` | Build self-contained single-model and comparison reports with drill-downs. |
| `run_demo.py` | Orchestrate the demo, including explicitly synthetic-only diagnostics. |
| `run_ab_demo.py` | Compare two generated model profiles on the same documents. |

## Invariants when changing the code

- **Keep evidence readable.** Comparison outcomes retain values, verdict,
  reason, rule version, and item-pairing context. Derive comparison
  metrics from those rows and give report numbers drill-downs.
- **Version measurement changes.** Bump the relevant comparator `VERSION`
  or `ALIGNER_VERSION` when changing its logic. Otherwise a metric shift
  cannot be attributed to data versus evaluation rules.
- **Keep policy in configuration.** Field criticality, tolerances,
  structural-defect severity and the alignment floor belong in
  `field_tiers.yaml`, not duplicated Python constants.
- **Do not turn missing into success.** Comparators return
  `NOT_COMPARABLE` for missing or unparseable values. Reuse
  `comparators.parse_number` and `comparators.parse_date`.
- **Keep truth-derived diagnostics separate.** Readers, evaluation,
  validity and report logic must not secretly read truth sidecars.
  Demo orchestration explicitly loads the gold reference and constructs
  labelled synthetic diagnostics; the evaluation machinery receives
  records without needing to know their provenance.
- **Use plain meaning first.** Explain "sorting power (AUROC)", not an
  unexplained acronym.

The generator's `gold_truth.csv`, `amendments_truth.csv` and
`injected_errors_truth.csv` are answer keys for the demonstration, not
inputs available to an ordinary production evaluation.

## Add a source system

Add one module in `readers/` mapping its export to canonical records and
register it in `readers/__init__.py`. Downstream layers stay unchanged.
The existing Acme flat CSV and Globex nested XML readers demonstrate
different formats using the same evaluator.

Readers should preserve date text for the comparator to interpret.
Canonical mapping makes records comparable; it does not prove a mapping
faithfully represents a real source system.

## Compare real model exports

Run both extractors on the same document batch and evaluate them against
the same reference with the same rules:

```python
from readers import acme_csv, customs_ledger
import compare_models
import evaluate

tiers = evaluate.load_field_tiers("field_tiers.yaml")
filed = customs_ledger.read("filed_export.csv")
evaluations = {
    "model_a": evaluate.evaluate(
        acme_csv.read("a.csv", source_system="model_a"), filed, tiers
    ),
    "model_b": evaluate.evaluate(
        acme_csv.read("b.csv", source_system="model_b"), filed, tiers
    ),
}
comparison = compare_models.compare(evaluations, "the filed records")
```

This example measures filing agreement. Use an independently checked
reference to assess correctness, and inspect `comparison.warnings` for
document-set or comparator-version differences. See
[methodology](methodology.md#compare-models-on-the-same-documents).

## Development commands

```bash
pip install -r requirements.txt
python run_demo.py --no-open
python run_ab_demo.py --no-open
pytest
ruff check .
```

Python 3.10+ is required. The requirements include pandas, PyYAML and
pytest; Ruff is a separate development tool. Unit tests cover comparison
and alignment examples, and `tests/test_end_to_end.py` exercises the
generator-to-report path and checks report drill-downs.
