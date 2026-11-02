import pandas as pd
import pytest

from reviewer_events import summarize_review_events, validate_review_events


def event(event_id, event_type, timestamp, **overrides):
    row = {
        "schema_version": "1.0",
        "study_id": "pilot-1",
        "assignment_id": "assignment-1",
        "doc_id": "doc-1",
        "reviewer_id": "reviewer-token-1",
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "arm": "prefill_on",
        "model_version": "extractor-2",
        "interface_version": "ui-4",
        "field_id": None,
        "state_ref": None,
    }
    row.update(overrides)
    return row


def test_summary_uses_recorded_interruption_and_counts_actions():
    events = pd.DataFrame(
        [
            event("1", "document_opened", "2026-01-01T10:00:00Z"),
            event(
                "2",
                "prefill_exposed",
                "2026-01-01T10:00:02Z",
                state_ref="opaque-state-1",
            ),
            event(
                "3",
                "field_changed",
                "2026-01-01T10:01:00Z",
                field_id="invoice_number",
                state_ref="opaque-state-2",
            ),
            event("4", "interruption_started", "2026-01-01T10:02:00Z"),
            event("5", "interruption_ended", "2026-01-01T10:03:00Z"),
            event(
                "6",
                "field_cleared",
                "2026-01-01T10:04:00Z",
                field_id="country_of_origin",
                state_ref="opaque-state-3",
            ),
            event(
                "7",
                "document_submitted",
                "2026-01-01T10:05:00Z",
                state_ref="opaque-state-4",
            ),
        ]
    )

    summary = summarize_review_events(events)

    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["exposure_status"] == "exposed"
    assert row["field_changes"] == 1
    assert row["field_clears"] == 1
    assert row["elapsed_seconds"] == 300
    assert row["recorded_interruption_seconds"] == 60
    assert row["active_seconds_estimate"] == 240
    assert row["timing_status"] == "complete"


def test_missing_terminal_is_retained_without_zero_duration():
    events = pd.DataFrame(
        [event("1", "document_opened", "2026-01-01T10:00:00Z")]
    )

    row = summarize_review_events(events).iloc[0]

    assert row["exposure_status"] == "missing_exposure_event"
    assert row["timing_status"] == "missing_terminal"
    assert pd.isna(row["active_seconds_estimate"])


def test_empty_event_table_returns_a_stable_summary_schema():
    empty_events = pd.DataFrame(columns=(
        "schema_version",
        "study_id",
        "assignment_id",
        "doc_id",
        "reviewer_id",
        "event_id",
        "event_type",
        "timestamp",
        "arm",
        "model_version",
        "interface_version",
        "field_id",
        "state_ref",
    ))

    summary = summarize_review_events(empty_events)

    assert summary.empty
    assert "active_seconds_estimate" in summary.columns
    assert "timing_status" in summary.columns


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                event("1", "field_changed", "2026-01-01T10:00:00Z",
                      state_ref="opaque-state"),
            ],
            "field_id is required",
        ),
        (
            [
                event(
                    "1",
                    "prefill_exposed",
                    "2026-01-01T10:00:00Z",
                    state_ref="opaque-state",
                ),
            ],
            "prefill_off assignment was exposed",
        ),
        (
            [
                event("1", "document_opened", "2026-01-01T10:00:00Z"),
                event("1", "document_abandoned", "2026-01-01T10:01:00Z"),
            ],
            "event_id must be unique",
        ),
    ],
)
def test_validation_rejects_ambiguous_or_inconsistent_events(rows, message):
    if "prefill_off" in message:
        rows[0]["arm"] = "prefill_off"
    with pytest.raises(ValueError, match=message):
        validate_review_events(pd.DataFrame(rows))


def test_event_table_rejects_inline_document_values():
    events = pd.DataFrame(
        [
            event("1", "document_opened", "2026-01-01T10:00:00Z")
            | {"extracted_value": "sensitive document content"}
        ]
    )

    with pytest.raises(ValueError, match="undeclared columns: extracted_value"):
        validate_review_events(events)


def test_interruption_without_a_matching_end_does_not_create_active_time():
    events = pd.DataFrame(
        [
            event("1", "document_opened", "2026-01-01T10:00:00Z"),
            event("2", "interruption_started", "2026-01-01T10:01:00Z"),
            event(
                "3",
                "document_submitted",
                "2026-01-01T10:03:00Z",
                state_ref="opaque-final-state",
            ),
        ]
    )

    row = summarize_review_events(events).iloc[0]

    assert row["elapsed_seconds"] == 180
    assert pd.isna(row["recorded_interruption_seconds"])
    assert pd.isna(row["active_seconds_estimate"])
    assert row["timing_status"] == "invalid_interruptions"
