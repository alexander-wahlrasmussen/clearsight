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
canonical.py           header + goods items: the shape every source maps into
readers/               one module per source system: read(path) -> [CanonicalRecord]
alignment.py           pairs extracted goods items with filed goods items
comparators.py         versioned field comparators: match / mismatch / not_comparable
field_tiers.yaml       which field is how critical, judged by which comparator
validity.py            deterministic rules that need no ground truth
evaluate.py            join docs, align items, apply comparators, aggregate;
                       plus the cross-regime analyses (leading indicators,
                       proxy-vs-gold disagreement)
uncertainty.py         error bars: bootstrap, Wilson intervals, audit-sample estimator
confidence_quality.py  is the confidence score any good: ECE, Brier, AUROC
report.py              one self-contained HTML file, every number clickable
generate_synthetic.py  paired records with deliberately injected error patterns
run_demo.py            end to end, across all three evaluation regimes
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

## Three evaluation regimes

The same extraction gets scored three ways, because production only ever
has some of them:

- **Truth (gold).**  The generator writes the true records to
  `data/gold_truth.csv` — same schema as the ledger export, same reader,
  same evaluator.  This regime only exists here because the data is
  synthetic; in production its stand-in is a periodically drawn,
  human-graded audit sample.
- **Proxy (the filed record).**  Available for every document, but days
  late and noisy: amendments count as errors, and agreement can hide
  shared mistakes.  This is what the report's main sections score against,
  because it is what production sees.
- **Blind (validity rules + confidence).**  Available instantly, before
  filing, with no reference at all.

Having gold and proxy side by side makes the **proxy bias measurable**
instead of hand-waved: the report shows both clean rates and splits every
proxy verdict by what gold says — *false alarms* (mismatch vs filed, match
vs gold: the amendments) and *hidden errors* (match vs filed, mismatch vs
gold: extraction and filing wrong the same way).  Hidden errors are near
zero while filings are independent of extraction; they are the number that
silently grows once auto-accepted extractions start being filed verbatim —
the feedback loop that eventually makes the proxy metric worthless on the
auto-accepted segment.

The **audit-sample estimator** (`uncertainty.audit_sample_estimate`) is
the production bridge: draw K documents, grade only those against truth,
and the Wilson interval says what the sample is worth.  The report shows
K = 50/150/500 so the cost-of-precision curve is visible — you don't buy
gold for the population, you buy a well-drawn sample to calibrate your
proxy.

The **leading-indicators table** measures the blind regime: for documents
flagged by a validity failure or low confidence, how much likelier is an
eventual tier-1 mismatch (lift over the base rate) — and, in the "neither
signal" row, how much damage looks perfectly clean ex ante.

## OCR noise vs LLM fabrication

The generator injects errors in three styles, tagged in
`data/injected_errors_truth.csv`, because OCR systems and LLM extractors
fail differently:

- **noise** (OCR-era): digit confusion, truncated codes, swapped dates,
  missing fields — garbage that often *looks* like garbage, so checksums,
  format rules and arithmetic catch a good share of it;
- **fabrication** (LLM-era): a *different but valid* EORI (right country,
  right length, correct check digit), an *in-tariff* HS sibling from the
  same chapter, a *real but wrong* origin country — delivered with high
  confidence.  Fluent garbage.  It passes every validity rule by
  construction, and it is exactly what confidence gates wave through;
- **structural**: dropped, merged and invented goods items.

`run_demo.py` runs a clearly-labelled synthetic-only diagnostic (it reads
the truth sidecar, which the harness proper never does) reporting the
share of each style that any blind signal actually pointed at.  In the
default run: roughly half of the noise, most of the structural (the
item-sum rule), and effectively none of the fabrications.  That asymmetry
is the operational argument for keeping the proxy and audit regimes
running even when blind monitoring looks healthy.

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

`confidence_quality.py` separates the two properties the calibration table
conflates:

- **calibration** (ECE, Brier): does 0.9 mean 90%?  Fixable after the fact
  by recalibration, if the ranking underneath is sound;
- **discrimination** (AUROC, the rank-based Mann-Whitney form): do wrong
  fields score lower than correct ones *at all*?  Not fixable by any
  recalibration — and the auto-accept gate only uses the ranking, so AUROC
  is what decides whether a confidence gate can work.

Both are computed against gold *and* against the proxy, which demonstrates
label-noise attenuation: the same score's measured discrimination is lower
against the noisy proxy than it truly is (while apparent calibration can
drift either way).  Per-field quality matters more than the pooled number:
a pooled 0.9 hides that 0.9 on an HS code and 0.9 on a date mean different
things — and in the demo, the fields fabrication targets are precisely the
ones whose AUROC collapses.

## Error bars

Every headline proportion carries an interval, stdlib-computed
(`uncertainty.py`): a percentile **bootstrap** (resampling documents) for
the clean document rate — shown as the general tool that works for any
statistic — and closed-form **Wilson intervals** for every
country × source-system slice and the audit samples.  The point of the
per-slice intervals is to stop over-reading small cells: a two-point
difference between slices whose intervals overlap is not a finding.

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
9. **The blind regime cannot see fabrications.**  Valid-looking,
   high-confidence, wrong values pass every validity rule by construction
   and sail through confidence gates.  The synthetic diagnostic quantifies
   it; nothing in this harness fixes it.  The only countermeasures are the
   proxy and the audited gold sample — which is why they exist.
10. **The audit estimator assumes an honest random sample.**  Real audit
    queues are rarely random (they oversample flagged documents); a
    non-random sample needs reweighting the estimator does not do.  And a
    95% interval still misses one run in twenty.

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
| `out/field_comparisons.csv` | every comparison with level, item numbers, reason (vs filed) |
| `out/gold/*.csv` | the same evaluation frames, scored vs gold truth |
| `out/doc_summary.csv` | per-document mismatch and item counts, min tier-1 confidence, clean flag |
| `out/per_field.csv` | precision / recall per header and item field |
| `out/breakdown.csv` | mismatches by country × source system |
| `out/calibration.csv` | observed accuracy per confidence decile |
| `out/straight_through.csv` | auto-accept share and escaped errors per threshold |
| `out/alignment_summary.csv` | items paired / missed / spurious per source system |
| `out/validity_failures.csv` | every failed validity check with reason |
| `out/leading_indicators.csv` | blind signals vs eventual tier-1 mismatches, with lift |
| `out/confidence_summary.csv` | ECE / Brier / AUROC per regime |
| `out/confidence_per_field.csv` | calibration gap and AUROC per field (vs gold) |
| `out/audit_estimates.csv` | audit-sample clean-rate estimates with Wilson CIs |

Truth files written by the generator (read by humans and by run_demo's
labelled diagnostics, never by the harness proper): `data/gold_truth.csv`,
`data/amendments_truth.csv`, `data/injected_errors_truth.csv`.

## The synthetic data

`generate_synthetic.py` produces a few thousand paired multi-item
declarations with the error patterns real customs extraction actually
shows, in the three styles described above.  Noise: OCR digit confusion in
totals, item values, weights, EORI and BL references; HS codes truncated
to 6 digits; day/month swaps; missing origins; wrong supplementary units;
incoterm and currency confusion.  Fabrication: valid-but-wrong EORIs, HS
siblings and origins, at high confidence.  Structural: dropped last lines,
two same-chapter lines merged into one, and subtotal rows read as goods
items — multi-item documents deliberately include same-chapter items so
alignment has something genuinely hard to do.  The NL lane is
systematically ~2.5× worse than DE, because someone's scans always are.
Confidence scores are imperfectly honest: noise-corrupted fields tend to
score lower, fabricated fields score high, the distributions overlap, and
dropped lines produce no score at all.
