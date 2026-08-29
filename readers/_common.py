"""Tiny parsing helpers shared by readers.

Readers stay independent of each other; they only share these leaf
utilities for turning raw strings into canonical field values.
"""

from __future__ import annotations

from datetime import datetime

from comparators import parse_number


def text_or_none(value) -> str | None:
    """Empty / whitespace-only strings become None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def float_or_none(value) -> float | None:
    """Delegates to comparators.parse_number so the whole harness has
    exactly one opinion about what a number string means (including
    European decimal commas)."""
    text = text_or_none(value)
    if text is None:
        return None
    return parse_number(text)


def int_or_none(value) -> int | None:
    number = float_or_none(value)
    if number is None:
        return None
    return int(number)


def datetime_or_none(value) -> datetime | None:
    text = text_or_none(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
