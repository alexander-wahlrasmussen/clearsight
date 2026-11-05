# Outputs and glossary

[Back to the project overview](../README.md)

## Generated files

`data/` and `out/` are generated and gitignored. The primary deliverable
is `out/report.html`. Comparison results retain values, verdicts,
reasons, comparison-rule versions and item-pairing evidence; report
drill-downs lead back to the contributing rows.

| File | Contents |
|---|---|
| `out/report.html` | Self-contained single-model report. |
| `out/field_comparisons.csv` | Header, aligned-item and unmatched-item comparison rows against filings. |
| `out/gold/*.csv` | Evaluation frames against generated truth. |
| `out/doc_summary.csv` | Document mismatch/item counts, minimum tier-1 confidence and clean flag. |
| `out/per_field.csv` | Per-field precision and recall. |
| `out/breakdown.csv` | Mismatches by country and source system. |
| `out/calibration.csv` | Observed agreement by confidence band. |
| `out/straight_through.csv` | Hypothetical no-review share and escaped mismatches by threshold. |
| `out/alignment_summary.csv` | Paired, missed and spurious items by source system. |
| `out/validity_failures.csv` | Failed validity checks and reasons. |
| `out/leading_indicators.csv` | Blind signals versus eventual important filing mismatches, with lift. |
| `out/confidence_summary.csv` | Confidence calibration, probability error and ranking by reference. |
| `out/confidence_per_field.csv` | Per-field confidence quality against gold. |
| `out/audit_estimates.csv` | Simple random-audit clean-rate estimates and Wilson intervals. |
| `out/synthetic_review/decisions.csv` | Assumed review decisions, probability, seed, draw and reason. |
| `out/synthetic_review/metadata.json` | Scenario assumptions and version, even with no eligible fields. |
| `out/synthetic_review/field_comparisons.csv` | Extraction versus hypothetical filing. |
| `out/synthetic_review/*hidden_errors.csv` | Baseline and hypothetical filing-agreement errors identified using gold. |
| `out/ab/comparison.html` | Two-model comparison report from `run_ab_demo.py`. |
| `out/ab/comparison_summary.csv` | Model scorecards. |
| `out/ab/head_to_head.csv` | Paired win/loss counts, difference range and exact-test p-value. |
| `out/ab/per_field_comparison.csv` | Per-field mismatch rates by model, sorted by gap. |

The generator also writes `data/gold_truth.csv`,
`data/amendments_truth.csv` and `data/injected_errors_truth.csv`.
These truth sidecars are reserved for human inspection and explicitly
labelled demo evaluation/diagnostics, not implicit inputs to the
evaluation machinery.

## Plain-English glossary

These terms appear in the report, code and CSV columns. Consult
[methodology](methodology.md) for assumptions and interpretation.

| Term | Plain meaning |
|---|---|
| Gold / truth | An answer key representing what the source said. Generated here; independently human-adjudicated for real work. |
| Proxy | A stand-in answer key: here, the filed record, which may be amended or wrong. |
| Blind | Checks without reference answers: validity rules and confidence signals. A blinded human audit is a separate concept. |
| Regime | Which evaluation view a number uses: gold, proxy or blind. |
| Tier | Importance configured in `field_tiers.yaml`: tier 1 corrupts the filing, tier 2 causes rework, tier 3 is cosmetic. |
| Clean document rate | Share of joined documents with no tier-1 mismatch against the reference. Unresolved comparisons do not count as mismatches. |
| Not comparable | Missing or unparseable values prevented a comparison; not a match. |
| Precision | Among rows with values on both sides, the share that match. |
| Recall | Among reference values, the share for which extraction supplied a matching value. |
| Calibration / honesty | Whether stated confidence agrees with observed outcomes. |
| ECE / honesty gap | Average gap between confidence and observed agreement in score bands; lower is better. |
| Brier score / error score | Probability-prediction error, penalizing confident mistakes heavily; lower is better. |
| AUROC / sorting power | How often a randomly chosen correct field scores above an incorrect one, with ties receiving half credit. |
| Confidence interval | A sampling-uncertainty range from a procedure designed for a stated long-run coverage rate. It does not account for every source of error. |
| Bootstrap | Estimate sampling variation by repeatedly resampling documents. |
| Wilson interval | An interval method for simple proportions, including small samples. |
| Lift | Error frequency among flagged documents divided by the overall frequency. |
| False alarm | Extraction matches truth but not the filing; an amendment is one possible cause. |
| Hidden error | Extraction and filing agree but are wrong against truth. |
| Amendment | A legitimate later change to a filing. |
| Noise | Broken-looking extraction: confused digits, truncated codes, missing fields. |
| Fabrication | Plausible but wrong values; deliberately high-confidence and validity-passing in this demo. |
| Structural error | Whole goods lines dropped, merged or invented. |
| Alignment | Choosing which extracted and reference goods lines probably correspond. |
| Missed / spurious item | A reference item without an extracted partner / an extracted item without a reference partner. |
| Straight-through | A hypothetical policy accepting documents without human review above a confidence threshold. |
| Escaped error | A document that policy accepts despite an important mismatch against the reference; not necessarily an independently confirmed error. |
| Paired comparison | Comparing model outcomes document by document on the same batch. |
| "Chance it's luck" | Report wording for the exact sign-test / McNemar p-value: frequency of at least this lopsided a win/loss split under the equal-performance null, not the probability the conclusion is wrong. |
