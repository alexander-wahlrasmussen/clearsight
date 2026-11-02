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

from comparators import MISMATCH, NOT_COMPARABLE
from compare_models import HeadToHead, ModelComparison
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
class SyntheticReviewScenario:
    """Precomputed synthetic-only demonstration; not a real reviewer study."""

    acceptance_probability: float
    simulation_version: str
    evaluation: Evaluation
    hidden_errors: pd.DataFrame


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
    synthetic_review: SyntheticReviewScenario | None = None


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
            f' &middot; <span class="ci">very likely between {low * 100:.1f}% and '
            f"{high * 100:.1f}%</span>"
        )
    tiles = [
        (
            f"{ev.clean_document_rate * 100:.1f}%",
            f"clean document rate (zero tier-{tier} mismatches, vs filed)",
            clean_sub,
        ),
        (
            f"{len(critical_mismatch_rows):,}",
            f"serious (tier-{tier}) mismatches",
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


def _story_item_card(label: str, item_number, values: dict[str, object]) -> str:
    """Render the few item facts needed to follow the opening case."""
    details = [
        ("HS code", values.get("hs_code")),
        ("Value", values.get("item_value")),
        ("Origin", values.get("origin_country")),
        ("Quantity", values.get("quantity")),
        ("Unit", values.get("quantity_unit")),
    ]
    facts = " ".join(
        f'<span><small>{esc(name)}</small>{_fmt(value) if value is not None else "&ndash;"}</span>'
        for name, value in details
    )
    number = "?" if item_number is None or pd.isna(item_number) else _fmt(item_number)
    return (
        '<div class="story-item">'
        f'<strong>{esc(label)} {number}</strong>'
        f'<div class="story-facts">{facts}</div></div>'
    )


def _story_section(ev: Evaluation, dd: _Drilldowns, extras: ReportExtras) -> str:
    """Put one auditable document and the agreement-vs-correctness lesson first."""
    comparisons = ev.comparisons
    structures = comparisons[
        (comparisons["level"] == "structure")
        & (comparisons["status"] == MISMATCH)
    ].sort_values(["doc_id", "field"])

    case_doc = structures.iloc[0]["doc_id"] if not structures.empty else None
    if case_doc is None:
        critical = comparisons[
            (comparisons["status"] == MISMATCH)
            & (comparisons["tier"] == ev.critical_tier)
        ].sort_values("doc_id")
        case_doc = critical.iloc[0]["doc_id"] if not critical.empty else None
    if case_doc is None and not ev.doc_summary.empty:
        case_doc = ev.doc_summary.sort_values("doc_id").iloc[0]["doc_id"]

    case_rows = (
        comparisons[comparisons["doc_id"] == case_doc]
        if case_doc is not None else comparisons.head(0)
    )
    case_structures = case_rows[
        (case_rows["level"] == "structure")
        & (case_rows["status"] == MISMATCH)
    ]
    paired_rows = case_rows[case_rows["level"] == "item"]
    pair_groups: dict[tuple, pd.DataFrame] = {}
    for key, group in paired_rows.groupby(
        ["item_no_extracted", "item_no_filed"], dropna=False, sort=False
    ):
        pair_groups[key] = group

    pair_cards = []
    for (extracted_no, filed_no), group in pair_groups.items():
        extracted_values = dict(zip(group["field"], group["extracted_value"]))
        filed_values = dict(zip(group["field"], group["filed_value"]))
        pair_cards.append(
            '<div class="story-pair">'
            + _story_item_card("Extracted line", extracted_no, extracted_values)
            + '<div class="story-link" aria-label="paired by content">MATCH</div>'
            + _story_item_card("Filed line", filed_no, filed_values)
            + "</div>"
        )

    unmatched_cards = []
    for row in case_structures.itertuples():
        if row.field == "missed_item":
            unmatched_cards.append(
                '<div class="story-pair">'
                '<div class="story-item story-empty"><strong>No extracted line matched</strong>'
                '<div class="story-facts">The comparison could not find a partner.</div></div>'
                '<div class="story-link story-unmatched" aria-label="no match">NO MATCH</div>'
                f'<div class="story-item story-alert"><strong>Filed line {_fmt(row.item_no_filed)}</strong>'
                f'<div class="story-facts">{esc(row.filed_value)}</div></div></div>'
            )
        elif row.field == "spurious_item":
            unmatched_cards.append(
                '<div class="story-pair">'
                f'<div class="story-item story-alert"><strong>Extracted line {_fmt(row.item_no_extracted)}</strong>'
                f'<div class="story-facts">{esc(row.extracted_value)}</div></div>'
                '<div class="story-link story-unmatched" aria-label="no match">NO MATCH</div>'
                '<div class="story-item story-empty"><strong>No filed line matched</strong>'
                '<div class="story-facts">The comparison could not find a partner.</div></div></div>'
            )

    if not pair_cards and not unmatched_cards:
        mismatches = case_rows[case_rows["status"] == MISMATCH]
        if not mismatches.empty:
            row = mismatches.iloc[0]
            pair_cards.append(
                '<div class="story-pair">'
                f'<div class="story-item"><strong>Extracted {esc(row["field"])}</strong>'
                f'<div class="story-facts">{_fmt(row["extracted_value"])}</div></div>'
                '<div class="story-link story-unmatched" aria-label="values differ">DIFFERS</div>'
                f'<div class="story-item story-alert"><strong>Filed {esc(row["field"])}</strong>'
                f'<div class="story-facts">{_fmt(row["filed_value"])}</div></div></div>'
            )

    if case_doc is None:
        case_content = '<p>No joined documents are available to illustrate a comparison.</p>'
    else:
        case_content = (
            f'<p class="story-kicker">ONE DOCUMENT &middot; {esc(case_doc)}</p>'
            + "".join(pair_cards + unmatched_cards)
            + '<p class="story-caption">Pairing is a content-based hypothesis, not proof of identity. '
            'A mismatch against a filed record is a reason to investigate, not by itself proof that extraction was wrong.</p>'
            + dd.link("Open this document's comparison rows",
                      f"Evidence for {case_doc}", case_rows, COMPARISON_COLUMNS)
        )

    case_confidence = ev.doc_summary.loc[
        ev.doc_summary["doc_id"] == case_doc, "min_critical_confidence"]
    if case_doc is not None and len(case_confidence) and pd.notna(case_confidence.iloc[0]):
        case_content += (
            '<p class="story-caption">The extractor\'s lowest confidence on an important field here is '
            f'<strong>{float(case_confidence.iloc[0]):.3f}</strong>. Nothing in the scores points the '
            'reviewer to the missing line, because a line that was never extracted has no score.</p>'
        )

    agreement_content = (
        '<p>Suppose a document says EUR 1,000, the extraction says EUR 100 and the reviewer submits '
        'EUR 100 unchanged. Extraction and filing agree, and both are wrong. Only an independent '
        'check of the source can tell.</p>'
    )
    scenario = extras.synthetic_review
    gold = extras.gold_evaluation
    if scenario is not None and gold is not None and extras.hidden_errors is not None:
        def clean_link(evaluation: Evaluation, label: str) -> tuple[str, float]:
            rate = evaluation.clean_document_rate
            rows = evaluation.doc_summary
            text = f"{rate * 100:.1f}%" if not pd.isna(rate) else "n/a"
            return dd.link(text, f"Documents and clean flag: {label}", rows,
                           DOC_COLUMNS_BASE + ["critical_mismatches", "clean"]), rate

        def hidden_link(frame: pd.DataFrame, label: str) -> str:
            rows = frame[frame["level"] == "header"]
            return dd.link(f"{len(rows):,}", f"Wrong header fields hidden by agreement: {label}",
                           rows, ["doc_id", "field", "extracted_value", "filed_value",
                                  "gold_value", "reason", "reason_gold", "confidence"])

        bars = []
        for label, evaluation, css in [
            ("agreement with the original filing", ev, "story-pass"),
            (
                ("agreement if reviewers accept wrong pre-fill "
                 f"({scenario.acceptance_probability * 100:.0f}% assumed)"),
                scenario.evaluation,
                "story-risk",
            ),
            ("agreement with the synthetic truth", gold, "story-truth"),
        ]:
            link, rate = clean_link(evaluation, label)
            width = 0 if pd.isna(rate) else rate * 100
            bars.append(
                f'<div class="story-compare"><div class="story-compare-label">{esc(label)}</div>'
                f'<div class="story-compare-track"><div class="story-compare-fill {css}" '
                f'style="width:{width:.1f}%"></div></div><strong>{link}</strong></div>'
            )
        agreement_content = (
            '<p class="story-kicker">SYNTHETIC SCENARIO &middot; SAME EXTRACTION, DIFFERENT FILING</p>'
            '<p class="story-data-label">Documents with no important mismatch, by what we compare against</p>'
            + "".join(bars)
            + '<p class="story-caption"><strong>Agreement went up while the filing got worse.</strong> '
            'Wrong values hidden by agreement: '
            + hidden_link(extras.hidden_errors, "original filing") + ' &rarr; '
            + hidden_link(scenario.hidden_errors, "hypothetical filing")
            + '. The extraction did not change; reviewers copying its mistakes made the filing '
            'look more like it. The acceptance rate is an assumption, not observed reviewer behaviour.</p>'
        )

    return (
        '<section class="story-section">'
        '<div class="story-heading"><div><p class="story-kicker">THE QUESTION</p>'
        '<h2>What does the person reviewing this declaration need to be warned about?</h2></div>'
        '<p>Today a person reviews every declaration before it is filed. This report gathers evidence '
        'about the extraction they are shown. It does not declare a document correct.</p></div>'
        '<p class="story-takeaway"><span>The takeaway</span>A confidence score can only describe what the '
        'extractor found. It cannot point a reviewer to a line the extractor left out, and agreement '
        'with the filing cannot prove the reviewer caught it.</p>'
        '<div class="story-grid">'
        f'<div class="story-panel"><h3>1. Follow one document</h3>{case_content}</div>'
        f'<div class="story-panel"><h3>2. Agreement is not correctness</h3>{agreement_content}</div>'
        '</div>'
        '<div class="story-routing"><span class="story-kicker">AFTER FILING: A POLICY TO VALIDATE</span>'
        '<div class="story-routes">'
        '<div class="route route-agree"><strong>Extraction agrees with filing</strong><span>Not proof: it may be a shared error. Keep some in a random independent audit</span></div>'
        '<div class="route route-differs"><strong>Extraction differs from filing</strong><span>Investigate which side is wrong</span></div>'
        '<div class="route route-unknown"><strong>Not enough evidence</strong><span>Report as unresolved; never count it as correct</span></div>'
        '</div><p class="story-caption">Human review stays in place. These lanes describe how to treat the evidence '
        'afterwards; they are a proposal for the team to validate, not an implemented workflow. '
        'Only an independent check of the source (blind audit) can estimate what extraction and reviewer both missed.</p>'
        '</div></section>'
    )


def _future_whatif_picture(ev: Evaluation, dd: _Drilldowns, synthetic: bool = False) -> str:
    """One-picture version of the straight-through what-if at the threshold
    nearest 0.70, shown above the full threshold table."""
    comparisons = ev.comparisons
    # Use the configured threshold nearest 0.70, and display the actual value.
    straight = ev.straight_through
    risk_content = '<p>No confidence-threshold results are available for this run.</p>'
    if not straight.empty and len(ev.doc_summary):
        index = (straight["threshold"] - 0.70).abs().idxmin()
        policy = straight.loc[index]
        threshold = float(policy["threshold"])
        accepted = ev.doc_summary[
            ev.doc_summary["min_critical_confidence"].notna()
            & (ev.doc_summary["min_critical_confidence"] >= threshold)
        ]
        escaped = accepted[accepted["critical_mismatches"] > 0]
        accepted_without_mismatch = len(accepted) - len(escaped)
        review_docs = ev.doc_summary[~ev.doc_summary.index.isin(accepted.index)]
        not_accepted = len(review_docs)
        total = len(ev.doc_summary)
        no_mismatch = accepted[accepted["critical_mismatches"] == 0]
        critical_nc_docs = set(comparisons.loc[
            (comparisons["tier"] == ev.critical_tier)
            & (comparisons["status"] == NOT_COMPARABLE), "doc_id"])
        unchecked = accepted[accepted["doc_id"].isin(critical_nc_docs)]
        segments = [
            ("above threshold · no mismatch found vs filing (not verified)",
             accepted_without_mismatch, "story-pass"),
            ("above threshold · mismatch found vs filing", len(escaped), "story-risk"),
            ("below threshold · would go to review (review is what happens today)",
             not_accepted, "story-review"),
        ]
        bar = "".join(
            f'<div class="story-bar-segment {css}" style="width:{count / total * 100:.2f}%" '
            f'title="{count} {esc(label)}"></div>'
            for label, count, css in segments if count
        )
        legend_frames = [
            no_mismatch,
            escaped,
            review_docs,
        ]
        legend_titles = [
            f"No tier-{ev.critical_tier} mismatch found at threshold {threshold:.2f}",
            f"Tier-{ev.critical_tier} mismatch found at threshold {threshold:.2f}",
            f"Documents below threshold {threshold:.2f} (would go to review)",
        ]
        legend = "".join(
            f'<div class="story-legend-item"><i class="{css}"></i>'
            f'<strong>{dd.link(f"{count:,}", title, frame, DOC_COLUMNS_BASE + ["critical_mismatches", "min_critical_confidence"])}</strong> '
            f'{esc(label)}</div>'
            for (label, count, css), frame, title
            in zip(segments, legend_frames, legend_titles, strict=True)
        )
        source = ("Generated demo data" if synthetic else "Extraction run")
        data_label = (
            f'<p class="story-data-label">{source} &middot; {total:,} documents &middot; '
            'compared against filed records, not against verified answers</p>'
        )
        nc_frame = ev.doc_summary[ev.doc_summary["doc_id"].isin(critical_nc_docs)]
        nc_columns = DOC_COLUMNS_BASE + [
            "critical_mismatches", "not_comparable", "min_critical_confidence"]
        unchecked_note = (
            '<p class="story-caption"><strong>Missing fields are caught; missing lines are not.</strong> '
            + dd.link(f"{len(nc_frame):,}",
                      f"Documents with a tier-{ev.critical_tier} field that could not be compared",
                      nc_frame, nc_columns)
            + f' documents had a tier-{ev.critical_tier} field that could not be compared; '
            + dd.link(f"{len(unchecked):,}",
                      f"Above threshold {threshold:.2f} despite an uncomparable tier-{ev.critical_tier} field",
                      unchecked, nc_columns)
            + ' of them cleared the threshold: a missing critical score blocks automatic acceptance. '
            'A dropped goods line leaves no score behind, so nothing blocks it.</p>'
        )
        risk_content = (
            f'<p class="story-kicker">IF WE SET A TEST THRESHOLD OF {threshold:.2f}</p>'
            + data_label +
            f'<div class="story-bar" role="img" aria-label="{accepted_without_mismatch} accepted documents had no tier mismatch found, '
            f'{len(escaped)} accepted documents had a tier mismatch, {not_accepted} below threshold">{bar}</div>'
            f'<div class="story-legend">{legend}</div>'
            + unchecked_note +
            f'<p class="story-caption">“Mismatch found” means a tier-{ev.critical_tier} difference from the filed record. '
            '“No mismatch found” is not the same as verified: some fields cannot be compared, and filed records '
            'can be wrong or amended. A reviewer can also accept the same wrong value, which hides it. '
            'These are warning signals, not confirmed extraction errors.</p>'
            + dd.link("Open threshold results",
                      f"Threshold {threshold:.2f}: pass/review outcomes",
                      ev.doc_summary, DOC_COLUMNS_BASE + [
                          "critical_mismatches", "min_critical_confidence", "clean",
                      ])
        )

    return f'<div class="story-whatif">{risk_content}</div>'


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
            f"serious (tier-{tier}) mismatches, wrong fields and whole items",
            f"{gold_n:,}", f"{proxy_n:,}", f"{proxy_n - gold_n:+,}",
        ],
        [
            "share of auto-accepted docs (at confidence 0.80) carrying a serious error",
            _pct(gold_escape), _pct(proxy_escape),
            f"{(proxy_escape - gold_escape) * 100:+.1f} pp",
        ],
    ]
    table = _raw_table(
        ["metric", "scored against the truth (gold)",
         "scored against what was filed (proxy)", "difference"],
        body,
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
            "<p>Where the two scorings disagree, one of them is misleading us. "
            f"{fa_link}: the tool was actually right, but the filed record was "
            "different &mdash; for example, changed after filing &mdash; so "
            "scoring against the filing wrongly blames the tool.  "
            f"{hidden_link}: the tool agreed with the filing and both were wrong "
            "&mdash; a mistake comparison with that filing cannot see.  This "
            "can happen even when a person reviews every document, if they "
            "accept a wrong pre-filled value.  The baseline synthetic filing "
            "starts from truth, so it does not measure real reviewer behaviour.  "
            "These counts exclude unmatched whole items.</p>"
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
             "likely range (95%)", "width of range"],
            audit_body,
        )
        audit = (
            "<h3>What to do in production: audit a sample by hand</h3>"
            "<p>In the real world nobody has the truth for every document.  What "
            "you can do is pick documents at random, have a person check just "
            "those against the paperwork, and let that sample speak for the "
            "whole.  In the synthetic demo, generated truth plays the person.  "
            "The smaller "
            "the sample, the wider the range of clean rates it could honestly "
            "mean &mdash; that width is the price list for human checking: to "
            "halve it, check four times as many documents.  The rate against "
            "the available gold reference is "
            f"{_pct(gold.clean_document_rate)}; each range below should usually "
            "contain it, though even a 95% range misses about one run in twenty "
            "&mdash; which is part of what it teaches.</p>"
            f"{audit_table}"
        )

    return (
        "<section><h2>Three ways to score the same extraction</h2>"
        "<p>To say the tool got something wrong, you need something to compare "
        "against &mdash; and there are three candidates, each with a catch.  "
        "<strong>The truth</strong> (we call it gold): what the paperwork really "
        "said.  The synthetic demo has generated truth; in production, "
        "independent human audits provide a reference for a sample.  "
        "<strong>What was filed</strong> (the proxy, i.e. "
        "our stand-in for the truth): available for every document, but it "
        "arrives days later and it is not quite the truth &mdash; filings get "
        "legitimately corrected after the fact, and sometimes the filing repeats "
        "the tool's own mistake.  <strong>No answer key at all</strong> (blind): "
        "the built-in checks and the tool's own confidence scores, available the "
        "moment a document is processed; how far they reach is measured a few "
        "sections down.  The rest of this report scores against what was filed, "
        "because that is all production normally has.</p>"
        f"{table}{disagreement}{audit}</section>"
    )


def _synthetic_review_section(ev: Evaluation, extras: ReportExtras, dd: _Drilldowns) -> str:
    scenario = extras.synthetic_review
    if scenario is None:
        return ""
    gold = extras.gold_evaluation
    if gold is None or extras.hidden_errors is None:
        raise ValueError("synthetic review requires gold and baseline hidden-error comparisons")
    if (
        scenario.evaluation.critical_tier != ev.critical_tier
        or gold.critical_tier != ev.critical_tier
    ):
        raise ValueError("synthetic review evaluations must use the same critical tier")

    def header_signature(evaluation: Evaluation) -> pd.DataFrame:
        columns = [
            "doc_id", "field", "tier", "extracted_value", "confidence",
            "comparator", "comparator_version",
        ]
        return (
            evaluation.comparisons.loc[evaluation.comparisons["level"] == "header", columns]
            .sort_values(["doc_id", "field"])
            .reset_index(drop=True)
        )

    def header_comparators(evaluation: Evaluation) -> pd.DataFrame:
        return (
            evaluation.per_field.loc[
                evaluation.per_field["level"] == "header", ["field", "tier", "comparator"]
            ]
            .sort_values("field")
            .reset_index(drop=True)
        )

    baseline_docs = ev.doc_summary["doc_id"]
    for other in (scenario.evaluation, gold):
        other_docs = other.doc_summary["doc_id"]
        if (
            not baseline_docs.is_unique or not other_docs.is_unique
            or set(baseline_docs) != set(other_docs)
        ):
            raise ValueError("synthetic review evaluations must cover the same documents")
        if (
            not header_signature(ev).equals(header_signature(other))
            or not header_comparators(ev).equals(header_comparators(other))
        ):
            raise ValueError(
                "synthetic review requires the same extracted headers and "
                "header comparator settings"
            )

    def clean_link(evaluation: Evaluation, label: str) -> str:
        rows = evaluation.comparisons[
            evaluation.comparisons["tier"] == evaluation.critical_tier
        ].merge(
            evaluation.doc_summary[["doc_id", "clean"]],
            on="doc_id",
            validate="many_to_one",
        )
        rate = evaluation.clean_document_rate
        text = f"{rate * 100:.1f}%" if not pd.isna(rate) else "not available"
        return dd.link(
            text,
            f"Tier-{evaluation.critical_tier} comparisons: {label}",
            rows,
            COMPARISON_COLUMNS + ["comparator", "comparator_version", "clean"],
        )

    def hidden_link(frame: pd.DataFrame, label: str) -> str:
        header_rows = frame[frame["level"] == "header"]
        return dd.link(
            f"{len(header_rows):,}",
            f"Wrong header fields hidden by agreement: {label}",
            header_rows,
            [
                "doc_id", "field", "tier", "extracted_value", "filed_value",
                "gold_value", "status", "reason", "status_gold", "reason_gold",
                "confidence", "comparator", "comparator_version",
            ],
        )

    table = _raw_table(
        ["measure", "result"],
        [
            [
                (
                    f"documents with no detected tier-{ev.critical_tier} mismatch "
                    "vs baseline filing"
                ),
                clean_link(ev, "baseline filing"),
            ],
            [
                (
                    f"documents with no detected tier-{ev.critical_tier} mismatch "
                    "vs hypothetical filing"
                ),
                clean_link(scenario.evaluation, "hypothetical filing"),
            ],
            [
                (
                    f"documents with no detected tier-{ev.critical_tier} mismatch "
                    "vs synthetic truth"
                ),
                clean_link(gold, "synthetic truth"),
            ],
            [
                "wrong header fields hidden by agreement with baseline filing",
                hidden_link(extras.hidden_errors, "baseline filing"),
            ],
            [
                "wrong header fields hidden by agreement with hypothetical filing",
                hidden_link(scenario.hidden_errors, "hypothetical filing"),
            ],
        ],
    )
    return (
        "<section><h2>Synthetic demonstration: when review copies an error</h2>"
        "<p>This is a sensitivity demonstration, <strong>not an observation "
        "of reviewers</strong>.  For each eligible wrong header value, it "
        "assumes a probability of "
        f"{scenario.acceptance_probability * 100:.1f}% that a hypothetical "
        "reviewer accepts the pre-fill.  It copies those values into a separate "
        "filing while keeping the extraction and baseline filing unchanged.  "
        "The synthetic truth identifies the wrong values; changed or "
        "not-comparable baseline values are left alone, even when a change is "
        "within a comparison tolerance.  The same comparators score each "
        "reference.</p>"
        f"{table}"
        "<p>A hidden error is a field where extraction matches the filing but "
        "not the synthetic truth.  Only comparable header fields are simulated; "
        "the document-level rate includes all configured fields and items.  "
        "A document with missing or unparseable fields can still have no "
        "detected mismatch.  This does not model review time, edits to correct "
        "values, missing fields, goods-item errors, or actual human behaviour.  "
        "An independent audit and a randomized workflow study are needed to "
        "measure real quality and usefulness.</p>"
        f"<p>Synthetic assumption version: {esc(scenario.simulation_version)}.  "
        "The seed, assumptions, decisions and comparison rows are in the "
        "synthetic_review folder next to this report.</p></section>"
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
        ["warning sign (known before filing)", "documents", "with tier-1 mismatch",
         "mismatch rate", "times the average"],
        body,
    )
    return (
        "<section><h2>Early warning signs, before any filing exists</h2>"
        "<p>The comparison against the filing only arrives days after extraction.  "
        "Until then, the only per-document warning signs are the built-in checks "
        "(validity rules) and the tool's own confidence scores.  This table asks "
        "how good those warnings are: of the documents each sign flags, how many "
        "later disagree with the filed record on a serious (tier-1) field, and "
        "how does that compare to an average document?  The last row includes "
        "documents with no warning sign that still disagree with the filing.  "
        "Errors shared by extraction and filing are invisible here.</p>"
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
        ["scored against", "fields scored", "agree with reference", "claimed confidence (avg)",
         "honesty gap (ECE)", "error score (Brier)", "sorting power (AUROC)"],
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
            "<h3>The same questions, field by field (scored against the truth)</h3>"
            "<p>One overall number hides a lot: a 0.9 on an HS code and a 0.9 on "
            "a date can mean different things.  The gap column is claimed "
            "confidence minus actual hit rate &mdash; above zero means the tool "
            "oversells itself on that field, below zero means it undersells.  "
            "Click a count to see that field's real errors, most confident "
            "first: a queue that prioritizes low confidence may miss them.</p>"
            + _raw_table(
                ["field", "level", "fields scored", "actually right",
                 "claimed confidence (avg)", "gap", "sorting power (AUROC)"],
                field_body,
            )
        )
    return (
        "<section><h2>Is the confidence score any good?</h2>"
        "<p>Two separate questions, often mixed up.  <strong>Is it honest?</strong> "
        "When the tool says 0.9, is it right about 9 times in 10?  The honesty "
        "gap (ECE) is the average distance between what the score claims and "
        "how often the tool is actually right &mdash; 0 is perfect.  The error "
        "score (Brier) measures the same idea but punishes being confidently "
        "wrong hardest &mdash; again, lower is better.  A consistent score "
        "may be recalibrated using representative independent labels, then "
        "checked on separate data.  <strong>Can it tell right from "
        "wrong at all?</strong>  Pick one field the tool got right and one it "
        "got wrong, at random: sorting power (AUROC) is how often the right "
        "one carries the higher score.  1.0 means always &mdash; a threshold "
        "can cleanly separate them; 0.5 means the overall ranking is no better "
        "than chance, not that every threshold is necessarily useless.  "
        "Monotonic recalibration cannot improve that ordering.</p>"
        "<p>Each row is scored against the reference it names.  A filing is an "
        "imperfect answer key: amendments and shared errors can make apparent "
        "confidence quality better or worse than quality against independent "
        "truth.  Use a blinded audit to assess the score in production.</p>"
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
        "<section><h2>Matching up the goods items</h2>"
        "<p>A declaration lists several goods items, and the line numbers are "
        "no help in matching them up &mdash; the extraction tool and the filing "
        "system each number their lines independently.  So items are paired by "
        "how similar their content is (HS code, value, origin, quantity; the "
        "rules live in alignment.py), and every pairing states its reasoning in "
        "the drill-downs.  A <em>missed</em> item was filed but never extracted "
        "(a dropped or merged line); a <em>spurious</em> item was extracted "
        "but never filed (usually a subtotal or footer read as goods).  Both "
        "count as mismatches, at the severity set in field_tiers.yaml.</p>"
        f"{table}</section>"
    )


def _straight_through_section(ev: Evaluation, dd: _Drilldowns, synthetic: bool = False) -> str:
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
        "<section><h2>Future what-if: confident documents without review</h2>"
        "<p>Each row plays out one policy: accept, without human review, every "
        "document whose most-critical (tier-1) fields all carry at least this "
        "confidence score.  The table shows how much of the workload would go "
        "straight through and how many would disagree with the filed record.  "
        "This is not a recommendation to remove current human review.  "
        "Amendments can create apparent escapes, while errors shared with the "
        "filing remain hidden, so the true escape rate may be higher or lower.  "
        "The gate can only see scores that "
        "exist: a document whose extraction silently dropped a goods item still "
        "sails through, because the missing line has no score to be low.  That "
        "is how confidence gates fail in real life.</p>"
        f"{_future_whatif_picture(ev, dd, synthetic)}{table}</section>"
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
        ["confidence band", "fields scored", "matched", "mismatched",
         "agree with filing", "average claimed confidence"],
        body,
    )
    return (
        "<section><h2>Does the confidence score mean what it says?</h2>"
        "<p>Every extracted field comes with a confidence score between 0 and 1.  "
        "Here those scores are grouped into ten bands, and for each band we ask: "
        "of the fields the tool scored in this range, how many actually matched "
        "what was filed?  If the score is honest, the 0.9&ndash;1.0 band should "
        "agree with an independent truth more often than the 0.6&ndash;0.7 band.  "
        "The filed record is not an independent truth.  If the rows look "
        "the same, the score is decoration and must not be used to decide "
        "anything.</p>"
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
        "<p>Two questions per field.  <em>Precision</em> &mdash; when the tool "
        "wrote a value, how often did it match the filing?  <em>Recall</em> &mdash; "
        "of the values that ended up in the filing, how often did the tool "
        "deliver a matching one?  The difference matters: a field the tool "
        "left empty is not included in the precision denominator, but it "
        "does hurt recall (the filing needed a value it never produced).  "
        "Item-level rows count every paired goods item; whole items that "
        "never got paired are counted in the alignment section above, not "
        "here.</p>"
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
        ["country", "source system", "documents", "clean doc rate [likely range]",
         f"tier-{tier} mismatches", f"tier-{tier} mismatch rate", "all mismatches"],
        body,
    )
    return (
        "<section><h2>Mismatches by country and source system</h2>"
        "<p>Country is the country of filing (taken from the filed record); "
        "source system is the extraction tool that produced the record.  A "
        "single bad cell here usually means bad scans or a bad template for "
        "that lane, not a globally bad tool.  The ranges in brackets say how "
        "much a rate built on this many documents can wobble by pure chance "
        "&mdash; when two cells' ranges overlap, the difference between them "
        "may well be luck, not a finding.</p>"
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
        "confidence &mdash; a confidently wrong value may not be prioritized "
        "by a queue that relies on low confidence.  These are mismatches "
        "against the filing, not proven extraction errors.  Missed "
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
        "<section><h2>Built-in checks (no answer key needed)</h2>"
        "<p>Fixed, rule-based checks that run on the extracted records alone "
        "&mdash; is the code well-formed, does the checksum work out, is it on "
        "the official list, do the numbers add up.  They need nothing to "
        "compare against, so they work the moment a document is processed, and "
        "they still catch problems when the filed record is wrong too.</p>"
        f"{table}</section>"
    )


def _caveats_section(extra_notes: list[str] | None, has_gold: bool = False) -> str:
    notes = [
        "Some wrong values look perfectly fine: a valid-but-wrong EORI, a "
        "different-but-real HS code, a wrong-but-existing origin country.  They "
        "pass every built-in check and arrive with high confidence.  This is "
        "how LLM-based extraction typically fails (fluent nonsense), where OCR "
        "typically fails visibly (mangled digits) &mdash; and it is why the "
        "built-in checks alone cannot guard production.",
    ]
    if has_gold:
        notes.append(
            "Gold needs an independent reference.  In the synthetic demo it "
            "comes from the generator; production needs a blinded, human-graded "
            "audit sample and an assessment of its coverage and uncertainty."
        )
    notes += [
        "The filed record is <strong>not the truth</strong>.  Documents get "
        "legitimately amended after filing (revaluations, corrected counts, "
        "reclassifications), and those amendments can appear as extraction "
        "mismatches.  A reviewer may also accept a wrong pre-filled value, "
        "hiding an error that extraction and filing share.  Filing-based "
        "mismatch counts are not guaranteed upper or lower bounds on true error.",
        "When extraction and filing agree, they can both be wrong.  Agreement "
        "measures consistency, not correctness.  Validity rules can flag some "
        "cases; an independent audit is needed for errors the rules cannot see.",
        (
            "Missing or unparseable values are not comparable, not matches.  They "
            "are not counted as mismatches in the clean-document headline, so a "
            "clean result does not prove that every critical value was present."
        ),
        "Item pairing is a best guess, not a fact.  Two similar goods lines "
        "can pair the wrong way round, and a merged line whose totals happen "
        "to equal one filed item pairs cleanly while the other filed item is "
        "reported as missed.  Every pairing states its reasoning in the "
        "drill-downs so a person can check it.",
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
.story-section { padding: clamp(1.1rem, 3vw, 2rem); border-top: 4px solid var(--accent); }
.story-heading { display: flex; justify-content: space-between; align-items: end; gap: 1.5rem; margin-bottom: 1.2rem; }
.story-heading h2 { font-size: clamp(1.35rem, 3vw, 2rem); line-height: 1.2; max-width: 22ch; }
.story-heading > p { max-width: 34ch; margin: 0; }
.story-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1rem; }
.story-panel { border: 1px solid var(--line); border-radius: 8px; padding: 1rem; min-width: 0; }
.story-panel h3 { margin-bottom: 1rem; }
.story-kicker { color: var(--muted); font-size: .72rem; font-weight: 750; letter-spacing: .09em; margin: 0 0 .7rem; }
.story-pair { display: grid; grid-template-columns: minmax(0, 1fr) 76px minmax(0, 1fr); gap: .55rem; align-items: stretch; margin: .7rem 0; }
.story-item { border: 1px solid var(--line); border-radius: 7px; padding: .75rem; min-width: 0; background: #fff; }
.story-item > strong { display: block; font-size: .88rem; margin-bottom: .5rem; }
.story-facts { color: var(--muted); font-size: .8rem; overflow-wrap: anywhere; }
.story-facts span { display: inline-flex; flex-direction: column; margin: 0 .7rem .45rem 0; }
.story-facts small { color: var(--muted); font-size: .68rem; }
.story-link { display: flex; align-items: center; justify-content: center; align-self: center; color: #17603a; font-size: .62rem; font-weight: 800; letter-spacing: .04em; }
.story-link::before, .story-link::after { content: ""; height: 2px; flex: 1; background: #59a878; }
.story-link { gap: .25rem; }
.story-unmatched { color: #9a3412; }
.story-unmatched::before, .story-unmatched::after { background: #d97745; }
.story-alert { border-color: #e6a483; background: #fff8f4; }
.story-empty { border-style: dashed; color: var(--muted); background: #fafbfc; }
.story-caption { font-size: .8rem; margin: .8rem 0; }
.story-bar { height: 1.3rem; display: flex; overflow: hidden; border-radius: 6px; background: #eef0f3; margin: 1rem 0 .6rem; }
.story-bar-segment { min-width: 2px; }
.story-pass { background: #7fa9a6; }
.story-risk { background: #d65b42; }
.story-review { background: #d7dce3; }
.story-truth { background: #5b6573; }
.story-compare { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) 3.5rem; gap: .6rem; align-items: center; margin: .5rem 0; font-size: .85rem; }
.story-compare-track { height: 12px; border-radius: 6px; background: #eef0f3; overflow: hidden; }
.story-compare-fill { height: 100%; }
.story-whatif { max-width: 46rem; margin: 1rem 0 1.4rem; }
.story-takeaway { margin: 0 0 1.2rem; padding: .8rem 1rem; border-left: 4px solid #d65b42; background: #fff6f3; font-size: 1.1rem; font-weight: 600; }
.story-takeaway span { display: block; font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; color: #9a3d2b; }
.story-data-label { margin: -.2rem 0 .6rem; font-size: .8rem; color: #5b6573; }
.story-legend { display: grid; gap: .4rem; }
.story-legend-item { display: flex; align-items: baseline; gap: .45rem; color: var(--muted); font-size: .85rem; }
.story-legend-item strong { color: var(--ink); font-variant-numeric: tabular-nums; }
.story-legend-item i { width: .65rem; height: .65rem; border-radius: 2px; flex: 0 0 auto; }
.story-routing { border-top: 1px solid var(--line); margin-top: 1.2rem; padding-top: 1rem; }
.story-routes { display: grid; grid-template-columns: repeat(3, 1fr); gap: .7rem; }
.route { display: flex; flex-direction: column; gap: .2rem; border-radius: 7px; padding: .8rem; border-left: 4px solid; }
.route strong { font-size: .9rem; }
.route span { color: var(--muted); font-size: .8rem; }
.route-agree { background: #f0f8f2; border-color: #50a878; }
.route-differs { background: #fff4ef; border-color: #d65b42; }
.route-unknown { background: #f4f5f7; border-color: #8a94a2; }
@media (max-width: 800px) {
  .story-grid { grid-template-columns: 1fr; }
  .story-heading { align-items: start; flex-direction: column; gap: .4rem; }
}
@media (max-width: 520px) {
  body { padding: 1rem .7rem 4rem; }
  .story-pair { grid-template-columns: 1fr; }
  .story-link { min-height: 1.1rem; }
  .story-link::before, .story-link::after { max-width: 3rem; }
  .story-routes { grid-template-columns: 1fr; }
}
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


# ---------------------------------------------------------------------------
# the model comparison report (A/B)
# ---------------------------------------------------------------------------


def _luck_text(probability: float) -> str:
    if pd.isna(probability):
        return "&ndash;"
    if probability < 0.001:
        return "less than 0.1% of the time"
    if probability < 0.01:
        return f"about {probability * 100:.1f}% of the time"
    return f"about {probability * 100:.0f}% of the time"


def _critical_rows_for_docs(ev: Evaluation, doc_ids) -> pd.DataFrame:
    c = ev.comparisons
    return c[
        c["doc_id"].isin(doc_ids)
        & (c["status"] == MISMATCH)
        & (c["tier"] == ev.critical_tier)
    ].sort_values(["doc_id", "tier"])


def _comparison_tiles(comparison: ModelComparison, dd: _Drilldowns) -> str:
    tiles = []
    for _, row in comparison.summary.iterrows():
        model = row["model"]
        ev = comparison.evaluations[model]
        dirty = ev.doc_summary[~ev.doc_summary["clean"]]
        sub = dd.link(
            f"{len(dirty)} documents not clean",
            f"{model}: documents with a serious (tier-{ev.critical_tier}) mismatch",
            dirty,
            DOC_COLUMNS_BASE + ["critical_mismatches", "missed_items", "spurious_items"],
        )
        sub += (
            f' &middot; <span class="ci">very likely between '
            f'{row["clean_ci_low"] * 100:.1f}% and {row["clean_ci_high"] * 100:.1f}%</span>'
        )
        tiles.append(
            f'<div class="tile"><div class="num">{_pct(row["clean_document_rate"])}</div>'
            f'<div class="label">{esc(model)} clean document rate</div>'
            f'<div class="sub">{sub}</div></div>'
        )
    if len(comparison.head_to_head) == 1:
        h = comparison.head_to_head[0]
        low, high = h.diff_ci
        tiles.append(
            f'<div class="tile"><div class="num">{h.clean_rate_diff * 100:+.1f} pp</div>'
            f'<div class="label">{esc(h.model_a)} minus {esc(h.model_b)}</div>'
            f'<div class="sub"><span class="ci">very likely between {low * 100:+.1f} and '
            f"{high * 100:+.1f} points</span></div></div>"
        )
    return f'<section><div class="tiles">{"".join(tiles)}</div></section>'


def _head_to_head_section(
    h: HeadToHead, comparison: ModelComparison,
    gold_comparison: ModelComparison | None, dd: _Drilldowns,
) -> str:
    ev_a = comparison.evaluations[h.model_a]
    ev_b = comparison.evaluations[h.model_b]
    n = h.documents

    def share(count: int) -> str:
        return _pct(count / n) if n else "&ndash;"

    body = [
        ["clean under both models", f"{h.both_clean:,}", share(h.both_clean)],
        [
            f"only {esc(h.model_a)} clean ({esc(h.model_a)} wins)",
            dd.link(f"{h.only_a_clean:,}",
                    f"Why {h.model_b} lost these documents (its serious mismatches)",
                    _critical_rows_for_docs(ev_b, h.only_a_doc_ids), COMPARISON_COLUMNS),
            share(h.only_a_clean),
        ],
        [
            f"only {esc(h.model_b)} clean ({esc(h.model_b)} wins)",
            dd.link(f"{h.only_b_clean:,}",
                    f"Why {h.model_a} lost these documents (its serious mismatches)",
                    _critical_rows_for_docs(ev_a, h.only_b_doc_ids), COMPARISON_COLUMNS),
            share(h.only_b_clean),
        ],
        ["clean under neither", f"{h.neither_clean:,}", share(h.neither_clean)],
    ]
    table = _raw_table(["outcome, per document", "documents", "share"], body)

    discordant = h.only_a_clean + h.only_b_clean
    winner = h.model_a if h.only_a_clean >= h.only_b_clean else h.model_b
    wins = max(h.only_a_clean, h.only_b_clean)
    losses = min(h.only_a_clean, h.only_b_clean)
    luck = h.chance_split_is_luck
    if discordant == 0:
        verdict = "The models never disagreed on a document; nothing separates them here."
    elif luck < 0.05:
        verdict = (
            f"Of the {discordant:,} documents where exactly one model was clean, "
            f"{esc(winner)} won {wins:,} and lost {losses:,}.  If the models were "
            f"equally good, a split this lopsided would happen by luck "
            f"{_luck_text(luck)} &mdash; the difference is real."
        )
    else:
        verdict = (
            f"Of the {discordant:,} documents where exactly one model was clean, "
            f"{esc(winner)} won {wins:,} and lost {losses:,}.  A split like this "
            f"happens by luck {_luck_text(luck)} between equally good models "
            "&mdash; this run does not separate them; more documents would."
        )

    gold_note = ""
    if gold_comparison is not None:
        match = next(
            (g for g in gold_comparison.head_to_head
             if (g.model_a, g.model_b) == (h.model_a, h.model_b)),
            None,
        )
        if match is not None:
            low, high = match.diff_ci
            gold_note = (
                f"<p><strong>Truth check:</strong> scored against the gold truth "
                f"instead of the filing, the difference is "
                f"{match.clean_rate_diff * 100:+.1f} points (very likely between "
                f"{low * 100:+.1f} and {high * 100:+.1f}) &mdash; the filing-based "
                f"verdict {'points the same way' if (match.clean_rate_diff >= 0) == (h.clean_rate_diff >= 0) else 'points the OTHER way; trust the truth'}.</p>"
            )

    return (
        f"<section><h2>Head to head: {esc(h.model_a)} vs {esc(h.model_b)}</h2>"
        "<p>Both models saw the same documents, so each document can be scored "
        "as a small contest.  Documents where both models have the same verdict "
        "cancel out of the rate difference.  An amendment can affect the models "
        "differently, and a filing influenced by one model can bias the result; "
        "pairing does not make the reference independent.  Click a "
        "win count to see exactly why the losing model failed those "
        "documents.</p>"
        f"{table}<p>{verdict}</p>{gold_note}</section>"
    )


def _comparison_summary_section(comparison: ModelComparison, dd: _Drilldowns) -> str:
    body = []
    for _, row in comparison.summary.iterrows():
        model = row["model"]
        ev = comparison.evaluations[model]
        critical = ev.comparisons[
            (ev.comparisons["status"] == MISMATCH)
            & (ev.comparisons["tier"] == ev.critical_tier)
        ]
        body.append([
            esc(model),
            f"{int(row['documents']):,}",
            _pct_with_ci(row["clean_document_rate"],
                         (row["clean_ci_low"], row["clean_ci_high"])),
            dd.link(f"{int(row['critical_mismatches']):,}",
                    f"{model}: all serious mismatches",
                    critical.sort_values("confidence", ascending=False),
                    COMPARISON_COLUMNS),
            f"{int(row['missed_items']):,}",
            f"{int(row['spurious_items']):,}",
            _pct(row["escaped_rate_at_080"]),
            _fmt(row["auroc"]),
            _fmt(row["ece"]),
        ])
    table = _raw_table(
        ["model", "documents", "clean doc rate [likely range]", "serious mismatches",
         "missed items", "spurious items", "escapes @ 0.80",
         "sorting power (AUROC)", "honesty gap (ECE)"],
        body,
    )
    return (
        "<section><h2>Model scorecards, side by side</h2>"
        f"<p>All numbers scored against {esc(comparison.reference)}.  The clean "
        "rate is the headline, but the character columns matter as much: a "
        "model can win on clean rate while inventing more goods lines "
        "(spurious items) or carrying a less trustworthy confidence score "
        "(sorting power) &mdash; different failure styles need different "
        "safety nets around them.</p>"
        f"{table}</section>"
    )


def _per_field_comparison_section(comparison: ModelComparison, dd: _Drilldowns) -> str:
    models = list(comparison.evaluations)
    body = []
    for _, row in comparison.per_field.iterrows():
        cells = [esc(row["field"]), esc(row["level"]), str(int(row["tier"]))]
        for model in models:
            ev = comparison.evaluations[model]
            mismatches = ev.comparisons[
                (ev.comparisons["field"] == row["field"])
                & (ev.comparisons["status"] == MISMATCH)
            ]
            cells.append(
                dd.link(_pct(row[model]),
                        f"{model}: mismatches on {row['field']}",
                        mismatches.sort_values("confidence", ascending=False),
                        COMPARISON_COLUMNS)
            )
        cells.append(f"{row['spread'] * 100:.1f} pp")
        body.append(cells)
    table = _raw_table(
        ["field", "level", "tier"]
        + [f"{esc(m)} mismatch rate" for m in models]
        + ["gap between models"],
        body,
    )
    return (
        "<section><h2>Where the choice of model actually matters</h2>"
        "<p>Mismatch rate per field, one column per model, sorted by how far "
        "the models are apart.  The fields at the top are the ones the A/B "
        "decision is really about; fields at the bottom would look the same "
        "whichever model you pick.</p>"
        f"{table}</section>"
    )


def _comparison_caveats(
    comparison: ModelComparison, gold_comparison: ModelComparison | None
) -> str:
    notes = list(comparison.warnings)
    if gold_comparison is not None:
        notes.extend(w for w in gold_comparison.warnings if w not in notes)
    notes += [
        "A fair A/B needs the same documents, the same reference records, the "
        "same field_tiers.yaml and the same comparator versions for every "
        "model.  The versions are recorded on every row precisely so this "
        "can be checked; violations are flagged at the top of this list.",
        "Per-model depth (built-in checks, confidence bands, country "
        "breakdowns) lives in each model's own full report; this page only "
        "compares.",
        "The overall rates and head-to-head comparison inherit the caveats "
        "of their reference.  Amendments may affect the models differently; "
        "a filing influenced by one model may also favor that model.",
    ]
    items = "".join(f"<li>{note}</li>" for note in notes)
    return (
        '<section class="caveats"><h2>Read this before declaring a winner</h2>'
        f"<ul>{items}</ul></section>"
    )


def build_comparison_report(
    comparison: ModelComparison,
    out_path: str | Path,
    gold_comparison: ModelComparison | None = None,
    generated_at: datetime | None = None,
) -> Path:
    dd = _Drilldowns()
    sections = [_comparison_tiles(comparison, dd)]
    for h in comparison.head_to_head:
        sections.append(_head_to_head_section(h, comparison, gold_comparison, dd))
    sections += [
        _comparison_summary_section(comparison, dd),
        _per_field_comparison_section(comparison, dd),
        _comparison_caveats(comparison, gold_comparison),
    ]
    return _write_page(
        out_path,
        "Extraction model comparison",
        f"all models scored against {esc(comparison.reference)} "
        "&middot; every number links to the rows behind it",
        sections,
        dd,
        generated_at,
    )


def _write_page(
    out_path: str | Path,
    title: str,
    meta: str,
    sections: list[str],
    dd: _Drilldowns,
    generated_at: datetime | None,
) -> Path:
    generated_at = generated_at or datetime.now()
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>{esc(title)}</h1>
<div class="meta">Generated {esc(generated_at.strftime('%Y-%m-%d %H:%M'))}
 &middot; {meta}</div>
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


def build_report(
    evaluation: Evaluation,
    validity_results: pd.DataFrame | None,
    out_path: str | Path,
    extras: ReportExtras | None = None,
    generated_at: datetime | None = None,
) -> Path:
    extras = extras or ReportExtras()
    dd = _Drilldowns()
    sections = [
        _story_section(evaluation, dd, extras),
        _headline(evaluation, dd, extras),
        _regimes_section(evaluation, extras, dd),
        _synthetic_review_section(evaluation, extras, dd),
        _alignment_section(evaluation, dd),
        _straight_through_section(
            evaluation, dd, synthetic=extras.gold_evaluation is not None),
        _leading_indicators_section(evaluation, extras, dd),
        _calibration_section(evaluation, dd),
        _confidence_quality_section(extras, dd),
        _per_field_section(evaluation, dd),
        _breakdown_section(evaluation, dd, extras.breakdown_ci),
        _worst_mismatches_section(evaluation),
        _validity_section(validity_results, dd),
        _caveats_section(extras.extra_notes, has_gold=extras.gold_evaluation is not None),
    ]
    return _write_page(
        out_path,
        "Extraction quality report",
        "every number links to the rows behind it &middot; full row-level data in the out/ CSVs",
        sections,
        dd,
        generated_at,
    )
