"""Compare several extraction models over the SAME documents.

Each model is evaluated on its own (evaluate.evaluate, each against the
same reference records); this module lines the results up.  Because every
model saw the same documents, the comparison can be PAIRED -- scored
document by document -- which is far more decisive than comparing two
separate rates:

- the four-way split (both models clean / only A / only B / neither)
  shows exactly where the models actually differ;
- the difference in clean rates gets a likely range from a paired
  bootstrap (uncertainty.paired_bootstrap_diff_ci), which is much tighter
  than two independent ranges because the documents both models get right
  or wrong together cancel out;
- the win/loss split among the disagreement documents gets a plain
  "chance this is luck" number (uncertainty.chance_split_is_luck).

Fairness is checked, not assumed: the models must have been scored over
the same documents, with the same comparator and aligner versions.  A
violation lands in ModelComparison.warnings rather than passing silently.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from comparators import MISMATCH
from confidence_quality import ConfidenceQualitySummary
from evaluate import Evaluation
from uncertainty import chance_split_is_luck, paired_bootstrap_diff_ci, wilson_interval

STRAIGHT_THROUGH_THRESHOLD = 0.80  # the operating point quoted in summaries


@dataclass(frozen=True)
class HeadToHead:
    """One pair of models scored document by document."""

    model_a: str
    model_b: str
    documents: int
    both_clean: int
    only_a_clean: int          # A's wins: clean where B was not
    only_b_clean: int          # B's wins
    neither_clean: int
    clean_rate_diff: float     # A minus B; positive = A better
    diff_ci: tuple[float, float]
    chance_split_is_luck: float
    only_a_doc_ids: tuple[str, ...]
    only_b_doc_ids: tuple[str, ...]


@dataclass
class ModelComparison:
    reference: str                       # what everyone was scored against
    summary: pd.DataFrame                # one row per model
    per_field: pd.DataFrame              # mismatch rate per field, per model
    head_to_head: list[HeadToHead]       # every model pair
    evaluations: dict[str, Evaluation]   # kept for the report's drill-downs
    warnings: list[str] = field(default_factory=list)


def _escaped_rate_at(evaluation: Evaluation, threshold: float) -> float:
    st = evaluation.straight_through
    row = st[(st["threshold"] - threshold).abs() < 1e-9]
    return float(row["escaped_error_rate"].iloc[0]) if len(row) else math.nan


def _fairness_warnings(evaluations: dict[str, Evaluation]) -> list[str]:
    warnings = []
    doc_sets = {model: set(ev.doc_summary["doc_id"]) for model, ev in evaluations.items()}
    reference_docs = next(iter(doc_sets.values()))
    if any(docs != reference_docs for docs in doc_sets.values()):
        warnings.append(
            "The models did not cover exactly the same documents; the "
            "head-to-head is computed on the overlap only."
        )
    rule_sets = {
        model: set(
            zip(ev.comparisons["comparator"], ev.comparisons["comparator_version"])
        )
        for model, ev in evaluations.items()
    }
    reference_rules = next(iter(rule_sets.values()))
    if any(rules != reference_rules for rules in rule_sets.values()):
        warnings.append(
            "The models were scored under different comparator/aligner "
            "versions -- this is not a like-for-like comparison."
        )
    return warnings


def _summary_frame(
    evaluations: dict[str, Evaluation],
    confidence: dict[str, ConfidenceQualitySummary] | None,
) -> pd.DataFrame:
    rows = []
    for model, ev in evaluations.items():
        docs = ev.doc_summary
        low, high = wilson_interval(int(docs["clean"].sum()), len(docs))
        critical = ev.comparisons[
            (ev.comparisons["status"] == MISMATCH)
            & (ev.comparisons["tier"] == ev.critical_tier)
        ]
        quality = (confidence or {}).get(model)
        rows.append(
            {
                "model": model,
                "documents": len(docs),
                "clean_document_rate": float(docs["clean"].mean()),
                "clean_ci_low": low,
                "clean_ci_high": high,
                "critical_mismatches": len(critical),
                "missed_items": int(docs["missed_items"].sum()),
                "spurious_items": int(docs["spurious_items"].sum()),
                "escaped_rate_at_080": _escaped_rate_at(ev, STRAIGHT_THROUGH_THRESHOLD),
                "auroc": quality.auroc if quality else math.nan,
                "ece": quality.ece if quality else math.nan,
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("clean_document_rate", ascending=False)
        .reset_index(drop=True)
    )


def _per_field_frame(evaluations: dict[str, Evaluation]) -> pd.DataFrame:
    """Mismatch rate per field with one column per model, sorted by how far
    the models disagree -- the fields at the top are where the choice of
    model actually matters."""
    merged: pd.DataFrame | None = None
    for model, ev in evaluations.items():
        frame = ev.per_field[["level", "field", "tier", "mismatches", "n_comparisons"]].copy()
        frame[model] = frame["mismatches"] / frame["n_comparisons"].where(
            frame["n_comparisons"] > 0
        )
        frame = frame[["level", "field", "tier", model]]
        merged = frame if merged is None else merged.merge(
            frame, on=["level", "field", "tier"], how="outer"
        )
    models = list(evaluations)
    merged["spread"] = merged[models].max(axis=1) - merged[models].min(axis=1)
    return merged.sort_values("spread", ascending=False).reset_index(drop=True)


def _head_to_head(
    model_a: str, ev_a: Evaluation, model_b: str, ev_b: Evaluation, seed: int
) -> HeadToHead:
    clean_a = ev_a.doc_summary.set_index("doc_id")["clean"]
    clean_b = ev_b.doc_summary.set_index("doc_id")["clean"]
    shared = sorted(set(clean_a.index) & set(clean_b.index))
    a = clean_a.loc[shared]
    b = clean_b.loc[shared]
    only_a = a & ~b
    only_b = ~a & b
    return HeadToHead(
        model_a=model_a,
        model_b=model_b,
        documents=len(shared),
        both_clean=int((a & b).sum()),
        only_a_clean=int(only_a.sum()),
        only_b_clean=int(only_b.sum()),
        neither_clean=int((~a & ~b).sum()),
        clean_rate_diff=float(a.mean() - b.mean()) if shared else math.nan,
        diff_ci=paired_bootstrap_diff_ci(list(a), list(b), seed=seed),
        chance_split_is_luck=chance_split_is_luck(int(only_a.sum()), int(only_b.sum())),
        only_a_doc_ids=tuple(only_a[only_a].index),
        only_b_doc_ids=tuple(only_b[only_b].index),
    )


def compare(
    evaluations: dict[str, Evaluation],
    reference: str,
    confidence: dict[str, ConfidenceQualitySummary] | None = None,
    seed: int = 1,
) -> ModelComparison:
    """Line up per-model evaluations that all used `reference` as their
    answer key (e.g. "the filed records" or "the gold truth")."""
    if len(evaluations) < 2:
        raise ValueError("comparing models needs at least two evaluations")
    return ModelComparison(
        reference=reference,
        summary=_summary_frame(evaluations, confidence),
        per_field=_per_field_frame(evaluations),
        head_to_head=[
            _head_to_head(model_a, evaluations[model_a], model_b, evaluations[model_b], seed)
            for model_a, model_b in itertools.combinations(evaluations, 2)
        ],
        evaluations=dict(evaluations),
        warnings=_fairness_warnings(evaluations),
    )


def write_outputs(comparison: ModelComparison, out_dir: str | Path) -> dict[str, str]:
    """CSV per frame, so the comparison can be re-derived and inspected."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    head_to_head = pd.DataFrame(
        [
            {
                "model_a": h.model_a,
                "model_b": h.model_b,
                "documents": h.documents,
                "both_clean": h.both_clean,
                "only_a_clean": h.only_a_clean,
                "only_b_clean": h.only_b_clean,
                "neither_clean": h.neither_clean,
                "clean_rate_diff": h.clean_rate_diff,
                "diff_ci_low": h.diff_ci[0],
                "diff_ci_high": h.diff_ci[1],
                "chance_split_is_luck": h.chance_split_is_luck,
            }
            for h in comparison.head_to_head
        ]
    )
    frames = {
        "comparison_summary.csv": comparison.summary,
        "head_to_head.csv": head_to_head,
        "per_field_comparison.csv": comparison.per_field,
    }
    paths = {}
    for filename, frame in frames.items():
        path = out_dir / filename
        frame.to_csv(path, index=False)
        paths[filename] = str(path)
    return paths
