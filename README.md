# ClearSight

**A runnable lesson in measuring document-extraction quality.**

Imagine software that reads a customs document and fills in a form for a
person to review. Before relying on it, we need to know what it gets
wrong, whether its confidence scores are useful, and whether our way of
checking it can miss mistakes.

ClearSight explores those questions with simulated customs records and
deliberately introduced extraction errors. It produces a clickable HTML
report: start with a score, then inspect the values, goods lines and
comparison rules behind it.

This is a teaching prototype, not an extraction model or a production
platform. It does not train or run AI. It uses ordinary Python and pandas
to demonstrate evaluation principles that apply to OCR and language-model
extractors alike.

## The important trap: agreement is not correctness

The final filing is tempting to use as an answer key. But the person who
filed it may have accepted the extractor's mistake:

| Original document | Extracted value | Final filing | What happened |
|---|---|---|---|
| EUR 1,000 | EUR 100 | EUR 100 | They agree, but both are wrong. |
| EUR 1,000 | EUR 100 | EUR 1,000 | They disagree because the reviewer corrected the error. |

**Better agreement with the filing does not necessarily mean better
extraction or a better workflow.** Checking correctness requires an
independent reading of the original document. Generated data gives this
demo an answer key; real work needs independently audited examples.

## Run it and see the evidence

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python run_demo.py
```

The demo generates records, evaluates them and opens `out/report.html`.
Use `--no-open` to generate the report without launching a browser.
`data/` and `out/` are generated and gitignored.

Start with **"Synthetic demonstration: when review copies an error"**.
The extraction stays the same while hypothetical filings accept some
wrong values. Watch agreement improve without extraction improving, then
click a number to inspect its evidence. The assumed reviewer behaviour is
illustrative, not measured.

For a guided first session, follow the
[one-document walkthrough](docs/walkthrough.md). To compare two simulated
extractors on the same documents, run `python run_ab_demo.py`; it opens
`out/ab/comparison.html`.

## What this can and cannot tell you

| Question | What is available here | What is still needed for real work |
|---|---|---|
| Did extraction read the document correctly? | Comparisons against generated truth and filed records, including missing or extra goods lines. | Representative documents and an independently checked answer key. |
| Can confidence and validation checks warn us? | Examples of useful warnings and errors they miss. A completely omitted line has no confidence score. | Validation on real, independently labelled data. |
| Which extractor performs better? | A paired comparison on the same generated documents. | Actual model outputs, a trustworthy shared reference and a representative sample. |
| Does showing extraction help a person? | A proposed review-event contract and guidance for a pilot. | A randomized study of time and independently audited final-filing errors. |

The report's **"clean" means no important mismatch was detected against
the chosen reference**, not that the document was verified. Unresolved
comparisons are counted separately. Synthetic results do not establish
real accuracy, time savings or permission to remove human review.

## Go deeper when you need it

| Guide | Read it to... |
|---|---|
| [One-document walkthrough](docs/walkthrough.md) | Explain one failure and trace a report number back to its rows. |
| [Evaluation methodology](docs/methodology.md) | Understand references, metrics, confidence, uncertainty and known limitations. |
| [Code architecture](docs/architecture.md) | Follow the implementation, add a source or compare real model exports. |
| [Reviewer pilot](docs/reviewer-pilot.md) | Plan how to test whether pre-fill helps, separately from extraction accuracy. |
| [Outputs and glossary](docs/reference.md) | Look up generated files and unfamiliar terms. |

Keep the measurement principles even if you replace the implementation:
readable evidence, explicit comparison rules, visible missing data, and
honest limits on what a score can establish.
