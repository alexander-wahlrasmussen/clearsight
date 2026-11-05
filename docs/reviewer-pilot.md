# Does pre-fill help a person?

[Back to the project overview](../README.md)

Better extraction does not automatically mean faster review or safer
filings. Keep these questions separate:

| Question | Evidence needed |
|---|---|
| Did extraction read the source correctly? | Unchanged extraction compared with an independent, blinded reading of the source. |
| Does showing pre-fill help? | Randomized pre-fill shown versus hidden, with review time and independently audited final-filing errors. |
| What happened during review? | Assignment, actual exposure, actions and timing. An unchanged field does not prove it was inspected. |

Human review stays in place in both pilot groups. Shadow mode, where
extraction runs without being shown, can evaluate extraction but cannot
measure the effect of showing it. The model A/B demo compares extractors,
not reviewers using them.

## What exists here

The repository contains synthetic records, confidence scores, generated
answer keys, a sensitivity demonstration, and an initial event contract.
It does not contain real source images, reviewer logs or elapsed times,
independent human audit labels, randomized delivery or production
outcome data.

The synthetic demonstration keeps extraction fixed and copies eligible
wrong header values into a separate hypothetical filing with a selected
probability. Eligible values must be comparable and wrong against gold,
with a baseline filed value unchanged from truth. Even baseline changes
within comparator tolerance are left alone.

To compare assumptions with the same documents and random draws:

```bash
python run_demo.py --seed 42 --synthetic-review-acceptance 0 --no-open
python run_demo.py --seed 42 --synthetic-review-acceptance 1 --no-open
```

Each run replaces output in its chosen directories. Supply different
`--data-dir` and `--out-dir` values to keep both runs.

The original records remain unchanged within each run. Decisions,
probability, seed, draws, reasons and versions are written under
`out/synthetic_review/`; metadata is written even when no fields qualify.
These are synthetic truth-derived diagnostics, not reviewer telemetry.

The demonstration does not model confidence-dependent behaviour, edits
to correct values, missing/unparseable values, goods-item or structural
errors, reviewer variation, highlights, validation constraints or time.
It is not evidence of actual automation bias or time savings.

## Proposed event contract

`reviewer_events.py` defines a versioned event table intended for
append-only collection. It separates randomized assignment from actual
pre-fill exposure and records model/interface versions.

Supported events are `document_opened`, `prefill_exposed`,
`field_changed`, `field_cleared`, `interruption_started`,
`interruption_ended`, `document_submitted` and `document_abandoned`.

```python
import pandas as pd
from reviewer_events import summarize_review_events

events = pd.read_csv("review_events.csv", dtype="string")
workflow_rows = summarize_review_events(events)
```

`active_seconds_estimate` is elapsed time minus explicitly paired
interruptions, not proof of attention or complete timing. Missing or
ambiguous starts, submissions and interruptions remain visible in
`timing_status`, with duration missing rather than zero. Change counts
are descriptive, not measurements of correctness, effort or benefit.

The validator rejects undeclared columns, but cannot detect sensitive
content inside an allowed column or enforce append-only storage.
`state_ref` must be an opaque reference to a separately access-controlled
snapshot, never an actual value, URL or direct identifier. Keep
pseudonymous reviewer identifiers, assignment records, events and
sensitive snapshots under approved access, retention and privacy controls.

This contract does not implement randomization, audit sampling,
statistical inference or authorization to reduce review.

## Design a real pilot

1. **Agree the decision and outcomes first.** With operations and domain
   owners, define a worthwhile time benefit, a serious filing error, an
   acceptable quality difference and a stop rule. Pre-register outcomes,
   analysis and sample size. Do not borrow a non-inferiority margin from
   synthetic examples or interpret a non-significant difference as proof
   that quality stayed the same.
2. **Observe real review work.** With appropriate consent and safeguards,
   define start, submit, interruptions, abandonment and missing-data
   rules. Retain unchanged extraction, what was shown, actions, final
   values and model/interface versions. Minimize sensitive data and
   agree retention and any applicable employee-representation requirements.
3. **Randomize shown versus hidden pre-fill.** Account for document mix,
   reviewers, carryover and the randomization unit. Analyze assigned
   groups (intention-to-treat); retain actual exposure to describe
   delivery. Report the time distribution as well as the chosen summary.
   If testing confidence highlights, randomize that policy: comparing
   highlighted with unhighlighted fields does not isolate its effect.
4. **Independently audit both groups.** Draw a probability sample.
   Auditors read the source without seeing extraction, filing or group;
   use two auditors where feasible and record and adjudicate uncertainty.
   Compare the audited share of final filings with at least one serious
   error. Handle missing labels explicitly and account for sampling
   probabilities, reviewer clustering and study design in uncertainty
   estimates.
5. **Keep monitoring after the pilot.** Document mix, confidence and edit
   patterns are investigation signals, not proof of quality changes or
   complacency. Continue independent audits and use operationally
   justified alert rules.

Reviewer feedback, blinded audits, randomized comparisons and downstream
corrections offer complementary evidence. Later decisions about less
review require their own risk criteria and audit design.
