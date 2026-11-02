"""Privacy-conscious contract for human-review pilot telemetry.

The event table stores pseudonymous identifiers and references to separately
controlled value snapshots, never document values themselves.
"""

from __future__ import annotations

import pandas as pd

REVIEW_EVENT_SCHEMA_VERSION = "1.0"

EVENT_COLUMNS = (
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
)

EVENT_TYPES = {
    "document_opened",
    "prefill_exposed",
    "field_changed",
    "field_cleared",
    "interruption_started",
    "interruption_ended",
    "document_submitted",
    "document_abandoned",
}

_REQUIRED_IDENTIFIERS = (
    "schema_version",
    "study_id",
    "assignment_id",
    "doc_id",
    "reviewer_id",
    "event_id",
    "event_type",
    "arm",
    "model_version",
    "interface_version",
)
_FIELD_EVENTS = {"field_changed", "field_cleared"}
_STATE_EVENTS = {
    "prefill_exposed",
    "field_changed",
    "field_cleared",
    "document_submitted",
}
_TERMINAL_EVENTS = {"document_submitted", "document_abandoned"}
SUMMARY_COLUMNS = (
    "study_id",
    "assignment_id",
    "doc_id",
    "reviewer_id",
    "arm",
    "model_version",
    "interface_version",
    "exposure_status",
    "terminal_event",
    "field_changes",
    "field_clears",
    "elapsed_seconds",
    "recorded_interruption_seconds",
    "active_seconds_estimate",
    "timing_status",
)


def validate_review_events(events: pd.DataFrame) -> pd.DataFrame:
    """Validate an event table and return a copy with UTC timestamps.

    The strict column list prevents accidental ingestion of raw document or
    reviewer data. `state_ref` is an opaque reference to a separately secured
    snapshot, not a value or a URL.
    """
    missing = sorted(set(EVENT_COLUMNS) - set(events.columns))
    unknown = sorted(set(events.columns) - set(EVENT_COLUMNS))
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing columns: {', '.join(missing)}")
        if unknown:
            details.append(f"undeclared columns: {', '.join(unknown)}")
        raise ValueError("Invalid review event columns (" + "; ".join(details) + ")")

    result = events.loc[:, EVENT_COLUMNS].copy()
    for column in _REQUIRED_IDENTIFIERS:
        values = result[column].astype("string")
        if values.isna().any() or values.str.strip().eq("").any():
            raise ValueError(f"{column} must contain a non-empty value on every event")

    if not result["schema_version"].eq(REVIEW_EVENT_SCHEMA_VERSION).all():
        raise ValueError(
            f"schema_version must be {REVIEW_EVENT_SCHEMA_VERSION!r}; "
            "add an explicit migration before accepting another version"
        )
    if result["event_id"].duplicated().any():
        raise ValueError("event_id must be unique")
    invalid_types = sorted(set(result["event_type"]) - EVENT_TYPES)
    if invalid_types:
        raise ValueError(f"Unsupported event_type values: {', '.join(invalid_types)}")
    invalid_arms = sorted(set(result["arm"]) - {"prefill_on", "prefill_off"})
    if invalid_arms:
        raise ValueError(f"arm must be 'prefill_on' or 'prefill_off': {invalid_arms}")

    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True, errors="coerce")
    if result["timestamp"].isna().any():
        raise ValueError("timestamp must contain a valid date/time on every event")

    for event_type in _FIELD_EVENTS:
        field_rows = result["event_type"].eq(event_type)
        if result.loc[field_rows, "field_id"].isna().any() or result.loc[
            field_rows, "field_id"
        ].astype("string").str.strip().eq("").any():
            raise ValueError(f"field_id is required for {event_type} events")
    for event_type in _STATE_EVENTS:
        state_rows = result["event_type"].eq(event_type)
        if result.loc[state_rows, "state_ref"].isna().any() or result.loc[
            state_rows, "state_ref"
        ].astype("string").str.strip().eq("").any():
            raise ValueError(f"state_ref is required for {event_type} events")

    document_keys = ["study_id", "assignment_id", "doc_id"]
    stable_columns = [
        "reviewer_id",
        "arm",
        "model_version",
        "interface_version",
        "schema_version",
    ]
    for key, group in result.groupby(document_keys, sort=False, dropna=False):
        for column in stable_columns:
            if group[column].nunique(dropna=False) != 1:
                raise ValueError(
                    f"{column} must be consistent within study/assignment/document {key}"
                )
        if group["arm"].iloc[0] == "prefill_off" and group["event_type"].eq(
            "prefill_exposed"
        ).any():
            raise ValueError(f"prefill_off assignment was exposed to pre-fill: {key}")

    return result


def summarize_review_events(events: pd.DataFrame) -> pd.DataFrame:
    """Create one traceable workflow row per assigned document.

    Duration is elapsed time less explicitly recorded interruptions; it is
    only an estimate of active review time. Incomplete or ambiguous timing is
    retained with a status and missing duration, never silently counted as
    zero. This summary does not estimate pre-fill's causal effect or quality.
    """
    validated = validate_review_events(events)
    keys = ["study_id", "assignment_id", "doc_id"]
    stable = ["reviewer_id", "arm", "model_version", "interface_version"]
    rows = []

    for key, group in validated.groupby(keys, sort=False, dropna=False):
        group = group.sort_values("timestamp", kind="stable")
        event_types = group["event_type"]
        opens = group.loc[event_types.eq("document_opened"), "timestamp"]
        terminals = group.loc[event_types.isin(_TERMINAL_EVENTS)]

        timing_status = "complete"
        start = opens.iloc[0] if len(opens) == 1 else None
        end = terminals["timestamp"].iloc[0] if len(terminals) == 1 else None
        elapsed_seconds = pd.NA
        interruption_seconds = pd.NA
        active_seconds = pd.NA

        if len(opens) == 0:
            timing_status = "missing_start"
        elif len(opens) > 1:
            timing_status = "multiple_starts"
        elif len(terminals) == 0:
            timing_status = "missing_terminal"
        elif len(terminals) > 1:
            timing_status = "multiple_terminals"
        elif end < start:
            timing_status = "terminal_before_start"
        else:
            elapsed_seconds = (end - start).total_seconds()
            interruptions = group.loc[
                event_types.isin({"interruption_started", "interruption_ended"})
            ]
            interruption_start = None
            intervals = []
            invalid_interruptions = False
            for event in interruptions.itertuples(index=False):
                timestamp = event.timestamp
                if timestamp < start or timestamp > end:
                    invalid_interruptions = True
                    break
                if event.event_type == "interruption_started":
                    if interruption_start is not None:
                        invalid_interruptions = True
                        break
                    interruption_start = timestamp
                elif interruption_start is None:
                    invalid_interruptions = True
                    break
                else:
                    intervals.append((interruption_start, timestamp))
                    interruption_start = None
            if interruption_start is not None:
                invalid_interruptions = True

            if invalid_interruptions:
                timing_status = "invalid_interruptions"
            else:
                interruption_seconds = sum(
                    (interval_end - interval_start).total_seconds()
                    for interval_start, interval_end in intervals
                )
                active_seconds = elapsed_seconds - interruption_seconds

        assigned_arm = group["arm"].iloc[0]
        exposure_recorded = event_types.eq("prefill_exposed").any()
        exposure_status = (
            "exposed"
            if exposure_recorded
            else "not_assigned"
            if assigned_arm == "prefill_off"
            else "missing_exposure_event"
        )
        row = dict(zip(keys, key, strict=True))
        row.update({column: group[column].iloc[0] for column in stable})
        row.update(
            {
                "exposure_status": exposure_status,
                "terminal_event": (
                    terminals["event_type"].iloc[0] if len(terminals) == 1 else None
                ),
                "field_changes": int(event_types.eq("field_changed").sum()),
                "field_clears": int(event_types.eq("field_cleared").sum()),
                "elapsed_seconds": elapsed_seconds,
                "recorded_interruption_seconds": interruption_seconds,
                "active_seconds_estimate": active_seconds,
                "timing_status": timing_status,
            }
        )
        rows.append(row)

    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
