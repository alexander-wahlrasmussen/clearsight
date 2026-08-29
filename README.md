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
pip install -r requirements.txt   # Python 3.10+; pandas and PyYAML only
python run_demo.py        # generates data, evaluates, opens out/report.html
python run_ab_demo.py     # two models, same documents -> out/ab/comparison.html
pytest                    # unit tests plus end-to-end smoke tests
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

- Items are paired best-looking match first (taken and never
  reconsidered), scored on how deep their HS codes agree — using the
  code's real structure: 2 digits = chapter, 4 = heading, 6 = subheading,
  8 = CN, 10 = TARIC — plus how close the values and quantities are and
  whether the origins agree.
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

## Three ways to score the same extraction

To say the tool got something wrong you need something to compare
against, and there are three candidates.  The code calls them *regimes*;
each has a catch:

- **The truth (called "gold" throughout).**  What the paperwork really
  said.  The generator writes it to `data/gold_truth.csv` — same file
  layout as the ledger export, same reader, same evaluator.  We only have
  it because this data is generated; in the real world you buy truth in
  small amounts by having a person check documents by hand.
- **What was filed (called the "proxy" — our stand-in for the truth).**
  Available for every document, but it arrives days later and it is not
  quite the truth: filings get legitimately corrected after the fact, and
  sometimes a filing repeats the tool's own mistake.  The report's main
  sections score against this, because it is all production normally has.
- **No answer key at all (called "blind").**  The built-in checks and the
  tool's own confidence scores, available the moment a document is
  processed.

Having truth and proxy side by side turns "the filed record isn't quite
the truth" from a caveat into a **measured number**: the report shows both
clean rates, and sorts every disagreement between the two scorings into
*false alarms* (the tool was right; the filing was amended afterwards) and
*hidden errors* (the tool and the filing were wrong in the same way — a
mistake no filing-based check can ever see).  Hidden errors are near zero
while people still file from the paperwork; the moment auto-accepted
extractions start being filed as-is, that number grows silently, and
"agreement with the filing" stops meaning anything for those documents.

The **audit sample** (`uncertainty.audit_sample_estimate`) is what
replaces gold in production: pick documents at random, have a person grade
only those, and use the sample to estimate the true clean rate — with an
honest range around it that narrows as the sample grows.  The report shows
samples of 50, 150 and 500 so you can see what precision costs: to halve
the range, check four times as many documents.

The **early-warning table** measures the blind regime: for documents
flagged by a failed check or a low confidence score, how much likelier is
a serious error than for the average document — and, in the "no warning
sign" row, how much damage looks perfectly clean until the filing lands.

## Comparing models head-to-head (A/B)

To decide between two extraction models, run both over the **same
documents**, judge both against the **same** filed records, and let
`compare_models.compare()` line them up.  With real data:

```python
from readers import acme_csv, customs_ledger
import compare_models, evaluate

tiers = evaluate.load_field_tiers("field_tiers.yaml")
filed = customs_ledger.read("filed_export.csv")
evaluations = {
    "model_a": evaluate.evaluate(acme_csv.read("a.csv", source_system="model_a"), filed, tiers),
    "model_b": evaluate.evaluate(acme_csv.read("b.csv", source_system="model_b"), filed, tiers),
}
comparison = compare_models.compare(evaluations, "the filed records")
```

(`python run_ab_demo.py` does exactly this with generated data: an
OCR-style model and an LLM-style model extracting the same 3,000
documents.)

Because every model saw the same documents, the comparison is **paired**
— scored document by document — which is what makes it decisive:

- The four-way split (clean under both / only A / only B / neither) is
  the actual evidence.  Documents both models get right or wrong together
  — including ones whose filings were amended — land in the agreeing rows
  and cancel out.
- The difference in clean rates gets a likely range from resampling the
  paired documents, far tighter than comparing two separate ranges.
- The win/loss split among the disagreement documents gets a plain
  **"chance it's luck"** number: how often a split at least this lopsided
  would occur between equally good models.  Below ~5%, the winner is
  real; above it, the run does not separate the models and you need more
  documents.  (Statisticians call this exact test McNemar's / the sign
  test.)

Fairness is checked, not assumed: differing document sets or differing
comparator versions between the runs land in `comparison.warnings` and at
the top of the report's caveats.  The comparison report
(`out/ab/comparison.html`) also shows each model's failure *character* —
missed and spurious items, escapes at the auto-accept threshold, sorting
power — because two models with the same clean rate can need entirely
different safety nets.  In the demo that is exactly what happens: the two
models tie on the headline while differing sharply on how their errors
would be caught.

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

`run_demo.py` runs a clearly-labelled synthetic-only check (it reads the
truth sidecar, which the harness proper never does) reporting, for each
style, the share of injected errors that any built-in check actually
pointed at.  In the default run: roughly half of the noise, most of the
structural errors (the do-the-numbers-add-up rule catches dropped and
invented lines), and effectively none of the fabrications.  That asymmetry
is the whole argument for keeping the filing comparison and the human
audit running even when the built-in checks look healthy.

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

## The confidence score: can we trust it, and what can we automate?

Every extracted field comes with a confidence score between 0 and 1.  The
report groups those scores into ten bands and shows, for each band, how
often the tool was actually right.  If the score means anything, the
0.9–1.0 band should be right far more often than the 0.6–0.7 band; if the
rows look the same, the score is decoration and must not be used to
decide anything.

The straight-through table answers the operational question directly: *if
we accepted, without human review, every document whose tier-1 fields all
carry confidence ≥ t*, what share of the workload goes through untouched,
and how many bad documents ride along?  The accept/review decision is
made the way production would have to make it — from the extracted record
alone, before any filing exists — which exposes the ugliest property of
confidence gating: a document whose extraction silently dropped a goods
item still sails through, because the missing line has no score to be
low.  The escaped-error column includes those documents on purpose.

`confidence_quality.py` separates two questions that sound alike but have
very different consequences:

- **Is the score honest?**  When the tool says 0.9, is it right about 9
  times in 10?  Measured by the *honesty gap* (the average distance
  between claimed confidence and the actual hit rate — the technical name
  is ECE) and the *error score* (which punishes being confidently wrong
  hardest — the technical name is Brier score).  A dishonest-but-consistent
  score can be repaired: once you know "0.9 really means 0.8", you relabel
  it.
- **Can the score tell right from wrong at all?**  Pick one field the tool
  got right and one it got wrong, at random: *sorting power* (technical
  name AUROC) is how often the right one carries the higher score.  1.0
  means always — a threshold can cleanly separate good from bad; 0.5 means
  the score is a coin flip.  No relabelling can fix poor sorting power,
  and the auto-accept gate depends on it entirely.

Both are computed against the truth *and* against the filing, which shows
a subtle trap: judged with a noisy answer key, the same score's sorting
power reads lower than it really is.  And the per-field table matters more
than the overall number — a pooled 0.9 hides that 0.9 on an HS code and
0.9 on a date mean different things.  In the demo, the fields where
fabricated values were planted are precisely the ones whose sorting power
collapses.

## Error bars

Every headline percentage carries a range, computed with the standard
library (`uncertainty.py`).  Two tools, deliberately shown side by side:

- For the headline clean rate, the **bootstrap**: re-draw the same number
  of documents at random (allowing repeats) hundreds of times and watch
  how much the rate wobbles.  Crude, general, and honest — it works for
  any statistic, not just percentages.
- For every country × source-system slice and the audit samples, the
  **Wilson interval**: a ready-made formula for "how sure can you be about
  a percentage based on n cases", instant and well-behaved even for small
  slices.

The point of the per-slice ranges is to stop people over-reading small
cells: when two slices' ranges overlap, the difference between them may
well be luck.

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
3. **Item pairing is a best guess, not a fact.**  The pairing takes the
   best-looking match first and never reconsiders, so two similar goods
   lines can pair the wrong way round; and a merged line whose totals
   happen to equal one filed item pairs cleanly while the other filed
   item is reported as missed (roughly right, but it blames the wrong
   kind of defect).  Every pairing states its reasoning in the
   drill-downs precisely so a person can check it.
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
| `out/ab/comparison.html` | the A/B comparison report (run_ab_demo.py) |
| `out/ab/comparison_summary.csv` | per-model scorecard |
| `out/ab/head_to_head.csv` | paired win/loss counts, difference range, luck probability |
| `out/ab/per_field_comparison.csv` | mismatch rate per field per model, sorted by gap |

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

## Plain-English glossary

The terms below appear in the code, the CSV columns and the report.
Plain meaning first; use the technical name when talking to a data
scientist or searching the literature.

| term | plain meaning |
|---|---|
| gold / truth | what the paperwork really said.  Synthetic here; a human-graded sample in production |
| proxy | our stand-in for the truth: the record that ended up filed.  Close, but amended and occasionally wrong itself |
| blind | judging with no answer key at all: built-in checks plus the tool's confidence scores |
| regime | which of the three answer keys (gold / proxy / blind) a number was computed against |
| tier | how much a field matters: tier 1 errors corrupt the filing, tier 2 cause rework, tier 3 are cosmetic (set in field_tiers.yaml) |
| clean document rate | share of documents with zero tier-1 problems — the headline number |
| precision | when the tool wrote a value, how often it was the right one |
| recall | of the values that ended up filed, how often the tool delivered a matching one.  An empty field hurts recall, not precision |
| calibration / honesty | whether a claimed confidence of 0.9 really means right 9 times in 10 |
| ECE ("honesty gap") | average distance between claimed confidence and the actual hit rate; 0 is perfect |
| Brier score ("error score") | like the honesty gap, but punishes being confidently wrong hardest; lower is better |
| AUROC ("sorting power") | pick one right field and one wrong field at random: how often the right one has the higher confidence.  1.0 = always, 0.5 = coin flip |
| confidence interval / likely range | the band a percentage could honestly be in, given how many cases it was computed from.  A 95% range still misses about one run in twenty |
| bootstrap | estimate that band by re-drawing the documents at random hundreds of times and watching the number wobble |
| Wilson interval | a ready-made formula for that band when the number is a simple percentage |
| lift ("times the average") | how much likelier an error is for flagged documents than for the average document |
| false alarm | the tool was right, but the filing was changed afterwards, so the comparison blames the tool |
| hidden error | the tool and the filing are wrong in the same way — invisible to any filing-based check |
| amendment | a legitimate change to a filing after the fact (revaluation, corrected count, reclassification) |
| noise (error style) | OCR-era mistakes: mangled digits, truncated codes, missing fields.  Often looks broken, so checks catch a fair share |
| fabrication (error style) | LLM-era mistakes: valid-looking, high-confidence, wrong.  Passes every check by construction |
| structural (error style) | whole goods lines dropped, merged, or invented from a subtotal row |
| alignment | pairing each extracted goods item with the filed item it most plausibly corresponds to |
| missed / spurious item | a filed item with no extracted partner / an extracted item with no filed partner |
| straight-through | accepting a document without human review because its confidence scores clear a threshold |
| escaped error | a bad document that a straight-through policy would have accepted |
| head-to-head / paired comparison | scoring two models document by document over the same documents, so shared successes and failures cancel out |
| chance it's luck | among documents where exactly one model was clean, how often a split this lopsided would occur between equally good models (the technical name is McNemar's / sign test p-value) |

