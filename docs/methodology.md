# Evaluation methodology

[Back to the project overview](../README.md)

Keep three questions separate: did extraction read the source correctly,
does showing it help a reviewer, and is something changing that deserves
investigation? This harness demonstrates extraction evaluation; a
[reviewer pilot](reviewer-pilot.md) needs additional evidence.

## Choose the reference before interpreting the score

| Reference | What it tells you | What it cannot establish alone |
|---|---|---|
| Independent answer key (gold / truth) | Whether extraction agrees with what the source said. The generator provides this for the demo. | Synthetic results do not establish real-world accuracy. Production needs representative, independently adjudicated examples. |
| Final filing (proxy) | Whether extraction agrees with the downstream record available at scale. | The filing may repeat an extraction error or contain a legitimate later amendment. Agreement is not correctness. |
| No answer key (blind checks) | Whether extracted data violates format, arithmetic or other validity rules; whether confidence looks unusual. | Plausible but wrong data can pass every check. No warning does not mean correct. |

The demo scores against both generated truth and filing records. It
identifies **false alarms** (extraction matches truth but not the filing)
and **hidden errors** (extraction and filing agree but are wrong). The
baseline generator starts filings from truth apart from amendments, so
few hidden errors are expected by construction, not because reviewers
have been shown to catch mistakes.

The separate synthetic review demonstration copies eligible wrong header
values into hypothetical filings. It illustrates how a reference can
become contaminated by the extraction it is meant to check.

## Pair goods lines before comparing fields

A declaration has a header and multiple goods items. Line numbers on the
two sides are not reliable identifiers: extraction can drop, merge or
invent lines.

`alignment.py` scores candidate pairs using HS-code agreement, value,
origin and quantity, then greedily takes the best available pairs above
the configured score floor. HS agreement respects the code hierarchy:
2-digit chapter, 4-digit heading, 6-digit subheading, 8-digit CN and
10-digit TARIC. Each accepted pair retains a written basis.

An unpaired filed item becomes `missed_item`; an unpaired extracted item
becomes `spurious_item`. These are structural mismatch rows, with
criticality set in `field_tiers.yaml`. They describe differences against
the reference, not independently confirmed extraction defects.

Greedy pairing is not globally optimal. Similar items can pair
incorrectly, and a merge can look like one accepted pair plus one missed
item. Audit consequential pairings against the source and validate the
policy on independently labelled examples.

## Read the metrics with their definitions

The headline **clean document rate** is the share of joined documents
with zero tier-1 mismatch rows. A declaration is filed as a unit, so one
important discrepancy matters even when many fields agree.

Missing or unparseable values receive `not_comparable`, never a match.
However, they do not count as mismatches in the clean flag. **Clean means
no important mismatch detected, not complete or verified.** Inspect the
unresolved counts; do not use the headline alone as an acceptance gate.

Document-level rates also hide error counts and severity within a
document. Field-level breakdowns explain what to investigate:

| Measure | Definition here |
|---|---|
| Precision | Among rows where extraction and reference both have a value, the share that match. |
| Recall | Among rows where the reference has a value, the share where extraction produced a matching value. Missing extraction hurts recall. |
| Structural differences | Whole missed and spurious items, reported separately rather than spread into per-field recall. |

These are deliberately simple agreement measures, not a universal
definition of precision and recall for every extraction task.

High field accuracy can hide substantial document failure. With
independent errors at 99% field accuracy, roughly 90% of 11-field
documents but only 63% of 46-field documents have every field correct.
Real errors may be correlated. Neither field nor document accuracy
measures reviewer effort or time saved.

## Confidence is another thing to evaluate

A confidence score is not a guarantee. A completely omitted goods line
has no score, so confidence cannot directly warn about it.

| Question | Measure | Interpretation |
|---|---|---|
| When the score says 0.9, is it right about nine times in ten? | Confidence bands and honesty gap (calibration / ECE) | Lower calibration error is better. |
| How wrong are the probability predictions? | Error score (Brier score) | Lower is better; confidently wrong predictions are penalized heavily. |
| Does confidence rank right values above wrong ones? | Sorting power (AUROC) | 1 means perfect separation, 0.5 means chance-level overall ranking, not necessarily uselessness at every threshold or in every subgroup. |

Confidence-quality calculations use only rows with a confidence and a
definite match/mismatch verdict. Unmatched structural items and unresolved
comparisons are outside that calculation. Inspect per-field results:
the same score on a date and an HS code need not mean the same thing.

Recalibration may improve probability estimates when checked on separate,
representative, independently labelled data. A monotonic recalibration
cannot improve ranking.

The straight-through table asks a **future what-if**: what happens if a
document bypasses review when every tier-1 field clears a confidence
threshold? The decision uses extraction alone, before filing exists.
Missing critical confidence blocks acceptance, but a silently dropped
item leaves no score behind. The escaped-error count includes those
documents. No threshold in this demo authorizes less human review.

## Validity checks are warnings, not verification

`validity.py` checks header and item formats, supplied code lists, dates,
expected quantity units, net versus gross weight, and whether item values
sum to the invoice total.

The sum check can flag dropped or invented lines without a reference.
It cannot catch a merge that preserves the sum, and plausible wrong
values can satisfy all the rules. The early-warning table reports how
much more often flagged documents have important filing mismatches
(lift), alongside errors without warnings. Those labels still inherit
the filing's limitations.

The generator demonstrates three styles of error:

| Style | Examples | Why it matters |
|---|---|---|
| Noise | Confused digits, truncated codes, swapped dates, missing values. | Some errors look broken and are caught by checks. |
| Fabrication | A valid-looking but wrong identifier, tariff code or origin, with high confidence. | The demo deliberately makes these pass validity checks. |
| Structural | Dropped, merged or invented goods lines. | Field scores alone miss whole-line problems. |

These are illustrative profiles, not measured failure rates for OCR or
language models. The generator covers Germany and the Netherlands,
1-5 items per declaration, deliberately overlapping items, and a worse
Netherlands lane by assumption. Its confidence distributions and country
differences are designed scenarios, not findings about real systems.

## Distinguish different kinds of uncertainty

An uncertain extraction, an inconclusive comparison, an unreliable
reference and a noisy population estimate are different problems.
They do not automatically combine into a single uncertainty score.

The report uses document resampling (bootstrap) for headline-rate ranges
and Wilson intervals for simple proportions, slices and audit samples.
These describe sampling variation under their assumptions. They do not
repair biased labels, non-representative samples, reviewer clustering,
distribution shift or systematic evaluator errors. Overlapping ranges
are not themselves a formal test of a difference.

`uncertainty.audit_sample_estimate` illustrates estimating a clean rate
from a simple random sample of synthetic gold labels, with samples of
50, 150 and 500 where available. To roughly halve sampling uncertainty
usually takes four times as many observations. This is not a production
audit queue or human-label ingestion workflow.

For real work, independently audit a probability sample, including
apparently clean cases. Targeted investigations answer different
questions. If risky cases are oversampled, retain sampling probabilities
and use appropriate weighting and uncertainty estimates; the demo's
simple estimator does not implement that correction. A 95% confidence
procedure covers the true parameter in about 95% of repeated samples
under its assumptions, not every run.

## Compare models on the same documents

`compare_models.py` compares models against a shared reference document by
document (a paired comparison). The both-clean / only-A / only-B /
neither-clean split shows where they differ. Paired resampling gives an
uncertainty range for the clean-rate difference.

The win/loss split among documents where only one model is clean also
gets an exact sign-test / McNemar p-value. It describes how often a split
at least this lopsided would occur under the equal-performance null
assumption. It is not the probability the winner is real or the result
is "just luck"; a small p-value does not establish practical importance.

Different document sets or comparator versions produce warnings. Inspect
those warnings and error types, not just the headline. A reference
influenced by one model can bias the comparison, and amendments need not
affect both models equally. Comparing extractors is not a randomized
study of reviewers using them.

## Known limitations to resolve before real use

| Assumption | Consequence |
|---|---|
| Final filings can be amended or wrong. | Filing-based mismatch and escape rates are neither guaranteed upper nor lower bounds on true error. Use independent audit and amendment provenance. |
| Item pairing is greedy. | Similar lines and merges can be misinterpreted. Inspect the pairing evidence. |
| Values are invoice-denominated. | No freight/insurance adjustment or exchange-rate conversion is modelled. Decide which value a real deployment measures. |
| Ambiguous dates use the configured `dayfirst` preference. | The preference can be wrong; comparison reasons flag cases where swapping day and month would remove a mismatch. |
| The EORI checksum is invented. | National digit counts are modelled, but there is no universal real EORI check digit. Real validation needs an appropriate registry check. |
| Reference code lists are subsets and tariff codes are not date-versioned. | Real valid codes can fail checks; a production reference needs appropriate scope and effective dates. |
| Document joining trusts `doc_id`. | Documents present on only one side are counted and listed, not diagnosed. |
| Unresolved comparisons do not make a document unclean. | Define and validate an operational policy for insufficient evidence before using scores to make decisions. |

Tests establish behaviour for fixtures and that the pipeline connects.
They do not certify that field tiers, tolerances, customs assumptions or
pairing rules are right for a real filing decision. Those choices require
domain review and representative independently adjudicated examples.
