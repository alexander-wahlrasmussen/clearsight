# ClearSight — extraction quality measurement harness

A prototype that measures how accurate an automated document extraction
tool is, by comparing what the tool extracted against what was actually
filed in a downstream customs system.  The downstream system is treated as
a black box we can only read from.

Plain Python, pandas and the standard library.  No ML libraries.  Every
metric is traceable to rows a human can open and read.

The synthetic population covers declarations filed in **Germany and the
Netherlands**, with 1–5 goods items per declaration.

## Quick start

```bash
pip install -r requirements.txt
python run_demo.py        # generates data, evaluates, opens out/report.html
pytest                    # tests for comparators, validity rules and alignment
```

`run_demo.py` writes synthetic source files to `data/`, row-level results
and the HTML report to `out/`.  Both directories are gitignored.

## What the layers are, and why they are separate

```
canonical.py          header + goods items: the shape every source maps into
readers/              one module per source system: read(path) -> [CanonicalRecord]
alignment.py          pairs extracted goods items with filed goods items
comparators.py        versioned field comparators: match / mismatch / not_comparable
field_tiers.yaml      which field is how critical, judged by which comparator
validity.py           deterministic rules that need no ground truth
evaluate.py           join docs, align items, apply comparators, aggregate
report.py             one self-contained HTML file, every number clickable
generate_synthetic.py paired records with deliberately injected error patterns
run_demo.py           end to end
```

The separation is the point:

- **Adding a source system is one file.**  A reader maps its system's
  export (flat item-per-row CSV, nested XML, whatever) into
  `CanonicalRecord` + `CanonicalItem` and gets registered in
  `readers/__init__.py`.  Alignment, comparators, validity, evaluation and
  reporting do not change.  The demo ships two deliberately different stub
  readers (Acme's flat CSV, Globex's nested XML) to prove it.
- **Criticality is configuration, not code.**  `field_tiers.yaml` maps
  each header and item field to a tier (1 = an error corrupts the filing,
  2 = rework, 3 = cosmetic) and to a comparator with parameters — and also
  configures how severe a missed or spurious goods item is, and the
  minimum score at which items may be paired.  All of it is an edit and a
  re-run.
- **Comparators and the aligner are versioned.**  Every comparison row
  records the comparator (or aligner) name, parameters and version.  When
  a metric moves between two runs, you can tell whether the data changed
  or the rules did.

## Declarations are multi-item, and that changes the problem

A real customs declaration is a header (importer EORI, incoterm, currency,
invoice total, dates) plus N goods items, each with its own HS code,
origin, quantity, value and weights.  The hardest part of measuring
extraction quality is not comparing values — it is deciding **which
extracted item corresponds to which filed item** when extraction merged
two lines, dropped one, or invented one from a subtotal row, and both
sides number their lines independently.

`alignment.py` makes that decision explicitly and auditable:

- Items are paired greedily, best pair first, scored on the real HS
  hierarchy (2 digits = chapter, 4 = heading, 6 = subheading, 8 = CN,
  10 = TARIC), value closeness, origin agreement and quantity closeness.
- Pairs below the configured `min_score` are refused: better two honest
  unmatched items than one invented pairing.
- Every accepted pair carries a written basis ("HS agrees to 6 digits;
  values differ by 1.2%; origin agrees") that appears in the report's
  drill-downs.
- A filed item with no extracted partner is a **missed_item**; an
  extracted item with no filed partner is a **spurious_item**.  Both flow
  through the same comparisons frame as ordinary mismatches, at the tier
  `field_tiers.yaml` assigns them (tier 1 by default), so a dropped line
  makes a document not clean exactly like a wrong HS code does.

## Why clean document rate is the headline

The headline metric is **clean_document_rate**: the share of documents
with *zero* tier-1 mismatches — wrong values, wrong codes, and missed or
spurious items alike.  Not field-level accuracy, deliberately:

- A declaration is filed as a unit.  One wrong tier-1 field on any goods
  item and a human has to touch the document — it does not matter that
  the other twenty fields were right.
- Field-level accuracy flatters the tool, and flatters it more the busier
  the document.  At 99% per-field accuracy, a 1-item declaration (~11
  compared fields) is clean ~90% of the time; a 5-item declaration (~46
  fields) is clean ~63% of the time.  "99% accurate" and "a third of busy
  documents need rework" are the same number wearing different clothes.
- Cost scales with documents touched, not fields corrected.

Per-field precision and recall are still reported — they tell you *what*
to fix — they are just not the headline.

Definitions used (kept deliberately simple):

- **precision** — of the rows where the tool extracted a value and the
  filing has one, the share that match: "when the tool writes a value,
  how often is it right?"
- **recall** — of the rows where the filing has a value, the share where
  the tool produced a matching one.  A value the tool failed to extract
  hurts recall, not precision.  (Item fields aggregate over aligned
  pairs; whole missed items are counted in the alignment summary, not
  smeared into per-field recall.)

## Confidence: calibration and straight-through

The report buckets per-field extraction confidence into deciles and shows
observed accuracy per bucket.  If confidence means anything, accuracy
climbs with the bucket; if the row for 0.9–1.0 is not visibly better than
0.6–0.7, the score is decoration and must not gate anything.

The straight-through table answers the operational question directly: *if
we auto-accepted every document whose tier-1 fields all carry confidence ≥
t*, what share of the workload goes through untouched, and how many bad
documents ride along?  The auto-accept decision is computed **ex ante**,
from the extracted record alone — which exposes the ugliest property of
confidence gating: a document whose extraction silently dropped a goods
item still auto-accepts, because the missing line has no score to be low.
The escaped-error column includes those documents on purpose.

## Validity rules

`validity.py` holds deterministic checks that need no ground truth:
header rules (ISO currency and country, EORI national format + checksum,
invoice date plausibility, **goods item values summing to the invoice
total**) and per-item rules (HS code format and presence in a supplied
tariff list, ISO origin, the supplementary unit the HS chapter prescribes
— footwear in pairs, beverages in litres — and net weight not exceeding
gross).

The item-sum rule is the quiet star: it is pure arithmetic on the
extracted record, yet it catches dropped lines and invented subtotal lines
*before filing, with no ground truth in sight*.  In the demo run it flags
every spurious-item document and every dropped-item document.  What it
cannot catch — deliberately — is a merge of two lines, because a merge
preserves the sum.

## Where this harness is knowingly wrong

Read these before quoting any number from the report.

1. **Amendments look like extraction errors.**  Documents get legitimately
   amended after filing — item revaluations, corrected counts, broker
   reclassifications, with the invoice total recomputed to match.  The
   harness compares extraction against the *final* filed record, so every
   amendment shows up as a mismatch.  All mismatch counts and the
   straight-through escape rate are therefore **upper bounds** on true
   extraction error.  The synthetic generator injects such amendments on
   purpose and writes them to `data/amendments_truth.csv` — which the
   harness never reads — so you can check how much of the measured error
   the ambiguity accounts for.  In production you would approximate this
   with filing-system audit history, if the black box ever exposes it.
2. **The filed record is not ground truth.**  When extraction and filing
   agree, both can be wrong.  Agreement measures consistency, not
   correctness.  The validity rules catch some of these cases; most go
   unmeasured.
3. **Item alignment is a heuristic, not truth.**  Greedy best-first
   matching is not globally optimal; two same-chapter items with similar
   values can pair the wrong way round; a merged line whose totals happen
   to equal one filed item pairs cleanly while the other filed item is
   reported as missed (which is directionally right but attributes the
   defect to the wrong shape).  Every pairing's stated basis is in the
   drill-downs precisely so a human can audit it.
4. **Values are invoice values, not customs values.**  No incoterm
   freight/insurance adjustment and no exchange-rate conversion is
   modelled.  Both sides carry invoice-denominated values so the
   comparison is at least like for like, but a real deployment must
   decide which value it is measuring.
5. **Ambiguous dates are resolved by preference.**  "04/05/2026" with both
   parts ≤ 12 is resolved by the `dayfirst` parameter in
   `field_tiers.yaml`, which can itself be wrong.  When a mismatch would
   disappear with day and month swapped, the comparator's reason says so.
6. **The EORI checksum is invented.**  The national digit counts are real
   (DE issues 15-digit numbers, NL uses the 9-digit RSIN), but real EORI
   numbers have no universal check digit — real validation is an EOS
   registry lookup.  Swap the checksum for a registry client in
   production.
7. **Reference lists are subsets.**  The currency list covers common
   codes; the chapter→unit map is a plausible subset of the real per-code
   unit table; the tariff list is whatever file you supply and is not
   date-versioned (real CN codes change every January).  A
   valid-but-unlisted code fails validity — a false alarm a human can
   waive, which we prefer to a silent pass.
8. **The join trusts doc_id.**  Extracted documents that never got filed,
   and filed documents we never extracted, are counted and listed but not
   diagnosed.

## Traceability

`evaluate.py` produces one row per compared thing — header field, aligned
item field, or unmatched item — with the extracted value, filed value,
verdict, reason, and (for item rows) the alignment score and basis.  That
frame *is* the source of truth, written to `out/field_comparisons.csv`.
Every other number is a plain aggregation of it, and every number in the
HTML report links to the underlying rows.

Outputs written by a run:

| file | contents |
|---|---|
| `out/report.html` | the self-contained report |
| `out/field_comparisons.csv` | every comparison with level, item numbers, reason |
| `out/doc_summary.csv` | per-document mismatch and item counts, min tier-1 confidence, clean flag |
| `out/per_field.csv` | precision / recall per header and item field |
| `out/breakdown.csv` | mismatches by country × source system |
| `out/calibration.csv` | observed accuracy per confidence decile |
| `out/straight_through.csv` | auto-accept share and escaped errors per threshold |
| `out/alignment_summary.csv` | items paired / missed / spurious per source system |
| `out/validity_failures.csv` | every failed validity check with reason |

## The synthetic data

`generate_synthetic.py` produces a few thousand paired multi-item
declarations with the error patterns real customs extraction actually
shows.  Field-level: OCR digit confusion in totals, item values, weights,
EORI and BL references; HS codes truncated to 6 digits; day/month swaps;
missing origins; wrong supplementary units; incoterm and currency
confusion.  Structural: dropped last lines, two same-chapter lines merged
into one, and subtotal rows read as goods items — multi-item documents
deliberately include same-chapter items so alignment has something
genuinely hard to do.  The NL lane is systematically ~2.5× worse than DE,
because someone's scans always are.  Confidence scores are imperfectly
honest: corrupted fields tend to score lower, the distributions overlap,
and dropped lines produce no score at all.
