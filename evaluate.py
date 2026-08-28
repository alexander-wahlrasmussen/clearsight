"""Evaluate extracted records against filed records.

The whole module is built around one idea: every aggregate number must be
traceable to rows a human can inspect.  So the first thing evaluate()
produces is the `comparisons` DataFrame -- one row per (document, field)
with the extracted value, the filed value, the comparator's verdict and
its reason -- and every metric after that is a plain aggregation of that
frame.  If a number in the report looks wrong, filter field_comparisons.csv
and read the rows.

What "filed" means here: the record the downstream system ended up with.
That is NOT ground truth -- see the README for the two ways this is
knowingly wrong (legitimate post-filing amendments, and filed values that
are themselves incorrect).  This module measures agreement with the filed
record, nothing more.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

import comparators
from canonical import CanonicalRecord
from comparators import MATCH, MISMATCH, Comparator

# Confidence thresholds examined for the straight-through estimate.
DEFAULT_THRESHOLDS = [0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 0.99]

CALIBRATION_BUCKETS = [f"{i / 10:.1f}-{(i + 1) / 10:.1f}" for i in range(10)]


# ---------------------------------------------------------------------------
# configuration (field_tiers.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    name: str
    tier: int
    comparator: Comparator


@dataclass(frozen=True)
class TiersConfig:
    version: int
    critical_tier: int  # the tier whose mismatches make a document "not clean"
    fields: dict[str, FieldSpec]


def load_field_tiers(path: str | Path) -> TiersConfig:
    """Load field_tiers.yaml and build the configured comparators.

    Criticality lives entirely in the YAML file; this function just turns
    it into objects."""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    fields = {}
    for name, spec in raw["fields"].items():
        fields[name] = FieldSpec(
            name=name,
            tier=int(spec["tier"]),
            comparator=comparators.from_config(spec["comparator"], spec.get("params")),
        )
    return TiersConfig(
        version=int(raw.get("version", 1)),
        critical_tier=int(raw.get("critical_tier", 1)),
        fields=fields,
    )


# ---------------------------------------------------------------------------
# evaluation result
# ---------------------------------------------------------------------------


@dataclass
class Evaluation:
    """Everything the report needs.  `comparisons` is the source of truth;
    the other frames are aggregations of it (doc_summary additionally feeds
    the straight-through table)."""

    clean_document_rate: float
    critical_tier: int
    n_extracted: int
    n_filed: int
    n_joined: int
    unmatched_extracted: list[str]  # doc_ids extracted but never filed
    unmatched_filed: list[str]      # doc_ids filed but never extracted
    comparisons: pd.DataFrame       # one row per (document, field)
    doc_summary: pd.DataFrame       # one row per document
    per_field: pd.DataFrame         # precision / recall per field
    breakdown: pd.DataFrame         # mismatches by country x source system
    calibration: pd.DataFrame       # observed accuracy per confidence decile
    straight_through: pd.DataFrame  # auto-accept share / escaped errors per threshold


def evaluate(
    extracted: list[CanonicalRecord],
    filed: list[CanonicalRecord],
    tiers: TiersConfig,
    thresholds: list[float] = DEFAULT_THRESHOLDS,
) -> Evaluation:
    comparisons, unmatched_extracted, unmatched_filed = _compare_all(
        extracted, filed, tiers
    )
    doc_summary = _summarise_documents(comparisons, tiers.critical_tier)
    clean_rate = float(doc_summary["clean"].mean()) if len(doc_summary) else math.nan
    return Evaluation(
        clean_document_rate=clean_rate,
        critical_tier=tiers.critical_tier,
        n_extracted=len(extracted),
        n_filed=len(filed),
        n_joined=int(doc_summary.shape[0]),
        unmatched_extracted=unmatched_extracted,
        unmatched_filed=unmatched_filed,
        comparisons=comparisons,
        doc_summary=doc_summary,
        per_field=_per_field_metrics(comparisons, tiers),
        breakdown=_mismatch_breakdown(comparisons, doc_summary, tiers.critical_tier),
        calibration=_calibration_table(comparisons),
        straight_through=_straight_through(doc_summary, thresholds),
    )


# ---------------------------------------------------------------------------
# step 1: the row-level comparison frame
# ---------------------------------------------------------------------------


def _compare_all(
    extracted: list[CanonicalRecord],
    filed: list[CanonicalRecord],
    tiers: TiersConfig,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Join on doc_id and apply the configured comparator to every field."""
    filed_by_id = {r.doc_id: r for r in filed}
    extracted_ids = {r.doc_id for r in extracted}
    unmatched_extracted = sorted(extracted_ids - filed_by_id.keys())
    unmatched_filed = sorted(filed_by_id.keys() - extracted_ids)

    rows = []
    for record in extracted:
        filed_record = filed_by_id.get(record.doc_id)
        if filed_record is None:
            continue  # counted in unmatched_extracted, nothing to compare
        for spec in tiers.fields.values():
            extracted_value = getattr(record, spec.name)
            filed_value = getattr(filed_record, spec.name)
            result = spec.comparator.compare(extracted_value, filed_value)
            confidence = record.extraction_confidence.get(spec.name)
            rows.append(
                {
                    "doc_id": record.doc_id,
                    "field": spec.name,
                    "tier": spec.tier,
                    "extracted_value": extracted_value,
                    "filed_value": filed_value,
                    "extracted_present": not comparators.is_missing(extracted_value),
                    "filed_present": not comparators.is_missing(filed_value),
                    "status": result.status,
                    "reason": result.reason,
                    "confidence": confidence,
                    # country of filing comes from the filed record (the
                    # authoritative side); source_system from the extracted
                    # one (that is what we are measuring).
                    "country": filed_record.country or record.country,
                    "source_system": record.source_system,
                    "comparator": spec.comparator.name,
                    "comparator_version": spec.comparator.VERSION,
                }
            )
    comparisons = pd.DataFrame(rows)
    comparisons["confidence"] = pd.to_numeric(comparisons["confidence"], errors="coerce")
    comparisons["confidence_bucket"] = comparisons["confidence"].map(_bucket_label)
    return comparisons, unmatched_extracted, unmatched_filed


def _bucket_label(confidence: float) -> str | None:
    if pd.isna(confidence):
        return None
    index = min(int(confidence * 10), 9)
    return CALIBRATION_BUCKETS[index]


# ---------------------------------------------------------------------------
# step 2: aggregations (each one a plain rollup of `comparisons`)
# ---------------------------------------------------------------------------


def _summarise_documents(comparisons: pd.DataFrame, critical_tier: int) -> pd.DataFrame:
    """One row per document: mismatch counts per tier, the minimum confidence
    across critical-tier fields, and the clean flag that feeds the headline."""
    summary = comparisons.groupby("doc_id").agg(
        country=("country", "first"),
        source_system=("source_system", "first"),
        not_comparable=("status", lambda s: int((s == comparators.NOT_COMPARABLE).sum())),
    )
    for tier in sorted(comparisons["tier"].unique()):
        tier_rows = comparisons[comparisons["tier"] == tier]
        counts = tier_rows.groupby("doc_id")["status"].agg(lambda s: int((s == MISMATCH).sum()))
        summary[f"tier{tier}_mismatches"] = counts.reindex(summary.index).fillna(0).astype(int)

    critical_rows = comparisons[comparisons["tier"] == critical_tier]
    # min with skipna=False: if ANY critical field lacks a confidence score,
    # the document has no usable minimum and can never auto-accept.
    min_confidence = critical_rows.groupby("doc_id")["confidence"].agg(
        lambda s: s.min(skipna=False)
    )
    summary["min_critical_confidence"] = min_confidence.reindex(summary.index)
    summary["critical_mismatches"] = summary[f"tier{critical_tier}_mismatches"]
    summary["clean"] = summary["critical_mismatches"] == 0
    return summary.reset_index()


def _per_field_metrics(comparisons: pd.DataFrame, tiers: TiersConfig) -> pd.DataFrame:
    """Per-field precision and recall.

    Definitions (deliberately simple; see README):
      precision  of the rows where the tool extracted a value AND the filed
                 side has one, the share that match.  "When the tool writes
                 a value, how often does it agree with what was filed?"
      recall     of the rows where the filed side has a value, the share
                 where the tool extracted a matching one.  A value the tool
                 failed to extract hurts recall, not precision.
    """
    rows = []
    for (field, tier), group in comparisons.groupby(["field", "tier"], sort=False):
        both_present = int((group["extracted_present"] & group["filed_present"]).sum())
        filed_present = int(group["filed_present"].sum())
        matches = int((group["status"] == MATCH).sum())
        mismatches = int((group["status"] == MISMATCH).sum())
        rows.append(
            {
                "field": field,
                "tier": tier,
                "comparator": tiers.fields[field].comparator.describe(),
                "n_documents": len(group),
                "matches": matches,
                "mismatches": mismatches,
                "missing_extracted": int(
                    (~group["extracted_present"] & group["filed_present"]).sum()
                ),
                "unparseable": both_present - matches - mismatches,
                "precision": matches / both_present if both_present else math.nan,
                "recall": matches / filed_present if filed_present else math.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(["tier", "field"]).reset_index(drop=True)


def _mismatch_breakdown(
    comparisons: pd.DataFrame, doc_summary: pd.DataFrame, critical_tier: int
) -> pd.DataFrame:
    """Mismatch counts by country of filing and extraction source system."""
    rows = []
    for (country, system), group in comparisons.groupby(["country", "source_system"]):
        critical = group[group["tier"] == critical_tier]
        docs = doc_summary[
            (doc_summary["country"] == country) & (doc_summary["source_system"] == system)
        ]
        rows.append(
            {
                "country": country,
                "source_system": system,
                "documents": int(docs.shape[0]),
                "clean_document_rate": float(docs["clean"].mean()),
                "mismatches": int((group["status"] == MISMATCH).sum()),
                "critical_mismatches": int((critical["status"] == MISMATCH).sum()),
                "critical_mismatch_rate": float(
                    (critical["status"] == MISMATCH).mean()
                ),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(["country", "source_system"])
        .reset_index(drop=True)
    )


def _calibration_table(comparisons: pd.DataFrame) -> pd.DataFrame:
    """Bucket per-field confidence into deciles and show observed accuracy.

    Only rows with a confidence score AND a definite verdict (match or
    mismatch) participate; not-comparable rows tell us nothing about
    calibration.  If the tool's confidence means anything, accuracy should
    climb with the bucket."""
    scored = comparisons[
        comparisons["confidence"].notna()
        & comparisons["status"].isin([MATCH, MISMATCH])
    ]
    rows = []
    for bucket in CALIBRATION_BUCKETS:
        group = scored[scored["confidence_bucket"] == bucket]
        n = len(group)
        matches = int((group["status"] == MATCH).sum())
        rows.append(
            {
                "confidence_bucket": bucket,
                "n": n,
                "matches": matches,
                "observed_accuracy": matches / n if n else math.nan,
                "mean_confidence": float(group["confidence"].mean()) if n else math.nan,
            }
        )
    return pd.DataFrame(rows)


def _straight_through(doc_summary: pd.DataFrame, thresholds: list[float]) -> pd.DataFrame:
    """At each confidence threshold: if we auto-accepted every document whose
    critical-tier fields all have confidence >= threshold, what share of the
    workload goes straight through, and what critical error rate escapes
    with it?

    Honesty note: "escaped errors" are documents that disagree with what was
    eventually filed.  Some of those are legitimate post-filing amendments,
    not extraction errors, so this is an upper bound on the true escape rate.
    """
    n_docs = len(doc_summary)
    confidence = doc_summary["min_critical_confidence"]
    rows = []
    for threshold in thresholds:
        accepted = doc_summary[confidence.notna() & (confidence >= threshold)]
        n_accepted = len(accepted)
        escaped = int((accepted["critical_mismatches"] > 0).sum())
        rows.append(
            {
                "threshold": threshold,
                "auto_accepted_docs": n_accepted,
                "auto_accept_share": n_accepted / n_docs if n_docs else math.nan,
                "escaped_error_docs": escaped,
                "escaped_error_rate": escaped / n_accepted if n_accepted else math.nan,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def write_outputs(evaluation: Evaluation, out_dir: str | Path) -> dict[str, str]:
    """Write every frame to CSV so each report number can be re-derived and
    inspected with nothing but a spreadsheet."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "field_comparisons.csv": evaluation.comparisons,
        "doc_summary.csv": evaluation.doc_summary,
        "per_field.csv": evaluation.per_field,
        "breakdown.csv": evaluation.breakdown,
        "calibration.csv": evaluation.calibration,
        "straight_through.csv": evaluation.straight_through,
    }
    paths = {}
    for filename, frame in frames.items():
        path = out_dir / filename
        frame.to_csv(path, index=False)
        paths[filename] = str(path)
    return paths
