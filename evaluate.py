"""Evaluate extracted records against filed records.

The whole module is built around one idea: every aggregate number must be
traceable to rows a human can inspect.  So the first thing evaluate()
produces is the `comparisons` DataFrame -- one row per compared thing --
and every metric after that is a plain aggregation of that frame.  If a
number in the report looks wrong, filter field_comparisons.csv and read
the rows.

Rows come at three levels (the `level` column):
  header     one row per (document, header field)
  item       one row per (aligned item pair, item field); carries both
             item numbers, the alignment score and the aligner's stated
             basis for the pairing
  structure  one row per unmatched goods item: field "missed_item" for a
             filed item nothing was extracted for, "spurious_item" for an
             extracted item nothing was filed for.  Their tier comes from
             field_tiers.yaml, so a missed line makes a document not clean
             exactly as configured, through the same frame as everything
             else.

What "filed" means here: the record the downstream system ended up with.
That is NOT ground truth -- see the README for the ways this is knowingly
wrong (legitimate post-filing amendments, filed values that are themselves
incorrect, and the alignment's own assumptions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

import comparators
from alignment import ALIGNER_NAME, ALIGNER_VERSION, align_items, describe_item
from canonical import CanonicalRecord
from comparators import MATCH, MISMATCH, Comparator

# Confidence thresholds examined for the straight-through estimate.
DEFAULT_THRESHOLDS = [0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 0.99]

CALIBRATION_BUCKETS = [f"{i / 10:.1f}-{(i + 1) / 10:.1f}" for i in range(10)]

HEADER, ITEM, STRUCTURE = "header", "item", "structure"

# Declared once so the comparisons frame has these columns even when no
# documents join at all (see _compare_all) -- every aggregation below and
# the report rely on them being present.
COMPARISON_COLUMNS = [
    "doc_id", "level", "field", "tier", "country", "source_system",
    "item_no_extracted", "item_no_filed", "alignment_score", "alignment_basis",
    "extracted_value", "filed_value", "extracted_present", "filed_present",
    "status", "reason", "confidence", "comparator", "comparator_version",
]
DOC_SUMMARY_COLUMNS = [
    "doc_id", "country", "source_system", "not_comparable",
    "extracted_items", "filed_items", "paired_items", "missed_items",
    "spurious_items", "min_critical_confidence", "critical_mismatches", "clean",
]


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
    header_fields: dict[str, FieldSpec]
    item_fields: dict[str, FieldSpec]
    missed_item_tier: int
    spurious_item_tier: int
    alignment_min_score: float


def load_field_tiers(path: str | Path) -> TiersConfig:
    """Load field_tiers.yaml and build the configured comparators.

    Criticality lives entirely in the YAML file; this function just turns
    it into objects."""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    def build(section: dict) -> dict[str, FieldSpec]:
        return {
            name: FieldSpec(
                name=name,
                tier=int(spec["tier"]),
                comparator=comparators.from_config(spec["comparator"], spec.get("params")),
            )
            for name, spec in section.items()
        }

    structure = raw.get("structure", {})
    return TiersConfig(
        version=int(raw.get("version", 2)),
        critical_tier=int(raw.get("critical_tier", 1)),
        header_fields=build(raw["header_fields"]),
        item_fields=build(raw["item_fields"]),
        missed_item_tier=int(structure.get("missed_item_tier", 1)),
        spurious_item_tier=int(structure.get("spurious_item_tier", 1)),
        alignment_min_score=float(raw.get("alignment", {}).get("min_score", 0.35)),
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
    comparisons: pd.DataFrame       # one row per compared thing (see module docstring)
    doc_summary: pd.DataFrame       # one row per document
    per_field: pd.DataFrame         # precision / recall per field
    breakdown: pd.DataFrame         # mismatches by country x source system
    calibration: pd.DataFrame       # observed accuracy per confidence decile
    straight_through: pd.DataFrame  # auto-accept share / escaped errors per threshold
    alignment_summary: pd.DataFrame # item pairing outcomes per source system


def evaluate(
    extracted: list[CanonicalRecord],
    filed: list[CanonicalRecord],
    tiers: TiersConfig,
    thresholds: list[float] = DEFAULT_THRESHOLDS,
) -> Evaluation:
    comparisons, doc_stats, unmatched_extracted, unmatched_filed = _compare_all(
        extracted, filed, tiers
    )
    doc_summary = _summarise_documents(comparisons, doc_stats, tiers.critical_tier)
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
        alignment_summary=_alignment_summary(doc_summary),
    )


# ---------------------------------------------------------------------------
# step 1: the row-level comparison frame
# ---------------------------------------------------------------------------


def _compare_all(
    extracted: list[CanonicalRecord],
    filed: list[CanonicalRecord],
    tiers: TiersConfig,
) -> tuple[pd.DataFrame, dict[str, dict], list[str], list[str]]:
    """Join on doc_id, align goods items, and apply the configured
    comparator to every header and item field."""
    filed_by_id = {r.doc_id: r for r in filed}
    extracted_ids = {r.doc_id for r in extracted}
    unmatched_extracted = sorted(extracted_ids - filed_by_id.keys())
    unmatched_filed = sorted(filed_by_id.keys() - extracted_ids)

    rows: list[dict] = []
    doc_stats: dict[str, dict] = {}
    for record in extracted:
        filed_record = filed_by_id.get(record.doc_id)
        if filed_record is None:
            continue  # counted in unmatched_extracted, nothing to compare
        country = filed_record.country or record.country

        def base_row(level: str, field: str, tier: int) -> dict:
            return {
                "doc_id": record.doc_id,
                "level": level,
                "field": field,
                "tier": tier,
                # country of filing comes from the filed record (the
                # authoritative side); source_system from the extracted
                # one (that is what we are measuring).
                "country": country,
                "source_system": record.source_system,
                "item_no_extracted": None,
                "item_no_filed": None,
                "alignment_score": None,
                "alignment_basis": None,
            }

        # header fields ---------------------------------------------------
        for spec in tiers.header_fields.values():
            extracted_value = getattr(record, spec.name)
            filed_value = getattr(filed_record, spec.name)
            result = spec.comparator.compare(extracted_value, filed_value)
            rows.append(
                base_row(HEADER, spec.name, spec.tier)
                | {
                    "extracted_value": extracted_value,
                    "filed_value": filed_value,
                    "extracted_present": not comparators.is_missing(extracted_value),
                    "filed_present": not comparators.is_missing(filed_value),
                    "status": result.status,
                    "reason": result.reason,
                    "confidence": record.extraction_confidence.get(spec.name),
                    "comparator": spec.comparator.name,
                    "comparator_version": spec.comparator.VERSION,
                }
            )

        # goods items: align first, then compare within each pair ---------
        aligned = align_items(record.items, filed_record.items, tiers.alignment_min_score)
        for pair in aligned.pairs:
            for spec in tiers.item_fields.values():
                extracted_value = getattr(pair.extracted, spec.name)
                filed_value = getattr(pair.filed, spec.name)
                result = spec.comparator.compare(extracted_value, filed_value)
                rows.append(
                    base_row(ITEM, spec.name, spec.tier)
                    | {
                        "item_no_extracted": pair.extracted.item_number,
                        "item_no_filed": pair.filed.item_number,
                        "alignment_score": pair.score,
                        "alignment_basis": pair.basis,
                        "extracted_value": extracted_value,
                        "filed_value": filed_value,
                        "extracted_present": not comparators.is_missing(extracted_value),
                        "filed_present": not comparators.is_missing(filed_value),
                        "status": result.status,
                        "reason": result.reason,
                        "confidence": pair.extracted.extraction_confidence.get(spec.name),
                        "comparator": spec.comparator.name,
                        "comparator_version": spec.comparator.VERSION,
                    }
                )
        for item in aligned.unmatched_filed:
            rows.append(
                base_row(STRUCTURE, "missed_item", tiers.missed_item_tier)
                | {
                    "item_no_filed": item.item_number,
                    "extracted_value": None,
                    "filed_value": describe_item(item),
                    "extracted_present": False,
                    "filed_present": True,
                    "status": MISMATCH,
                    "reason": (
                        f"filed {describe_item(item)} has no extracted counterpart "
                        f"scoring >= {tiers.alignment_min_score} -- a dropped or merged line?"
                    ),
                    "confidence": None,
                    "comparator": ALIGNER_NAME,
                    "comparator_version": ALIGNER_VERSION,
                }
            )
        for item in aligned.unmatched_extracted:
            rows.append(
                base_row(STRUCTURE, "spurious_item", tiers.spurious_item_tier)
                | {
                    "item_no_extracted": item.item_number,
                    "extracted_value": describe_item(item),
                    "filed_value": None,
                    "extracted_present": True,
                    "filed_present": False,
                    "status": MISMATCH,
                    "reason": (
                        f"extracted {describe_item(item)} has no filed counterpart "
                        f"scoring >= {tiers.alignment_min_score} -- an invented line "
                        "(a subtotal or footer read as goods)?"
                    ),
                    "confidence": None,
                    "comparator": ALIGNER_NAME,
                    "comparator_version": ALIGNER_VERSION,
                }
            )

        doc_stats[record.doc_id] = {
            "extracted_items": len(record.items),
            "filed_items": len(filed_record.items),
            "paired_items": len(aligned.pairs),
            "missed_items": len(aligned.unmatched_filed),
            "spurious_items": len(aligned.unmatched_extracted),
            "min_critical_confidence": _min_critical_confidence(record, tiers),
        }

    # Explicit columns so an empty join (nothing matched on doc_id) still
    # yields a well-formed frame instead of crashing downstream.
    comparisons = pd.DataFrame(rows, columns=COMPARISON_COLUMNS)
    comparisons["confidence"] = pd.to_numeric(comparisons["confidence"], errors="coerce")
    comparisons["confidence_bucket"] = comparisons["confidence"].map(_bucket_label)
    return comparisons, doc_stats, unmatched_extracted, unmatched_filed


def _min_critical_confidence(record: CanonicalRecord, tiers: TiersConfig) -> float:
    """The number an auto-accept gate would act on, computed EX ANTE from
    the extracted record alone: the minimum confidence over every
    critical-tier header field and every critical-tier field of every
    extracted item.  NaN when any of those scores is missing -- such a
    document can never auto-accept.

    Deliberately blind to alignment: a document whose extraction dropped a
    goods item can look perfectly confident here.  That is the point --
    the straight-through table must show what a confidence gate would
    really let through."""
    scores: list[float] = []
    for spec in tiers.header_fields.values():
        if spec.tier == tiers.critical_tier:
            scores.append(record.extraction_confidence.get(spec.name))
    for item in record.items:
        for spec in tiers.item_fields.values():
            if spec.tier == tiers.critical_tier:
                scores.append(item.extraction_confidence.get(spec.name))
    if not scores or any(score is None for score in scores):
        return math.nan
    return min(scores)


def _bucket_label(confidence: float) -> str | None:
    if pd.isna(confidence):
        return None
    index = min(int(confidence * 10), 9)
    return CALIBRATION_BUCKETS[index]


# ---------------------------------------------------------------------------
# step 2: aggregations (each one a plain rollup of `comparisons`)
# ---------------------------------------------------------------------------


def _summarise_documents(
    comparisons: pd.DataFrame, doc_stats: dict[str, dict], critical_tier: int
) -> pd.DataFrame:
    """One row per document: mismatch counts per tier, item alignment
    outcomes, the ex-ante minimum critical confidence, and the clean flag
    that feeds the headline."""
    if comparisons.empty:
        return pd.DataFrame(columns=DOC_SUMMARY_COLUMNS)
    summary = comparisons.groupby("doc_id").agg(
        country=("country", "first"),
        source_system=("source_system", "first"),
        not_comparable=("status", lambda s: int((s == comparators.NOT_COMPARABLE).sum())),
    )
    for tier in sorted(comparisons["tier"].unique()):
        tier_rows = comparisons[comparisons["tier"] == tier]
        counts = tier_rows.groupby("doc_id")["status"].agg(lambda s: int((s == MISMATCH).sum()))
        summary[f"tier{tier}_mismatches"] = counts.reindex(summary.index).fillna(0).astype(int)

    stats = pd.DataFrame.from_dict(doc_stats, orient="index")
    summary = summary.join(stats)
    summary["critical_mismatches"] = summary[f"tier{critical_tier}_mismatches"]
    summary["clean"] = summary["critical_mismatches"] == 0
    return summary.reset_index().rename(columns={"index": "doc_id"})


def _per_field_metrics(comparisons: pd.DataFrame, tiers: TiersConfig) -> pd.DataFrame:
    """Per-field precision and recall, header and item fields alike (item
    fields aggregate over all aligned pairs).  Structure rows are excluded:
    a missed item has no precision/recall semantics -- it is counted in the
    alignment summary and in every mismatch rollup instead.

    Definitions (deliberately simple; see README):
      precision  of the rows where the tool extracted a value AND the filed
                 side has one, the share that match.  "When the tool writes
                 a value, how often does it agree with what was filed?"
      recall     of the rows where the filed side has a value, the share
                 where the tool extracted a matching one.  A value the tool
                 failed to extract hurts recall, not precision.
    """
    columns = [
        "level", "field", "tier", "comparator", "n_comparisons", "matches",
        "mismatches", "missing_extracted", "unparseable", "precision", "recall",
    ]
    if comparisons.empty:
        return pd.DataFrame(columns=columns)
    specs = {**tiers.header_fields, **tiers.item_fields}
    field_rows = comparisons[comparisons["level"] != STRUCTURE]
    rows = []
    for (level, field, tier), group in field_rows.groupby(["level", "field", "tier"], sort=False):
        both_present = int((group["extracted_present"] & group["filed_present"]).sum())
        filed_present = int(group["filed_present"].sum())
        matches = int((group["status"] == MATCH).sum())
        mismatches = int((group["status"] == MISMATCH).sum())
        rows.append(
            {
                "level": level,
                "field": field,
                "tier": tier,
                "comparator": specs[field].comparator.describe(),
                "n_comparisons": len(group),
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
    return pd.DataFrame(rows).sort_values(["tier", "level", "field"]).reset_index(drop=True)


def _mismatch_breakdown(
    comparisons: pd.DataFrame, doc_summary: pd.DataFrame, critical_tier: int
) -> pd.DataFrame:
    """Mismatch counts by country of filing and extraction source system."""
    columns = [
        "country", "source_system", "documents", "clean_document_rate",
        "mismatches", "critical_mismatches", "critical_mismatch_rate",
    ]
    if comparisons.empty:
        return pd.DataFrame(columns=columns)
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
    calibration, and structure rows carry no confidence at all.  If the
    tool's confidence means anything, accuracy should climb with the
    bucket."""
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

    Two honesty notes.  "Escaped errors" are documents that disagree with
    what was eventually filed, so legitimate post-filing amendments count
    as escapes -- treat the rate as an upper bound.  And the gate sees only
    per-field confidence: a document whose extraction silently DROPPED a
    goods item still auto-accepts (the missing line has no score to be low),
    which is exactly how a real confidence gate fails."""
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


def _alignment_summary(doc_summary: pd.DataFrame) -> pd.DataFrame:
    """Item pairing outcomes per source system: how many goods items each
    side had, how many paired, how many were missed or spurious."""
    columns = [
        "source_system", "documents", "extracted_items", "filed_items",
        "paired_items", "missed_items", "spurious_items", "docs_with_structure_defects",
    ]
    if doc_summary.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for system, group in doc_summary.groupby("source_system"):
        rows.append(
            {
                "source_system": system,
                "documents": len(group),
                "extracted_items": int(group["extracted_items"].sum()),
                "filed_items": int(group["filed_items"].sum()),
                "paired_items": int(group["paired_items"].sum()),
                "missed_items": int(group["missed_items"].sum()),
                "spurious_items": int(group["spurious_items"].sum()),
                "docs_with_structure_defects": int(
                    ((group["missed_items"] > 0) | (group["spurious_items"] > 0)).sum()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("source_system").reset_index(drop=True)


# ---------------------------------------------------------------------------
# cross-regime analyses
# ---------------------------------------------------------------------------


@dataclass
class LeadingIndicators:
    """How predictive the blind signals (validity failures, low confidence)
    are of the critical mismatches only visible after filing."""

    frame: pd.DataFrame            # one row per signal: docs, hit rate, lift
    masks: dict[str, pd.Series]    # signal -> boolean mask over doc_summary rows
    confidence_floor: float


def leading_indicators(
    doc_summary: pd.DataFrame,
    validity_results: pd.DataFrame,
    confidence_floor: float = 0.8,
) -> LeadingIndicators:
    """The blind-regime question: in production, before any filed record
    exists, the only per-document warning signs are a failed validity rule
    and a low confidence score.  How much do they tell you about the
    errors the filing comparison will eventually reveal?  "Lift" is plain
    division: a flagged group's error rate over the everyone rate, so 2.0
    means twice as likely.  The "neither signal" row is the risk no blind
    gate can see -- fabricated-but-plausible values chief among it."""
    failed_docs = set(validity_results.loc[~validity_results["passed"], "doc_id"])
    validity_mask = doc_summary["doc_id"].isin(failed_docs)
    confidence = doc_summary["min_critical_confidence"]
    confidence_mask = confidence.isna() | (confidence < confidence_floor)
    has_critical_error = doc_summary["critical_mismatches"] > 0
    base_rate = float(has_critical_error.mean()) if len(doc_summary) else math.nan

    masks = {
        "all documents (base rate)": pd.Series(True, index=doc_summary.index),
        "any validity failure": validity_mask,
        f"min tier-1 confidence < {confidence_floor} or missing": confidence_mask,
        "either blind signal": validity_mask | confidence_mask,
        "neither signal (looks clean ex ante)": ~(validity_mask | confidence_mask),
    }
    rows = []
    for signal, mask in masks.items():
        selected = doc_summary[mask]
        hits = int((selected["critical_mismatches"] > 0).sum())
        rate = hits / len(selected) if len(selected) else math.nan
        rows.append(
            {
                "signal": signal,
                "documents": len(selected),
                "docs_with_critical_mismatch": hits,
                "critical_mismatch_rate": rate,
                "lift_vs_base": rate / base_rate if base_rate else math.nan,
            }
        )
    return LeadingIndicators(
        frame=pd.DataFrame(rows), masks=masks, confidence_floor=confidence_floor
    )


def proxy_disagreement(
    proxy_comparisons: pd.DataFrame, gold_comparisons: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split the proxy's verdicts by what the gold standard says.

    Returns (false_alarms, hidden_errors):
      false_alarms   mismatch vs filed but match vs gold -- the proxy's
                     noise, overwhelmingly post-filing amendments.
      hidden_errors  match vs filed but mismatch vs gold -- extraction and
                     filing wrong in the same way; near zero here, and the
                     population that grows once auto-accepted extractions
                     start being filed verbatim (the feedback loop).

    Joins field rows on the extracted side's identity (doc, level, field,
    extracted item number), which both regimes share; structure rows have
    no such shared key and are excluded.
    """

    def keyed(frame: pd.DataFrame) -> pd.DataFrame:
        fields = frame[frame["level"] != STRUCTURE].copy()
        # None item numbers (header rows) must still join: NaN never equals
        # NaN in a pandas merge, so use a sentinel.
        fields["item_key"] = (
            pd.to_numeric(fields["item_no_extracted"], errors="coerce").fillna(-1).astype(int)
        )
        return fields

    keys = ["doc_id", "level", "field", "item_key"]
    proxy_side = keyed(proxy_comparisons)
    gold_side = keyed(gold_comparisons)[keys + ["status", "filed_value", "reason"]].rename(
        columns={"status": "status_gold", "filed_value": "gold_value", "reason": "reason_gold"}
    )
    merged = proxy_side.merge(gold_side, on=keys, how="inner")
    false_alarms = merged[
        (merged["status"] == MISMATCH) & (merged["status_gold"] == MATCH)
    ].drop(columns=["item_key"])
    hidden_errors = merged[
        (merged["status"] == MATCH) & (merged["status_gold"] == MISMATCH)
    ].drop(columns=["item_key"])
    return false_alarms, hidden_errors


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
        "alignment_summary.csv": evaluation.alignment_summary,
    }
    paths = {}
    for filename, frame in frames.items():
        path = out_dir / filename
        frame.to_csv(path, index=False)
        paths[filename] = str(path)
    return paths
