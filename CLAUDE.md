# ClearSight — extraction quality measurement harness

Prototype that measures how accurate automated customs-document extraction
is, by comparing extracted records against the downstream filing system
(and, for synthetic data, against the generated truth).  Read README.md
first — it explains the design decisions and has a plain-English glossary.

## Commands

```bash
pip install -r requirements.txt
python run_demo.py --no-open      # generate data/ -> evaluate -> out/report.html
python run_ab_demo.py --no-open   # two models, same docs -> out/ab/comparison.html
pytest                            # unit + end-to-end smoke tests
ruff check .                      # lint (no config; defaults)
```

Python 3.10+.  Dependencies: pandas, PyYAML, pytest only — no ML
libraries, by design.

## Architecture (strict layers)

```
generate_synthetic.py  fake source files + truth sidecars (data/)
readers/               one module per source system -> CanonicalRecord
canonical.py           header + goods items; the only shape downstream sees
alignment.py           pairs extracted goods items with filed ones (versioned)
comparators.py         field comparators (versioned) -> match/mismatch/not_comparable
field_tiers.yaml       ALL criticality + comparator config; none in Python
validity.py            rules needing no reference data ("blind" checks)
evaluate.py            join, align, compare -> one row-level frame + aggregations
uncertainty.py         bootstrap / Wilson ranges, audit-sample estimator,
                       paired A/B statistics (diff range, luck probability)
confidence_quality.py  ECE / Brier / AUROC on the confidence scores
compare_models.py      paired A/B over the same documents; fairness-checked
report.py              single self-contained HTML; every number drills to rows
                       (build_report + build_comparison_report)
run_demo.py            orchestrates everything, incl. synthetic-only diagnostics
run_ab_demo.py         two model profiles over the same documents, head to head
```

## Rules that keep the design honest

- **Traceability**: every metric must be a plain aggregation of the
  row-level `comparisons` frame (one row per compared thing, with a
  human-readable reason).  If you add a metric, derive it from that frame
  and give its report number a drill-down.
- **Versioning**: changing comparison or alignment logic changes metrics,
  so bump that comparator's `VERSION` (or `ALIGNER_VERSION`) in the same
  commit.  Rows record the version, letting metric shifts be attributed.
- **Config over code**: field criticality, tolerances, structural-defect
  severity and the alignment score floor live in `field_tiers.yaml` only.
- **Truth files are quarantined**: `data/gold_truth.csv`,
  `data/amendments_truth.csv` and `data/injected_errors_truth.csv` are
  written by the generator and may be read ONLY by run_demo's
  clearly-labelled synthetic-only diagnostics and by humans — never by
  readers, evaluate, validity or report logic.
- **Missing is never a match**: comparators return NOT_COMPARABLE for
  missing/unparseable values; parsing has one opinion per type
  (`comparators.parse_number`, `comparators.parse_date`) — reuse them.
- **Adding a source system** = one new module in readers/ mapping to
  `CanonicalRecord` + a registry entry in `readers/__init__.py`.  Nothing
  else changes.
- **Language**: prose in the report and README leads with the plain
  meaning and keeps the technical term in parentheses ("sorting power
  (AUROC)").  Keep that style; the audience includes non-specialists.

## Outputs

`data/` and `out/` are generated and gitignored.  `out/report.html` is the
deliverable; every CSV next to it backs one report section (gold-regime
copies under `out/gold/`).
