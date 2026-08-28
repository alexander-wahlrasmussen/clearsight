"""Deterministic validity rules that need no ground truth.

These run against extracted records on their own, before (and independent
of) any comparison with filed records.  A record can agree perfectly with
the filed record and still be invalid (both wrong), or disagree and still
be valid -- validity and accuracy are different questions and are kept in
different modules on purpose.

Rules come in two shapes:
  - header rules take a CanonicalRecord (EORI, currency, dates, and the
    cross-check that goods item values sum to the invoice total);
  - item rules take one CanonicalItem and run once per goods item
    (HS code checks, origin, units, weights).

Each rule is a small named function returning a RuleResult: the rule name,
pass/fail, and a plain-language reason.  A missing required value fails the
rule (a customs declaration without an origin country is not valid), and
the reason says so, so nothing is silently skipped.

The item-sum rule deserves a note: because it is pure arithmetic on the
extracted record, it catches a whole class of STRUCTURAL extraction errors
before any filed record exists -- a dropped goods item leaves the items
short of the invoice total.  (A merge of two items preserves the sum and
sails through; see the README.)
"""

from __future__ import annotations

import csv
import dataclasses
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from canonical import CanonicalItem, CanonicalRecord
from comparators import parse_date, parse_number
from reference_data import (
    EORI_FORMATS,
    ISO_COUNTRIES,
    ISO_CURRENCIES,
    VALID_QUANTITY_UNITS,
    expected_quantity_unit,
)

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
    item_number: Optional[int] = None  # set for item-level rules


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
# header rules (one per record)
# ---------------------------------------------------------------------------


def country_is_iso(record: CanonicalRecord) -> RuleResult:
    rule = "country_is_iso"
    value = (record.country or "").strip().upper()
    if not value:
        return _fail(rule, "country is missing")
    if value in ISO_COUNTRIES:
        return _ok(rule, f"'{value}' is a valid ISO 3166 code")
    return _fail(rule, f"'{record.country}' is not a valid ISO 3166 alpha-2 code")


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


_EORI_SHAPE = re.compile(r"^([A-Z]{2})(\d+)$")


def eori_checksum(record: CanonicalRecord) -> RuleResult:
    """National format plus checksum for the importer EORI.

    The per-country digit counts in reference_data.EORI_FORMATS are real
    (Germany issues 15-digit numbers, the Netherlands uses the 9-digit
    RSIN).  The Luhn check digit at the end is NOT real -- actual EORI
    numbers have no universal checksum, and real validation is a lookup
    against the EU EOS registry.  The rule demonstrates the shape such a
    rule takes; swap the checksum for a registry client in production.
    """
    rule = "eori_checksum"
    value = (record.importer_eori or "").strip().upper()
    if not value:
        return _fail(rule, "importer_eori is missing")
    m = _EORI_SHAPE.match(value)
    if not m:
        return _fail(rule, f"'{record.importer_eori}' is not country prefix + digits")
    country, digits = m.groups()
    expected_length = EORI_FORMATS.get(country)
    if expected_length is None:
        return _fail(rule, f"no known national EORI format for prefix '{country}'")
    if len(digits) != expected_length:
        return _fail(
            rule,
            f"'{country}' EORI numbers have {expected_length} digits, this one has {len(digits)}",
        )
    expected_check = luhn_check_digit(digits[:-1])
    if int(digits[-1]) != expected_check:
        return _fail(rule, f"check digit is {digits[-1]}, expected {expected_check}")
    return _ok(rule, f"valid {country} format ({expected_length} digits) and check digit")


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


def item_values_sum_to_invoice_total(
    record: CanonicalRecord, tolerance_pct: float = 0.1
) -> RuleResult:
    """Goods item values must add up to the header invoice total, within a
    small rounding tolerance.  Pure arithmetic on the extracted record, so
    it flags dropped or invented items with no ground truth needed."""
    rule = "item_values_sum_to_invoice_total"
    total = parse_number(record.declared_value)
    if total is None:
        return _fail(rule, "invoice total (declared_value) is missing or not a number")
    if not record.items:
        return _fail(rule, "record has no goods items")
    values = [parse_number(item.item_value) for item in record.items]
    if any(value is None for value in values):
        return _fail(rule, "one or more item values are missing or not numbers")
    item_sum = sum(values)
    allowed = max(0.01, abs(total) * tolerance_pct / 100.0)
    diff = abs(item_sum - total)
    if diff <= allowed:
        return _ok(
            rule, f"{len(values)} item values sum to {item_sum:.2f} == total {total:.2f}"
        )
    return _fail(
        rule,
        f"{len(values)} item values sum to {item_sum:.2f} but invoice total is "
        f"{total:.2f} (off by {diff:.2f} -- a dropped, invented or misread item?)",
    )


# ---------------------------------------------------------------------------
# item rules (one per goods item)
# ---------------------------------------------------------------------------


def hs_code_format(item: CanonicalItem) -> RuleResult:
    """An HS code must be 8 digits (CN) or 10 digits (TARIC).  A 6-digit
    code is a classic truncation error: valid internationally, not valid on
    an EU declaration."""
    rule = "hs_code_format"
    code = _clean_code(item.hs_code)
    if not code:
        return _fail(rule, "hs_code is missing")
    if not code.isdigit():
        return _fail(rule, f"'{item.hs_code}' contains non-digit characters")
    if len(code) not in (8, 10):
        return _fail(rule, f"'{item.hs_code}' has {len(code)} digits, expected 8 or 10")
    return _ok(rule, f"'{code}' is a well-formed {len(code)}-digit code")


def hs_code_in_tariff(item: CanonicalItem, tariff_codes: frozenset[str]) -> RuleResult:
    """The code must appear in the supplied tariff list (see
    load_tariff_codes).  Format errors fail here too, which is fine: the
    two rules answer different questions and a human reads both reasons."""
    rule = "hs_code_in_tariff"
    code = _clean_code(item.hs_code)
    if not code:
        return _fail(rule, "hs_code is missing")
    if code in tariff_codes:
        return _ok(rule, f"'{code}' found in tariff list")
    return _fail(rule, f"'{code}' not found in tariff list ({len(tariff_codes)} codes)")


def origin_country_is_iso(item: CanonicalItem) -> RuleResult:
    rule = "origin_country_is_iso"
    value = (item.origin_country or "").strip().upper()
    if not value:
        return _fail(rule, "origin_country is missing")
    if value in ISO_COUNTRIES:
        return _ok(rule, f"'{value}' is a valid ISO 3166 code")
    return _fail(rule, f"'{item.origin_country}' is not a valid ISO 3166 alpha-2 code")


def quantity_unit_expected(item: CanonicalItem) -> RuleResult:
    """The quantity must be expressed in the supplementary unit the HS
    chapter prescribes (footwear in pairs, beverages in litres, bulk goods
    as net mass).  A bare number in the wrong unit is not a quantity."""
    rule = "quantity_unit_expected"
    unit = (item.quantity_unit or "").strip().upper()
    if not unit:
        return _fail(rule, "quantity_unit is missing")
    if unit not in VALID_QUANTITY_UNITS:
        return _fail(rule, f"'{item.quantity_unit}' is not a known unit code")
    expected = expected_quantity_unit(item.hs_code)
    if expected is None:
        return _fail(rule, "cannot determine expected unit: hs_code unusable")
    if unit == expected:
        return _ok(rule, f"'{unit}' matches what chapter {_clean_code(item.hs_code)[:2]} prescribes")
    return _fail(
        rule,
        f"chapter {_clean_code(item.hs_code)[:2]} prescribes {expected}, got {unit}",
    )


def net_weight_not_exceeding_gross(item: CanonicalItem) -> RuleResult:
    """Net weight cannot exceed gross weight -- the packaging weighs
    something.  A tiny relative slack absorbs rounding in source systems."""
    rule = "net_weight_not_exceeding_gross"
    net, gross = parse_number(item.net_weight), parse_number(item.gross_weight)
    if net is None:
        return _fail(rule, "net_weight is missing or not a number")
    if gross is None:
        return _fail(rule, "gross_weight is missing or not a number")
    if net <= gross * 1.0001:
        return _ok(rule, f"net {net} <= gross {gross}")
    return _fail(rule, f"net {net} exceeds gross {gross}")


# ---------------------------------------------------------------------------
# runners / helpers
# ---------------------------------------------------------------------------


def run_record_rules(
    record: CanonicalRecord, tariff_codes: frozenset[str]
) -> list[RuleResult]:
    """Run every header rule once and every item rule per goods item.
    Item-level results carry the item_number they refer to."""
    results = [
        country_is_iso(record),
        currency_is_iso(record),
        eori_checksum(record),
        invoice_date_plausible(record),
        item_values_sum_to_invoice_total(record),
    ]
    for item in record.items:
        for result in (
            hs_code_format(item),
            hs_code_in_tariff(item, tariff_codes),
            origin_country_is_iso(item),
            quantity_unit_expected(item),
            net_weight_not_exceeding_gross(item),
        ):
            results.append(dataclasses.replace(result, item_number=item.item_number))
    return results


def load_tariff_codes(path: str | Path) -> frozenset[str]:
    """Load the tariff list: a one-column CSV of valid HS codes."""
    codes = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = _clean_code(row["hs_code"])
            if code:
                codes.add(code)
    return frozenset(codes)
