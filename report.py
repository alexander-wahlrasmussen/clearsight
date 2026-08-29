"""Single self-contained HTML report.

Design rule: every number on the page is a link, and clicking it opens the
actual rows that number was computed from (capped for page size; the full
row set is always in out/field_comparisons.csv).  No external assets, no
frameworks -- the file can be mailed around and opened anywhere.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from comparators import MISMATCH
from confidence_quality import ConfidenceQualitySummary
from evaluate import Evaluation, LeadingIndicators
from uncertainty import AuditEstimate

DRILL_ROW_CAP = 100

# Columns shown when drilling into comparison rows.
COMPARISON_COLUMNS = [
    "doc_id", "level", "field", "item_no_extracted", "item_no_filed", "tier",
    "extracted_value", "filed_value", "status", "reason", "confidence",
    "country", "source_system",
]
# For drills into item alignment outcomes, the pairing justification matters.
STRUCTURE_COLUMNS = [
    "doc_id", "field", "item_no_extracted", "item_no_filed",
    "extracted_value", "filed_value", "reason", "country", "source_system",
]
DOC_COLUMNS_BASE = ["doc_id", "country", "source_system"]


@dataclass
class ReportExtras:
    """Optional analyses beyond the core proxy evaluation.  Every field may
    be None; the corresponding report section simply does not render."""

    gold_evaluation: Evaluation | None = None      # same extraction scored vs truth
    false_alarms: pd.DataFrame | None = None       # mismatch vs filed, match vs gold
    hidden_errors: pd.DataFrame | None = None      # match vs filed, mismatch vs gold
    audit_estimates: list[AuditEstimate] = field(default_factory=list)
    leading: LeadingIndicators | None = None       # blind signals vs proxy errors
    confidence_summaries: list[ConfidenceQualitySummary] = field(default_factory=list)
    per_field_confidence: pd.DataFrame | None = None
    clean_rate_ci: tuple[float, float] | None = None       # proxy, bootstrap
    gold_clean_rate_ci: tuple[float, float] | None = None  # gold, bootstrap
    breakdown_ci: pd.DataFrame | None = None       # Wilson CI per country x system
    extra_notes: list[str] = field(default_factory=list)


def esc(value) -> str:
    return html.escape(str(value))


def _fmt(value) -> str:
    """Human formatting for one table cell."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "&ndash;"
    if isinstance(value, float):
        return esc(f"{value:.4g}" if abs(value) < 1000 else f"{value:,.2f}")
    return esc(value)


def _pct(value) -> str:
    if value is None or pd.isna(value):
        return "&ndash;"
    return f"{value * 100:.1f}%"


def _pct_with_ci(value, ci: tuple[float, float] | None) -> str:
    text = _pct(value)
    if ci is not None:
        text += f' <span class="ci">[{ci[0] * 100:.1f}&ndash;{ci[1] * 100:.1f}]</span>'
    return text


class _Drilldowns:
    """Collects the hidden row-level tables that the page's numbers link to."""

    def __init__(self):
        self._sections: list[str] = []
        self._count = 0

    def link(self, label, title: str, frame: pd.DataFrame, columns=None) -> str:
        """Register `frame` as a drill-down and return an <a> for `label`."""
        self._count += 1
        anchor = f"drill-{self._count}"
        total = len(frame)
        shown = frame.head(DRILL_ROW_CAP)
        note = (
            f"Showing first {DRILL_ROW_CAP} of {total} rows &mdash; "
            "the full data is in the out/ CSVs."
            if total > DRILL_ROW_CAP
            else f"{total} row(s)."
        )
        self._sections.append(
            f'<div class="drilldown" id="{anchor}">\n'
            f'  <div class="drill-head"><h3>{esc(title)}</h3>'
            f'  <button type="button" class="close">close</button></div>\n'
            f'  <p class="note">{note}</p>\n'
            f"  {_table(shown, columns)}\n"
            f"</div>"
        )
        return f'<a class="drill" href="#{anchor}">{esc(label)}</a>'

    def html(self) -> str:
        return "\n".join(self._sections)


def _table(frame: pd.DataFrame, columns=None) -> str:
    if columns is None:
        columns = list(frame.columns)
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    rows = []
    for _, row in frame.iterrows():
        cells = "".join(f"<td>{_fmt(row[c])}</td>" for c in columns)
        rows.append(f"<tr>{cells}</tr>")
    return (
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _raw_table(header_cells: list[str], body_rows: list[list[str]]) -> str:
    """Table from pre-rendered cell HTML (used when cells contain links)."""
    head = "".join(f"<th>{h}</th>" for h in header_cells)
    rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
        for cells in body_rows
    )
    return (
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def _headline(ev: Evaluation, dd: _Drilldowns, extras: ReportExtras) -> str:
    comparisons = ev.comparisons
    tier = ev.critical_tier
    dirty_docs = ev.doc_summary[~ev.doc_summary["clean"]]
    doc_cols = DOC_COLUMNS_BASE + [
        c for c in ev.doc_summary.columns if c.endswith("_mismatches")
    ] + ["missed_items", "spurious_items", "min_critical_confidence"]
    critical_mismatch_rows = comparisons[
        (comparisons["status"] == MISMATCH) & (comparisons["tier"] == tier)
    ]
    clean_sub = dd.link(
        f"{len(dirty_docs)} documents are not clean",
        f"Documents with at least one tier-{tier} mismatch",
        dirty_docs, doc_cols,
    )
    if extras.clean_rate_ci is not None:
        low, high = extras.clean_rate_ci
        clean_sub += (
            f' &middot; <span class="ci">95% CI {low * 100:.1f}&ndash;{high * 100:.1f}'
            " (bootstrap)</span>"
        )
    tiles = [
        (
            f"{ev.clean_document_rate * 100:.1f}%",
            f"clean document rate (zero tier-{tier} mismatches, vs filed)",
            clean_sub,
        ),
        (
            f"{len(critical_mismatch_rows):,}",
            f"tier-{tier} field mismatches",
            dd.link(
                "see the mismatching rows",
                f"All tier-{tier} mismatches",
                critical_mismatch_rows.sort_values("confidence", ascending=False),
                COMPARISON_COLUMNS,
            ),
        ),
        (
            f"{ev.n_joined:,}",
            "documents compared (extracted joined to filed on doc_id)",
            _join_note(ev, dd),
        ),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="num">{num}</div>'
        f'<div class="label">{esc(label)}</div><div class="sub">{sub}</div></div>'
        for num, label, sub in tiles
    )
    return f'<section><div class="tiles">{tile_html}</div></section>'


def _join_note(ev: Evaluation, dd: _Drilldowns) -> str:
    parts = [f"{ev.n_extracted:,} extracted / {ev.n_filed:,} filed"]
    if ev.unmatched_extracted:
        frame = pd.DataFrame({"doc_id": ev.unmatched_extracted})
        parts.append(dd.link(
            f"{len(frame)} extracted but never filed", "Extracted but never filed", frame
        ))
    if ev.unmatched_filed:
        frame = pd.DataFrame({"doc_id": ev.unmatched_filed})
        parts.append(dd.link(
            f"{len(frame)} filed but never extracted", "Filed but never extracted", frame
        ))
    return " &middot; ".join(parts)


def _escaped_rate_at(evaluation: Evaluation, threshold: float) -> float:
    st = evaluation.straight_through
    row = st[(st["threshold"] - threshold).abs() < 1e-9]
    return float(row["escaped_error_rate"].iloc[0]) if len(row) else float("nan")


def _regimes_section(ev: Evaluation, extras: ReportExtras, dd: _Drilldowns) -> str:
    gold = extras.gold_evaluation
    if gold is None:
        return ""
    tier = ev.critical_tier

    def critical_mismatches(evaluation: Evaluation) -> int:
        c = evaluation.comparisons
        return int(((c["status"] == MISMATCH) & (c["tier"] == tier)).sum())

    gold_n, proxy_n = critical_mismatches(gold), critical_mismatches(ev)
    gold_escape, proxy_escape = _escaped_rate_at(gold, 0.80), _escaped_rate_at(ev, 0.80)
    body = [
        [
            "clean document rate",
            _pct_with_ci(gold.clean_document_rate, extras.gold_clean_rate_ci),
            _pct_with_ci(ev.clean_document_rate, extras.clean_rate_ci),
            f"{(ev.clean_document_rate - gold.clean_document_rate) * 100:+.1f} pp",
        ],
        [
            f"tier-{tier} mismatch rows (fields + structure)",
            f"{gold_n:,}", f"{proxy_n:,}", f"{proxy_n - gold_n:+,}",
        ],
        [
            "straight-through @ 0.80: escaped error rate",
            _pct(gold_escape), _pct(proxy_escape),
            f"{(proxy_escape - gold_escape) * 100:+.1f} pp",
        ],
    ]
    table = _raw_table(
        ["metric", "vs gold (truth)", "vs filed (proxy)", "proxy bias"], body
    )

    disagreement = ""
    if extras.false_alarms is not None and extras.hidden_errors is not None:
        false_alarms = extras.false_alarms
        hidden = extras.hidden_errors
        fa_critical = int((false_alarms["tier"] == tier).sum())
        disagreement_cols = [
            "doc_id", "level", "field", "item_no_extracted", "tier",
            "extracted_value", "filed_value", "gold_value", "reason", "confidence",
        ]
        fa_link = dd.link(
            f"{len(false_alarms):,} false alarms ({fa_critical} tier-{tier})",
            "Proxy false alarms: mismatch vs filed, match vs gold",
            false_alarms, disagreement_cols,
        )
        hidden_link = dd.link(
            f"{len(hidden):,} hidden errors",
            "Hidden errors: match vs filed, mismatch vs gold",
            hidden, disagreement_cols,
        )
        disagreement = (
            f"<p>Splitting the proxy's field verdicts by what gold says: {fa_link} "
            "&mdash; the proxy's noise, overwhelmingly post-filing amendments &mdash; "
            f"and {hidden_link}, where extraction and filing were wrong the same way. "
            "The hidden-error count is near zero here because filings are still "
            "independent of extraction; it is the number that silently grows once "
            "auto-accepted extractions start being filed verbatim.</p>"
        )

    audit = ""
    if extras.audit_estimates:
        audit_body = []
        gold_docs = gold.doc_summary
        for estimate in extras.audit_estimates:
            sampled = gold_docs[gold_docs["doc_id"].isin(estimate.sampled_doc_ids)]
            audit_body.append([
                dd.link(f"{estimate.sample_size:,}",
                        f"Audit sample of {estimate.sample_size} documents (graded vs gold)",
                        sampled, DOC_COLUMNS_BASE + ["critical_mismatches", "clean"]),
                f"{estimate.clean_in_sample:,}",
                _pct(estimate.estimate),
                f"{estimate.ci_low * 100:.1f}&ndash;{estimate.ci_high * 100:.1f}%",
                f"{estimate.ci_width * 100:.1f} pp",
            ])
        audit_table = _raw_table(
            ["documents audited", "clean in sample", "estimated true clean rate",
             "95% CI (Wilson)", "CI width"],
            audit_body,
        )
        audit = (
            "<h3>The production substitute for gold: a graded audit sample</h3>"
            "<p>In production nobody has truth for the whole population; you buy it "
            "for a random sample and let the interval speak.  Here the &ldquo;human "
            f"grading&rdquo; is played by the gold file.  The full-population truth is "
            f"{_pct(gold.clean_document_rate)}; each interval below should usually "
            "cover it &mdash; and a 95% interval still misses one run in twenty, "
            "which is part of what it teaches.  The CI width column is the price "
            "list for annotation: precision scales with the square root of the "
            "sample.</p>"
            f"{audit_table}"
        )

    return (
        "<section><h2>Three ways to score the same extraction</h2>"
        "<p><strong>Truth</strong> (gold): only exists here because the data is "
        "synthetic; in production it is a human-graded audit sample.  "
        "<strong>Proxy</strong> (the filed record): available for every document, "
        "but days late and noisy &mdash; amendments count as errors, and "
        "agreement can hide shared mistakes.  <strong>Blind</strong> (validity "
        "rules + confidence): available instantly with no reference at all; its "
        "reach is measured in the leading-indicators section below.  The rest of "
        "this report scores against the proxy, because that is what production "
        "sees.</p>"
        f"{table}{disagreement}{audit}</section>"
    )


def _leading_indicators_section(ev: Evaluation, extras: ReportExtras, dd: _Drilldowns) -> str:
    leading = extras.leading
    if leading is None:
        return ""
    doc_cols = DOC_COLUMNS_BASE + [
        "critical_mismatches", "min_critical_confidence", "missed_items", "spurious_items",
    ]
    body = []
    for _, row in leading.frame.iterrows():
        signal = row["signal"]
        selected = ev.doc_summary[leading.masks[signal]]
        body.append([
            esc(signal),
            dd.link(f"{int(row['documents']):,}", f"Documents: {signal}",
                    selected, doc_cols),
            f"{int(row['docs_with_critical_mismatch']):,}",
            _pct(row["critical_mismatch_rate"]),
            "&ndash;" if pd.isna(row["lift_vs_base"]) else f"{row['lift_vs_base']:.1f}x",
        ])
    table = _raw_table(
        ["blind signal (known before filing)", "documents", "with tier-1 mismatch",
         "mismatch rate", "lift vs base"],
        body,
    )
    return (
        "<section><h2>Blind-regime leading indicators</h2>"
        "<p>In production the proxy arrives days after extraction.  Until then the "
        "only per-document signals are the validity rules and the confidence "
        "scores &mdash; so the operational question is how much of the eventual "
        "damage those signals point at.  The last row is the residual: documents "
        "that look perfectly clean ex ante and are wrong anyway.  Fabricated "
        "values &mdash; valid-looking, high-confidence, wrong &mdash; live "
        "almost entirely in that row.</p>"
        f"{table}</section>"
    )


def _confidence_quality_section(extras: ReportExtras, dd: _Drilldowns) -> str:
    if not extras.confidence_summaries:
        return ""
    body = [
        [
            esc(s.label), f"{s.n_scored:,}", _pct(s.accuracy), _fmt(s.mean_confidence),
            _fmt(s.ece), _fmt(s.brier), _fmt(s.auroc),
        ]
        for s in extras.confidence_summaries
    ]
    summary_table = _raw_table(
        ["scored against", "fields scored", "accuracy", "mean confidence",
         "ECE", "Brier", "AUROC"],
        body,
    )
    per_field = ""
    if extras.per_field_confidence is not None and extras.gold_evaluation is not None:
        gold_comparisons = extras.gold_evaluation.comparisons
        field_body = []
        for _, row in extras.per_field_confidence.iterrows():
            field_name = row["field"]
            mismatches = gold_comparisons[
                (gold_comparisons["field"] == field_name)
                & (gold_comparisons["status"] == MISMATCH)
                & gold_comparisons["confidence"].notna()
            ].sort_values("confidence", ascending=False)
            field_body.append([
                esc(field_name),
                esc(row["level"]),
                dd.link(f"{int(row['n_scored']):,}",
                        f"True errors on {field_name}, most confident first (vs gold)",
                        mismatches, COMPARISON_COLUMNS),
                _pct(row["accuracy"]),
                _fmt(row["mean_confidence"]),
                f"{row['calibration_gap'] * 100:+.1f} pp",
                _fmt(row["auroc"]),
            ])
        per_field = (
            "<h3>Per field (vs gold)</h3>"
            "<p>A pooled score hides that 0.9 on an HS code and 0.9 on a date can "
            "mean different things.  Positive gap = overconfident.  Click a count "
            "for that field's true errors, most confident first.</p>"
            + _raw_table(
                ["field", "level", "fields scored", "accuracy", "mean confidence",
                 "calibration gap", "AUROC"],
                field_body,
            )
        )
    return (
        "<section><h2>Is the confidence score any good?</h2>"
        "<p>Two different properties.  <strong>Calibration</strong> (ECE, Brier: "
        "lower is better): does 0.9 mean 90%?  Fixable after the fact by "
        "recalibration.  <strong>Discrimination</strong> (AUROC: 1.0 separates "
        "right from wrong perfectly, 0.5 is noise): do errors rank below correct "
        "fields at all?  Not fixable by any recalibration &mdash; and the "
        "auto-accept gate only uses the ranking, so AUROC decides whether a "
        "confidence gate can work.  Comparing the two rows shows label noise at "
        "work: against the noisy proxy the same score's discrimination always "
        "reads lower (AUROC, Brier), while apparent calibration can drift either "
        "way &mdash; here the noise nudges an underconfident score's ECE slightly "
        "down, flattering it.  Fabricated errors carry deliberately high "
        "confidence, which is what keeps AUROC away from 1.0 &mdash; and the "
        "fields fabrication targets are exactly the ones with the worst AUROC "
        "below.</p>"
        f"{summary_table}{per_field}</section>"
    )


def _alignment_section(ev: Evaluation, dd: _Drilldowns) -> str:
    comparisons = ev.comparisons
    structure = comparisons[comparisons["level"] == "structure"]
    body = []
    for _, row in ev.alignment_summary.iterrows():
        system = row["source_system"]
        missed = structure[
            (structure["field"] == "missed_item") & (structure["source_system"] == system)
        ]
        spurious = structure[
            (structure["field"] == "spurious_item") & (structure["source_system"] == system)
        ]
        defect_docs = ev.doc_summary[
            (ev.doc_summary["source_system"] == system)
            & ((ev.doc_summary["missed_items"] > 0) | (ev.doc_summary["spurious_items"] > 0))
        ]
        doc_cols = DOC_COLUMNS_BASE + [
            "extracted_items", "filed_items", "paired_items", "missed_items", "spurious_items",
        ]
        body.append([
            esc(system),
            f"{int(row['documents']):,}",
            f"{int(row['extracted_items']):,}",
            f"{int(row['filed_items']):,}",
            f"{int(row['paired_items']):,}",
            dd.link(f"{int(row['missed_items']):,}",
                    f"Filed items with no extracted counterpart: {system}",
                    missed, STRUCTURE_COLUMNS),
            dd.link(f"{int(row['spurious_items']):,}",
                    f"Extracted items with no filed counterpart: {system}",
                    spurious, STRUCTURE_COLUMNS),
            dd.link(f"{int(row['docs_with_structure_defects']):,}",
                    f"Documents with structural defects: {system}",
                    defect_docs, doc_cols),
        ])
    table = _raw_table(
        ["source system", "documents", "extracted items", "filed items",
         "paired", "missed items", "spurious items", "docs with defects"],
        body,
    )
    return (
        "<section><h2>Goods item alignment</h2>"
        "<p>Declarations are multi-item, and item numbers cannot be joined on "
        "&mdash; extraction and filing number lines independently.  Items are "
        "paired by content (HS code hierarchy, value, origin, quantity; see "
        "alignment.py), and every pairing carries its stated basis in the "
        "drill-downs.  A <em>missed</em> item was filed but never extracted "
        "(a dropped or merged line); a <em>spurious</em> item was extracted "
        "but never filed (a subtotal or footer read as goods).  Both count "
        "as mismatches at the tier configured in field_tiers.yaml.</p>"
        f"{table}</section>"
    )


def _straight_through_section(ev: Evaluation, dd: _Drilldowns) -> str:
    ds = ev.doc_summary
    confidence = ds["min_critical_confidence"]
    body = []
    for _, row in ev.straight_through.iterrows():
        threshold = row["threshold"]
        accepted = ds[confidence.notna() & (confidence >= threshold)]
        escaped = accepted[accepted["critical_mismatches"] > 0]
        doc_cols = DOC_COLUMNS_BASE + ["critical_mismatches", "min_critical_confidence"]
        body.append([
            f"{threshold:.2f}",
            dd.link(
                f"{int(row['auto_accepted_docs']):,}",
                f"Documents auto-accepted at threshold {threshold:.2f}",
                accepted, doc_cols,
            ),
            _pct(row["auto_accept_share"]),
            dd.link(
                f"{int(row['escaped_error_docs']):,}",
                f"Tier-{ev.critical_tier} errors escaping at threshold {threshold:.2f}",
                escaped, doc_cols,
            ),
            _pct(row["escaped_error_rate"]),
        ])
    table = _raw_table(
        ["confidence threshold", "auto-accepted docs", "share of all docs",
         f"escaped tier-{ev.critical_tier} error docs", "escaped error rate"],
        body,
    )
    return (
        "<section><h2>Straight-through processing estimate</h2>"
        "<p>If every document whose critical-tier fields all carry at least this "
        "confidence were accepted without human review: how much goes straight "
        "through, and how many bad documents ride along.  Two honest caveats. "
        "Escapes are measured against the filed record, so legitimate post-filing "
        "amendments count as escapes &mdash; treat the rate as an upper bound. "
        "And the gate only sees per-field confidence: a document whose extraction "
        "silently dropped a goods item still auto-accepts, because the missing "
        "line has no score to be low.  That is how real confidence gates fail.</p>"
        f"{table}</section>"
    )


def _calibration_section(ev: Evaluation, dd: _Drilldowns) -> str:
    comparisons = ev.comparisons
    scored = comparisons[
        comparisons["confidence"].notna()
        & comparisons["status"].isin(["match", "mismatch"])
    ]
    body = []
    for _, row in ev.calibration.iterrows():
        bucket = row["confidence_bucket"]
        bucket_rows = scored[scored["confidence_bucket"] == bucket]
        bucket_mismatches = bucket_rows[bucket_rows["status"] == MISMATCH]
        n = int(row["n"])
        body.append([
            esc(bucket),
            dd.link(f"{n:,}", f"Field comparisons with confidence {bucket}",
                    bucket_rows, COMPARISON_COLUMNS) if n else "0",
            f"{int(row['matches']):,}",
            dd.link(f"{n - int(row['matches']):,}",
                    f"Mismatches with confidence {bucket}",
                    bucket_mismatches, COMPARISON_COLUMNS) if n else "0",
            _pct(row["observed_accuracy"]),
            _fmt(row["mean_confidence"]),
        ])
    table = _raw_table(
        ["confidence bucket", "comparisons", "matches", "mismatches",
         "observed accuracy", "mean confidence"],
        body,
    )
    return (
        "<section><h2>Confidence calibration</h2>"
        "<p>Per-field extraction confidence bucketed into deciles, against the "
        "share of those fields that actually matched the filed value.  If the "
        "tool's confidence is honest, accuracy climbs with the bucket; flat "
        "rows mean the score is decoration.</p>"
        f"{table}</section>"
    )


def _per_field_section(ev: Evaluation, dd: _Drilldowns) -> str:
    comparisons = ev.comparisons
    body = []
    for _, row in ev.per_field.iterrows():
        field = row["field"]
        field_rows = comparisons[comparisons["field"] == field]
        mismatch_rows = field_rows[field_rows["status"] == MISMATCH]
        missing_rows = field_rows[
            ~field_rows["extracted_present"] & field_rows["filed_present"]
        ]
        body.append([
            esc(field),
            esc(row["level"]),
            str(row["tier"]),
            esc(row["comparator"]),
            f"{int(row['matches']):,}",
            dd.link(f"{int(row['mismatches']):,}", f"Mismatches on {field}",
                    mismatch_rows.sort_values("confidence", ascending=False),
                    COMPARISON_COLUMNS),
            dd.link(f"{int(row['missing_extracted']):,}",
                    f"{field}: filed value present but nothing extracted",
                    missing_rows, COMPARISON_COLUMNS),
            str(int(row["unparseable"])),
            _pct(row["precision"]),
            _pct(row["recall"]),
        ])
    table = _raw_table(
        ["field", "level", "tier", "comparator", "matches", "mismatches",
         "missing extracted", "unparseable", "precision", "recall"],
        body,
    )
    return (
        "<section><h2>Per-field precision and recall</h2>"
        "<p>Precision: when the tool extracted a value and the filing has one, "
        "how often they agree.  Recall: of the values present in the filing, "
        "how often the tool produced a matching one (so a field the tool "
        "skipped hurts recall, not precision).  Item-level fields aggregate "
        "over aligned item pairs; unmatched items are counted in the "
        "alignment section above, not here.</p>"
        f"{table}</section>"
    )


def _breakdown_section(
    ev: Evaluation, dd: _Drilldowns, breakdown_ci: pd.DataFrame | None = None
) -> str:
    comparisons = ev.comparisons
    tier = ev.critical_tier
    ci_lookup: dict[tuple, tuple[float, float]] = {}
    if breakdown_ci is not None:
        ci_lookup = {
            (row["country"], row["source_system"]): (row["clean_ci_low"], row["clean_ci_high"])
            for _, row in breakdown_ci.iterrows()
        }
    body = []
    for _, row in ev.breakdown.iterrows():
        country, system = row["country"], row["source_system"]
        cell_rows = comparisons[
            (comparisons["country"] == country)
            & (comparisons["source_system"] == system)
            & (comparisons["status"] == MISMATCH)
        ]
        critical_rows = cell_rows[cell_rows["tier"] == tier]
        body.append([
            esc(country),
            esc(system),
            f"{int(row['documents']):,}",
            _pct_with_ci(row["clean_document_rate"], ci_lookup.get((country, system))),
            dd.link(f"{int(row['critical_mismatches']):,}",
                    f"Tier-{tier} mismatches: {country} / {system}",
                    critical_rows, COMPARISON_COLUMNS),
            _pct(row["critical_mismatch_rate"]),
            dd.link(f"{int(row['mismatches']):,}",
                    f"All mismatches: {country} / {system}",
                    cell_rows, COMPARISON_COLUMNS),
        ])
    table = _raw_table(
        ["country", "source system", "documents", "clean doc rate [95% CI]",
         f"tier-{tier} mismatches", f"tier-{tier} mismatch rate", "all mismatches"],
        body,
    )
    return (
        "<section><h2>Mismatches by country and source system</h2>"
        "<p>Country is the country of filing (taken from the filed record); "
        "source system is the extraction tool that produced the record.  A "
        "single bad cell here usually means bad scans or a bad template for "
        "that lane, not a globally bad tool.  The intervals (Wilson, 95%) are "
        "there to stop over-reading small slices: a two-point difference "
        "between cells whose intervals overlap is not a finding.</p>"
        f"{table}</section>"
    )


def _worst_mismatches_section(ev: Evaluation) -> str:
    mismatches = ev.comparisons[ev.comparisons["status"] == MISMATCH]
    worst = mismatches.sort_values(
        ["tier", "confidence"], ascending=[True, False], na_position="last"
    ).head(50)
    return (
        "<section><h2>50 worst mismatches</h2>"
        "<p>Worst = lowest tier first (most critical), then highest extraction "
        "confidence &mdash; a confidently wrong value is more dangerous than a "
        "hesitantly wrong one, because no review queue will catch it.  Missed "
        "and spurious items carry no confidence, so they sort to the end of "
        "their tier here; the alignment section lists them all.</p>"
        f"{_table(worst, COMPARISON_COLUMNS)}</section>"
    )


def _validity_section(validity: pd.DataFrame | None, dd: _Drilldowns) -> str:
    if validity is None or validity.empty:
        return ""
    body = []
    for rule, group in validity.groupby("rule"):
        failures = group[~group["passed"]]
        checked = len(group)
        body.append([
            esc(rule),
            f"{checked:,}",
            dd.link(f"{len(failures):,}", f"Validity failures: {rule}",
                    failures,
                    ["doc_id", "item_number", "country", "source_system", "reason"]),
            _pct(len(failures) / checked if checked else None),
        ])
    table = _raw_table(["rule", "records checked", "failures", "failure rate"], body)
    return (
        "<section><h2>Validity rules (no ground truth needed)</h2>"
        "<p>Deterministic checks run on the extracted records alone &mdash; "
        "format, checksums, reference lists, arithmetic.  These catch problems "
        "even when the filed record is wrong too.</p>"
        f"{table}</section>"
    )


def _caveats_section(extra_notes: list[str] | None, has_gold: bool = False) -> str:
    notes = [
        "Fabricated errors &mdash; a different but valid EORI, an in-tariff HS "
        "sibling, a wrong-but-real origin &mdash; pass every validity rule and "
        "carry high confidence.  This is the characteristic LLM failure mode "
        "(fluent garbage), as opposed to OCR noise (visible garbage), and it is "
        "why blind monitoring alone cannot carry production.",
    ]
    if has_gold:
        notes.append(
            "The gold columns exist only because this data is synthetic.  In "
            "production the gold regime is a periodically drawn, human-graded "
            "audit sample &mdash; see the audit table above for what its "
            "precision costs."
        )
    notes += [
        "The filed record is <strong>not ground truth</strong>.  Documents get "
        "legitimately amended after filing (revaluations, corrected counts, "
        "reclassifications), and those amendments appear here as extraction "
        "mismatches.  Nothing in this report can tell the two apart; the "
        "mismatch counts are upper bounds on extraction error.",
        "When extraction and filing agree, they can both be wrong.  Agreement "
        "measures consistency, not correctness; only the validity rules catch "
        "some of these cases.",
        "Item alignment is a greedy heuristic, not truth.  Two same-chapter "
        "items with similar values can pair the wrong way round, and a merged "
        "line whose totals happen to equal one filed item pairs cleanly while "
        "the other filed item is reported missed.  Every pairing's stated "
        "basis is in the drill-downs so a human can audit it.",
        "Ambiguous dates (day and month both &le; 12) are resolved by the "
        "configured day-first preference, which can itself be wrong.  Mismatch "
        "reasons flag when a difference would vanish with the order swapped.",
    ]
    if extra_notes:
        notes.extend(esc(n) for n in extra_notes)
    items = "".join(f"<li>{n}</li>" for n in notes)
    return (
        '<section class="caveats"><h2>Read this before quoting numbers</h2>'
        f"<ul>{items}</ul></section>"
    )


# ---------------------------------------------------------------------------
# page assembly
# ---------------------------------------------------------------------------

_CSS = """
:root { --accent: #1a56db; --ink: #1f2933; --muted: #616e7c; --line: #d9dee5; --bg: #f7f8fa; }
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       color: var(--ink); background: var(--bg); margin: 0; padding: 2rem 1.5rem 6rem; }
main { max-width: 1150px; margin: 0 auto; }
h1 { margin: 0 0 .2rem; font-size: 1.6rem; }
h2 { margin: 0 0 .5rem; font-size: 1.15rem; }
h3 { margin: 0; font-size: 1rem; }
section { background: #fff; border: 1px solid var(--line); border-radius: 8px;
          padding: 1.2rem 1.4rem; margin: 1.2rem 0; }
p { color: var(--muted); margin: .3rem 0 .9rem; max-width: 75ch; }
.meta { color: var(--muted); margin-bottom: 1.4rem; }
.tiles { display: flex; gap: 1rem; flex-wrap: wrap; }
.tile { flex: 1 1 240px; }
.tile .num { font-size: 2.4rem; font-weight: 700; letter-spacing: -0.02em; }
.tile .label { color: var(--muted); }
.tile .sub { margin-top: .3rem; font-size: .9rem; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .86rem; }
th { text-align: left; padding: .45rem .6rem; border-bottom: 2px solid var(--line);
     white-space: nowrap; }
td { padding: .4rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top;
     font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: #fafbfc; }
a.drill { color: var(--accent); text-decoration: underline dotted; cursor: pointer; }
.drilldown { display: none; background: #fff; border: 1px solid var(--accent);
             border-radius: 8px; padding: 1rem 1.2rem; margin: 1rem 0; }
.drilldown.open { display: block; }
.drill-head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.drill-head button { border: 1px solid var(--line); background: #fff; border-radius: 6px;
                     padding: .2rem .8rem; cursor: pointer; }
.note { font-size: .85rem; }
.ci { color: var(--muted); font-size: .85em; white-space: nowrap; }
.caveats { border-left: 4px solid #b45309; }
.caveats li { margin: .4rem 0; max-width: 85ch; }
#drilldowns h2 { margin-top: 2rem; }
"""

_JS = """
document.addEventListener('click', function (event) {
  var link = event.target.closest('a.drill');
  if (link) {
    event.preventDefault();
    var target = document.getElementById(link.getAttribute('href').slice(1));
    document.querySelectorAll('.drilldown.open').forEach(function (el) {
      if (el !== target) el.classList.remove('open');
    });
    target.classList.add('open');
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  var close = event.target.closest('.drilldown button.close');
  if (close) close.closest('.drilldown').classList.remove('open');
});
"""


def build_report(
    evaluation: Evaluation,
    validity_results: pd.DataFrame | None,
    out_path: str | Path,
    extras: ReportExtras | None = None,
    generated_at: datetime | None = None,
) -> Path:
    extras = extras or ReportExtras()
    dd = _Drilldowns()
    generated_at = generated_at or datetime.now()
    sections = [
        _headline(evaluation, dd, extras),
        _regimes_section(evaluation, extras, dd),
        _alignment_section(evaluation, dd),
        _straight_through_section(evaluation, dd),
        _leading_indicators_section(evaluation, extras, dd),
        _calibration_section(evaluation, dd),
        _confidence_quality_section(extras, dd),
        _per_field_section(evaluation, dd),
        _breakdown_section(evaluation, dd, extras.breakdown_ci),
        _worst_mismatches_section(evaluation),
        _validity_section(validity_results, dd),
        _caveats_section(extras.extra_notes, has_gold=extras.gold_evaluation is not None),
    ]
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Extraction quality report</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>Extraction quality report</h1>
<div class="meta">Generated {esc(generated_at.strftime('%Y-%m-%d %H:%M'))}
 &middot; every number links to the rows behind it
 &middot; full row-level data in the out/ CSVs</div>
{"".join(sections)}
<div id="drilldowns"><h2>Underlying rows</h2>
<p>These tables open when you click a number above.</p>
{dd.html()}
</div>
</main>
<script>{_JS}</script>
</body>
</html>
"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path
