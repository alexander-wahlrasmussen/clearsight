# ClearSight — extraction quality measurement harness

A prototype that measures how accurate an automated document extraction
tool is, by comparing what the tool extracted against what was actually
filed in a downstream customs system.  The downstream system is treated as
a black box we can only read from.

Plain Python, pandas and the standard library.  No ML libraries.  Every
metric is traceable to rows a human can open and read.

## Quick start

```bash
pip install -r requirements.txt
python run_demo.py        # generates data, evaluates, opens out/report.html
pytest                    # tests for the comparators and validity rules
```

`run_demo.py` writes synthetic source files to `data/`, row-level results
and the HTML report to `out/`.  Both directories are gitignored.

## What the layers are, and why they are separate

```
canonical.py          one dataclass every source system is mapped into
readers/              one module per source system: read(path) -> [CanonicalRecord]
comparators.py        versioned field comparators: match / mismatch / not_comparable
field_tiers.yaml      which field is how critical, judged by which comparator
validity.py           deterministic rules that need no ground truth
evaluate.py           join extracted vs filed, apply comparators, aggregate
report.py             one self-contained HTML file, every number clickable
generate_synthetic.py paired records with deliberately injected error patterns
run_demo.py           end to end
```

The separation is the point:

- **Adding a source system is one file.**  A reader maps its system's
  export (flat CSV, nested XML, whatever) into `CanonicalRecord` and gets
  registered in `readers/__init__.py`.  Comparators, validity, evaluation
  and reporting do not change.  The demo ships two deliberately different
  stub readers (Acme's flat CSV, Globex's nested XML) to prove it.
- **Criticality is configuration, not code.**  `field_tiers.yaml` maps
  each field to a tier (1 = an error corrupts the filing, 2 = rework,
  3 = cosmetic) and to a comparator with parameters.  Moving a field
  between tiers or loosening a tolerance is an edit and a re-run.
- **Comparators are versioned.**  Every comparison row records the
  comparator name, parameters and version.  When a metric moves between
  two runs, you can tell whether the data changed or the rules did.

## Why clean document rate is the headline

The headline metric is **clean_document_rate**: the share of documents
with *zero* tier-1 mismatches.  Not field-level accuracy, deliberately:

- A declaration is filed as a unit.  One wrong tier-1 field (HS code,
  value, currency, origin, EORI) and a human has to touch the document —
  it does not matter that the other eleven fields were right.
- Field-level accuracy flatters the tool.  At 99% per-field accuracy over
  twelve independent fields, roughly one document in nine still has an
  error somewhere.  "99% accurate" and "11% of documents need rework"
  are the same number wearing different clothes; the second one is the
  one your operations team lives with.
- Cost scales with documents touched, not fields corrected.  Once a
  document is open in front of a person, fixing one field or three is
  nearly the same effort.

Per-field precision and recall are still reported — they tell you *what*
to fix — they are just not the headline.

Definitions used (kept deliberately simple):

- **precision** — of the rows where the tool extracted a value and the
  filing has one, the share that match: "when the tool writes a value,
  how often is it right?"
- **recall** — of the rows where the filing has a value, the share where
  the tool produced a matching one.  A value the tool failed to extract
  hurts recall, not precision.

## Confidence: calibration and straight-through

The report buckets per-field extraction confidence into deciles and shows
observed accuracy per bucket.  If confidence means anything, accuracy
climbs with the bucket; if the row for 0.9–1.0 is not visibly better than
0.6–0.7, the score is decoration and must not gate anything.

The straight-through table answers the operational question directly: *if
we auto-accepted every document whose tier-1 fields all carry confidence ≥
t*, what share of the workload goes through untouched, and how many bad
documents ride along?  A document missing any tier-1 confidence score can
never auto-accept.

## Validity rules

`validity.py` holds deterministic checks that need no ground truth at all:
HS code format and presence in a supplied tariff list, ISO country and
currency codes, an EORI checksum, dates in a plausible window, invoice
lines summing to the total, net weight not exceeding gross.  These matter
because they still work when the filed record is wrong too.

One structural note: the line-sum rule needs invoice line items, which the
canonical record intentionally does not carry (only the total).  The rule
takes the line amounts directly, and `run_demo.py` feeds it from the raw
Globex XML via `readers/globex_xml.read_invoice_lines()`.  Readers whose
format has no line items simply never trigger it.

## Where this harness is knowingly wrong

Read these before quoting any number from the report.

1. **Amendments look like extraction errors.**  Documents get legitimately
   amended after filing — revaluations, corrected counts, broker
   reclassifications.  The harness compares extraction against the *final*
   filed record, so every amendment shows up as a mismatch.  Nothing in
   the data lets us tell the two apart.  All mismatch counts and the
   straight-through escape rate are therefore **upper bounds** on true
   extraction error.  The synthetic generator injects such amendments on
   purpose and writes them to `data/amendments_truth.csv` — which the
   harness never reads — so you can check how much of the measured error
   the ambiguity accounts for in the demo.  In production you would
   approximate this with filing-system audit history, if the black box
   ever exposes it.
2. **The filed record is not ground truth.**  When extraction and filing
   agree, both can be wrong (the human who filed it copied the same
   misread value, or waved the extraction through).  Agreement measures
   consistency, not correctness.  The validity rules catch some of these
   cases; most go unmeasured.
3. **Ambiguous dates are resolved by preference.**  "04/05/2026" with both
   parts ≤ 12 is resolved by the `dayfirst` parameter in
   `field_tiers.yaml`, which can itself be wrong.  When a mismatch would
   disappear with day and month swapped, the comparator's reason says so
   explicitly instead of hiding it in a generic "dates differ".
4. **The EORI checksum is invented.**  Real EORI numbers have national
   formats and no universal check digit; real validation is a registry
   lookup.  The rule demonstrates the shape a checksum rule takes (and the
   synthetic data honours it); swap its body for a registry client in
   production.
5. **Reference lists are subsets.**  The currency list covers common codes
   only; the tariff list is whatever file you supply.  A valid-but-unlisted
   code fails validity — a false alarm a human can waive, which we prefer
   to a silent pass.
6. **The join trusts doc_id.**  Extracted documents that never got filed,
   and filed documents we never extracted, are counted and listed but not
   diagnosed; a broken doc_id pipeline would look like missing documents,
   not like extraction errors.

## Traceability

`evaluate.py` produces one row per (document, field) with the extracted
value, filed value, comparator verdict and reason — that frame *is* the
source of truth, written to `out/field_comparisons.csv`.  Every other
number (headline rate, calibration buckets, breakdowns, straight-through)
is a plain aggregation of it, and every number in the HTML report links to
the underlying rows.  If a metric looks wrong, open the CSV and read the
rows; there is no hidden state to reverse-engineer.

Outputs written by a run:

| file | contents |
|---|---|
| `out/report.html` | the self-contained report |
| `out/field_comparisons.csv` | every field-level comparison with reason |
| `out/doc_summary.csv` | per-document mismatch counts, min tier-1 confidence, clean flag |
| `out/per_field.csv` | precision / recall per field |
| `out/breakdown.csv` | mismatches by country × source system |
| `out/calibration.csv` | observed accuracy per confidence decile |
| `out/straight_through.csv` | auto-accept share and escaped errors per threshold |
| `out/validity_failures.csv` | every failed validity check with reason |

## The synthetic data

`generate_synthetic.py` produces a few thousand paired records with the
error patterns real customs extraction actually shows: OCR digit confusion
in values and weights, HS codes truncated to 6 digits, day/month swaps in
dates, missing origin countries, incoterm and currency confusion, invoice
lines that stopped summing to their total — plus one country (TR) whose
documents are systematically ~3× worse, because someone's scans always
are.  Confidence scores are generated to be imperfectly honest: corrupted
fields tend to score lower, but the distributions overlap, so the
calibration table shows a realistic curve rather than a step function.
