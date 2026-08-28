"""Deterministic validity rules that need no ground truth.

These run against extracted records on their own, before (and independent
of) any comparison with filed records.  A record can agree perfectly with
the filed record and still be invalid (both wrong), or disagree and still
be valid -- validity and accuracy are different questions and are kept in
different modules on purpose.

Each rule is a small named function returning a RuleResult: the rule name,
pass/fail, and a plain-language reason.  A missing required value fails the
rule (a customs declaration without an origin country is not valid), and
the reason says so, so nothing is silently skipped.

One rule, invoice_lines_sum_to_total, needs invoice line items, which the
canonical record deliberately does not carry.  It takes the line amounts
directly; run_demo.py feeds it from the raw source document (see
readers/globex_xml.read_invoice_lines).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from canonical import CanonicalRecord
from comparators import parse_date, parse_number
from reference_data import ISO_COUNTRIES, ISO_CURRENCIES

# Plausible invoice date window.  Older than this is almost certainly an
# OCR/parse error rather than a real invoice; a little future slack allows
# for invoices dated slightly ahead of processing.
EARLIEST_PLAUSIBLE_DATE = date(2015, 1, 1)
FUTURE_SLACK = timedelta(days=45)


@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    reason: str


def _ok(rule: str, reason: str) -> RuleResult:
    return RuleResult(rule, True, reason)


def _fail(rule: str, reason: str) -> RuleResult:
    return RuleResult(rule, False, reason)


def _clean_code(value) -> str:
    """Strip whitespace, dots and dashes so '8471.30.0000' reads as digits."""
    if value is None:
        return ""
    return re.sub(r"[\s.\-]+", "", str(value))


# ---------------------------------------------------------------------------
# individual rules
# ---------------------------------------------------------------------------


def hs_code_format(record: CanonicalRecord) -> RuleResult:
    """An HS code must be 8 digits (CN) or 10 digits (TARIC).  A 6-digit
    code is a classic truncation error: valid internationally, not valid on
    an EU declaration."""
    rule = "hs_code_format"
    code = _clean_code(record.hs_code)
    if not code:
        return _fail(rule, "hs_code is missing")
    if not code.isdigit():
        return _fail(rule, f"'{record.hs_code}' contains non-digit characters")
    if len(code) not in (8, 10):
        return _fail(rule, f"'{record.hs_code}' has {len(code)} digits, expected 8 or 10")
    return _ok(rule, f"'{code}' is a well-formed {len(code)}-digit code")


def hs_code_in_tariff(record: CanonicalRecord, tariff_codes: frozenset[str]) -> RuleResult:
    """The code must appear in the supplied tariff list (see
    load_tariff_codes).  Format errors fail here too, which is fine: the
    two rules answer different questions and a human reads both reasons."""
    rule = "hs_code_in_tariff"
    code = _clean_code(record.hs_code)
    if not code:
        return _fail(rule, "hs_code is missing")
    if code in tariff_codes:
        return _ok(rule, f"'{code}' found in tariff list")
    return _fail(rule, f"'{code}' not found in tariff list ({len(tariff_codes)} codes)")


def country_is_iso(record: CanonicalRecord) -> RuleResult:
    rule = "country_is_iso"
    value = (record.country or "").strip().upper()
    if not value:
        return _fail(rule, "country is missing")
    if value in ISO_COUNTRIES:
        return _ok(rule, f"'{value}' is a valid ISO 3166 code")
    return _fail(rule, f"'{record.country}' is not a valid ISO 3166 alpha-2 code")


def origin_country_is_iso(record: CanonicalRecord) -> RuleResult:
    rule = "origin_country_is_iso"
    value = (record.origin_country or "").strip().upper()
    if not value:
        return _fail(rule, "origin_country is missing")
    if value in ISO_COUNTRIES:
        return _ok(rule, f"'{value}' is a valid ISO 3166 code")
    return _fail(rule, f"'{record.origin_country}' is not a valid ISO 3166 alpha-2 code")


def currency_is_iso(record: CanonicalRecord) -> RuleResult:
    rule = "currency_is_iso"
    value = (record.currency or "").strip().upper()
    if not value:
        return _fail(rule, "currency is missing")
    if value in ISO_CURRENCIES:
        return _ok(rule, f"'{value}' is a recognised ISO 4217 code")
    return _fail(rule, f"'{record.currency}' is not a recognised ISO 4217 code")


def luhn_check_digit(digits: str) -> int:
    """Standard Luhn check digit over a string of digits."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:  # rightmost payload digit gets doubled
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


_EORI_FORMAT = re.compile(r"^([A-Z]{2})(\d{8})(\d)$")


def eori_checksum(record: CanonicalRecord) -> RuleResult:
    """Format + checksum for our synthetic EORI scheme: two-letter country
    prefix, eight digits, one Luhn check digit.

    Honesty note: real EORI numbers have national formats and no universal
    checksum -- real validation is a lookup against the EU EOS registry.
    This rule demonstrates the shape a checksum rule takes; swap the body
    for a registry client in production.
    """
    rule = "eori_checksum"
    value = (record.importer_eori or "").strip().upper()
    if not value:
        return _fail(rule, "importer_eori is missing")
    m = _EORI_FORMAT.match(value)
    if not m:
        return _fail(rule, f"'{record.importer_eori}' does not match CC + 9 digits")
    country, payload, check = m.groups()
    if country not in ISO_COUNTRIES:
        return _fail(rule, f"'{country}' prefix is not a valid ISO country")
    expected = luhn_check_digit(payload)
    if int(check) != expected:
        return _fail(rule, f"check digit is {check}, expected {expected}")
    return _ok(rule, "format and check digit are valid")


def invoice_date_plausible(record: CanonicalRecord, today: date | None = None) -> RuleResult:
    """The invoice date must parse and fall in a plausible window
    (EARLIEST_PLAUSIBLE_DATE .. today + FUTURE_SLACK)."""
    rule = "invoice_date_plausible"
    if record.invoice_date is None or str(record.invoice_date).strip() == "":
        return _fail(rule, "invoice_date is missing")
    parsed = parse_date(record.invoice_date)
    if parsed is None:
        return _fail(rule, f"'{record.invoice_date}' could not be parsed as a date")
    today = today or date.today()
    latest = today + FUTURE_SLACK
    if parsed.value < EARLIEST_PLAUSIBLE_DATE:
        return _fail(
            rule, f"{parsed.value.isoformat()} is before {EARLIEST_PLAUSIBLE_DATE.isoformat()}"
        )
    if parsed.value > latest:
        return _fail(rule, f"{parsed.value.isoformat()} is after {latest.isoformat()} (future)")
    return _ok(rule, f"{parsed.value.isoformat()} is in the plausible window")


def net_weight_not_exceeding_gross(record: CanonicalRecord) -> RuleResult:
    """Net weight cannot exceed gross weight -- the packaging weighs
    something.  A tiny relative slack absorbs rounding in source systems."""
    rule = "net_weight_not_exceeding_gross"
    net, gross = parse_number(record.net_weight), parse_number(record.gross_weight)
    if net is None:
        return _fail(rule, "net_weight is missing or not a number")
    if gross is None:
        return _fail(rule, "gross_weight is missing or not a number")
    if net <= gross * 1.0001:
        return _ok(rule, f"net {net} <= gross {gross}")
    return _fail(rule, f"net {net} exceeds gross {gross}")


def invoice_lines_sum_to_total(
    line_amounts: list[float], total, tolerance_pct: float = 0.1
) -> RuleResult:
    """Invoice line amounts must add up to the invoice total, within a small
    tolerance for rounding.  Operates on raw line amounts because the
    canonical record carries only the total -- see the module docstring."""
    rule = "invoice_lines_sum_to_total"
    parsed_total = parse_number(total)
    if parsed_total is None:
        return _fail(rule, "invoice total is missing or not a number")
    if not line_amounts:
        return _fail(rule, "no invoice line items found")
    line_sum = sum(line_amounts)
    allowed = max(0.01, abs(parsed_total) * tolerance_pct / 100.0)
    diff = abs(line_sum - parsed_total)
    if diff <= allowed:
        return _ok(rule, f"{len(line_amounts)} lines sum to {line_sum:.2f} == total {parsed_total:.2f}")
    return _fail(
        rule,
        f"{len(line_amounts)} lines sum to {line_sum:.2f} but total is {parsed_total:.2f} "
        f"(off by {diff:.2f})",
    )


# ---------------------------------------------------------------------------
# runners / helpers
# ---------------------------------------------------------------------------


def run_record_rules(
    record: CanonicalRecord, tariff_codes: frozenset[str]
) -> list[RuleResult]:
    """Run every record-level rule against one record."""
    return [
        hs_code_format(record),
        hs_code_in_tariff(record, tariff_codes),
        country_is_iso(record),
        origin_country_is_iso(record),
        currency_is_iso(record),
        eori_checksum(record),
        invoice_date_plausible(record),
        net_weight_not_exceeding_gross(record),
    ]


def load_tariff_codes(path: str | Path) -> frozenset[str]:
    """Load the tariff list: a one-column CSV of valid HS codes."""
    codes = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = _clean_code(row["hs_code"])
            if code:
                codes.add(code)
    return frozenset(codes)
