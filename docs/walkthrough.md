# One error, one comparison row, one decision

[Back to the project overview](../README.md)

This walkthrough is for a first team session, about 20 minutes. Start with
the measurement problem, not the module diagram or the full dashboard.
No machine-learning background is required.

## 1. Start with the shared-error example

A document says EUR 1,000, extraction says EUR 100, and a reviewer files
EUR 100 unchanged. Extraction and filing agree perfectly, but both are
wrong. If the reviewer corrects the filing, agreement falls while the
outcome improves.

Ask what a comparison with the filing can tell us, and what it cannot.
An independent auditor would need to read the source without seeing the
extraction, filing or experimental group.

## 2. Open the demonstration

After installing the requirements from the [README](../README.md), run:

```bash
python run_demo.py --docs 150 --seed 7 --no-open
```

Open `out/report.html`. Everything in this run is generated, not evidence
about real reviewers or production accuracy. Each run replaces output in
its chosen directories; use different `--data-dir` and `--out-dir` values
to preserve multiple runs.

Start with the opening panels:

- **One document:** `DOC-000005` has one extracted goods line but two
  filed lines. Its lowest confidence on an important field is `0.751`.
  The omitted line has no confidence score, so confidence alone does not
  point a reviewer to the gap.
- **Agreement is not correctness:** "Synthetic demonstration: when review
  copies an error" keeps extraction fixed while hypothetical filings
  accept wrong pre-filled header values with probability 0.5. Click a
  number to inspect the hidden errors. The probability is a teaching
  assumption, not an estimate of human behaviour.

The three post-filing lanes are a proposal, not an implemented workflow:
investigate differences, keep unresolved cases visible, and independently
audit some apparently agreeing cases. Human review remains in place.
Skip the future no-review what-if and detailed statistics in a first
session.

## 3. Trace the missing line

For this seed, follow `DOC-000005` through the files:

| Step | Evidence | What it establishes |
|---|---|---|
| Read the inputs | `data/extracted_acme.csv` has item 1, HS `8528721000`, value `20891.70`. `data/filed_customs_ledger.csv` also has item 2, HS `8523512000`, value `33286.52`. | The two records differ in their goods lines. |
| Inspect the pairing | Filter `out/field_comparisons.csv` by `doc_id`. Read `alignment_basis` for the accepted pair and the `structure` / `missed_item` row. | The aligner paired one line and left the other filed line unmatched. Pairing is a hypothesis, not proof of identity. |
| Inspect a field comparison | The paired item values `20891.70` and `20891.10` match under the configured tolerance in `field_tiers.yaml`. | "Match" means the comparison rule accepted the values, not that they are identical or independently certified. |
| Inspect the rollup | `out/doc_summary.csv` records one pair, one missed item, one critical mismatch and `clean=False`. Click the corresponding report number. | The structural mismatch explains the document outcome. |
| Check the synthetic answer key | `data/gold_truth.csv` contains both goods items. | This generated example really did drop a line. The filing alone could not prove that interpretation. |

The report displays at most 100 rows per drill-down; the CSV holds the
complete evidence. Truth sidecars are for the demo's labelled diagnostics
and human inspection, not hidden inputs to comparison logic.

For a code walkthrough, trace `readers/` -> `canonical.py` ->
`alignment.py` -> `comparators.py` -> `evaluate.py` -> `report.py`.
See [architecture](architecture.md) after the example makes sense.

## 4. Defend the conclusion

Have a second person reproduce the evidence chain and a customs expert
review the assumptions:

- Which lines were paired? Which row records the missing line? Could a
  bad pairing explain the difference instead?
- Why is this document not clean? What would confidence have warned us
  about, and what is outside its view?
- In the future what-if, a `0.70` confidence threshold lets this document
  through; `0.80` holds it for review. Verify that in
  `out/doc_summary.csv` and `out/straight_through.csv`. Neither threshold
  is a validated operating policy.
- Which conclusion depends on the generated answer key, rather than the
  filing?

Finish by choosing one real workflow to observe, defining an important
filing error with a domain expert, and identifying who could independently
check the source. Do not borrow quality thresholds from the demo.

As a check on understanding: fewer edits alongside more audited errors
does not establish a better workflow. Lower confidence alongside stable
audit quality is a reason to investigate, not proof quality worsened.
The [reviewer-pilot guide](reviewer-pilot.md) explains the evidence needed
to test usefulness.
