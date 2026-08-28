"""Field comparators: the only place that decides whether two values agree.

Each comparator answers one narrow question for one kind of field and
returns a status plus a human-readable reason.  The reason strings end up
in the report's drill-down tables, so they are written for people.

Statuses:
    MATCH           the two values agree under this comparator's rules
    MISMATCH        both values are present and they disagree
    NOT_COMPARABLE  we cannot honestly say either way (a value is missing
                    or unparseable) -- never silently counted as a match

Comparators are versioned.  When the comparison logic of one changes, bump
that comparator's VERSION.  Every comparison row records the comparator's
name, parameters and version, so a shift in a metric can be attributed to
a logic change rather than a data change.

Which comparator applies to which field, and with which parameters, lives
in field_tiers.yaml -- nothing here knows about specific fields.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass
from datetime import date

from reference_data import COUNTRY_ALIASES, ISO_COUNTRIES

MATCH = "match"
MISMATCH = "mismatch"
NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class ComparisonResult:
    status: str  # one of MATCH / MISMATCH / NOT_COMPARABLE
    reason: str  # plain-language explanation, shown in drill-down tables


def is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


class Comparator:
    """Base class: handles missing values, subclasses compare present ones."""

    name: str = "?"
    VERSION: str = "?"

    def __init__(self):
        self.params: dict = {}

    def describe(self) -> str:
        """E.g. "numeric_with_tolerance(pct=0.5) v1.0" -- used in reports."""
        params = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({params}) v{self.VERSION}"

    def compare(self, extracted, filed) -> ComparisonResult:
        extracted_missing = is_missing(extracted)
        filed_missing = is_missing(filed)
        if extracted_missing and filed_missing:
            return ComparisonResult(NOT_COMPARABLE, "both values missing")
        if extracted_missing:
            return ComparisonResult(
                NOT_COMPARABLE, "extracted value missing, filed value present"
            )
        if filed_missing:
            return ComparisonResult(
                NOT_COMPARABLE, "filed value missing, extracted value present"
            )
        return self._compare_present(extracted, filed)

    def _compare_present(self, extracted, filed) -> ComparisonResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# exact_code
# ---------------------------------------------------------------------------

_CODE_JUNK = re.compile(r"[\s.\-]+")


class ExactCode(Comparator):
    """Exact match after light normalisation (case, whitespace, dots, dashes).

    For codes like HS codes, incoterms, currencies, EORI numbers:
    "8471.30.0000" and "8471300000" are the same code, "cif" and "CIF" are
    the same incoterm.  Anything beyond that formatting difference is a
    mismatch.
    """

    name = "exact_code"
    VERSION = "1.0"

    @staticmethod
    def _normalise(value) -> str:
        return _CODE_JUNK.sub("", str(value)).upper()

    def _compare_present(self, extracted, filed) -> ComparisonResult:
        a, b = self._normalise(extracted), self._normalise(filed)
        if a == b:
            return ComparisonResult(MATCH, f"'{extracted}' == '{filed}' after normalisation")
        return ComparisonResult(MISMATCH, f"'{extracted}' != '{filed}'")


# ---------------------------------------------------------------------------
# numeric_with_tolerance
# ---------------------------------------------------------------------------


def parse_number(value) -> float | None:
    """Parse a number as sources tend to write them ("12,500.00")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


class NumericWithTolerance(Comparator):
    """Numbers match when they differ by at most `pct` percent of the filed
    value.  pct=0 means exact numeric equality (still tolerant of formatting:
    "12" and "12.0" match).  A filed value of exactly 0 requires an extracted
    value of exactly 0, since a relative tolerance is meaningless there.
    """

    name = "numeric_with_tolerance"
    VERSION = "1.0"

    def __init__(self, pct: float = 0.0):
        super().__init__()
        self.pct = float(pct)
        self.params = {"pct": self.pct}

    def _compare_present(self, extracted, filed) -> ComparisonResult:
        a, b = parse_number(extracted), parse_number(filed)
        if a is None:
            return ComparisonResult(
                NOT_COMPARABLE, f"could not parse extracted value '{extracted}' as a number"
            )
        if b is None:
            return ComparisonResult(
                NOT_COMPARABLE, f"could not parse filed value '{filed}' as a number"
            )
        if b == 0:
            if a == 0:
                return ComparisonResult(MATCH, "both values are 0")
            return ComparisonResult(MISMATCH, f"filed value is 0 but extracted is {a}")
        diff_pct = abs(a - b) / abs(b) * 100.0
        if diff_pct <= self.pct + 1e-9:
            return ComparisonResult(
                MATCH, f"{a} vs {b}: differs by {diff_pct:.3f}%, within {self.pct}% tolerance"
            )
        return ComparisonResult(
            MISMATCH, f"{a} vs {b}: differs by {diff_pct:.2f}%, tolerance is {self.pct}%"
        )


# ---------------------------------------------------------------------------
# normalised_date
# ---------------------------------------------------------------------------

_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_PART_SPLIT = re.compile(r"[/.\-]")


@dataclass(frozen=True)
class ParsedDate:
    value: date
    ambiguous: bool  # True when the day/month order had to be assumed


def parse_date(raw, dayfirst: bool = True) -> ParsedDate | None:
    """Normalise the date formats sources actually produce.

    Accepts ISO (2026-04-05, unambiguous) and three-part numeric dates with
    /, . or - separators.  For the latter, the order is inferred when one
    part is > 12; otherwise the `dayfirst` preference decides and the result
    is flagged ambiguous.  Returns None for anything unparseable -- callers
    must treat that as not comparable, never as a match.

    This function is also used by validity.invoice_date_plausible so that
    the harness has exactly one opinion about what a date string means.
    """
    text = str(raw).strip()
    m = _ISO_DATE.match(text)
    if m:
        try:
            return ParsedDate(date(int(m[1]), int(m[2]), int(m[3])), ambiguous=False)
        except ValueError:
            return None
    parts = _PART_SPLIT.split(text)
    if len(parts) != 3:
        return None
    try:
        first, second, year = (int(p) for p in parts)
    except ValueError:
        return None
    if not 1900 <= year <= 2200:
        return None
    if first > 12 >= second:
        day, month, ambiguous = first, second, False
    elif second > 12 >= first:
        day, month, ambiguous = second, first, False
    elif first <= 12 and second <= 12:
        day, month = (first, second) if dayfirst else (second, first)
        ambiguous = first != second  # 05/05/2026 is the same date either way
    else:
        return None  # both parts > 12: not a real date
    try:
        return ParsedDate(date(year, month, day), ambiguous=ambiguous)
    except ValueError:
        return None


def _swap_day_month(d: date) -> date | None:
    try:
        return date(d.year, d.day, d.month)
    except ValueError:
        return None


class NormalisedDate(Comparator):
    """Dates match when they normalise to the same calendar day.

    Formatting differences ("05/04/2026" vs "2026-04-05") are not errors.
    When a mismatch would disappear if day and month were swapped, the
    reason says so explicitly -- that pattern is the classic DD/MM vs MM/DD
    extraction error and deserves to be visible, not buried in a generic
    "dates differ".
    """

    name = "normalised_date"
    VERSION = "1.0"

    def __init__(self, dayfirst: bool = True):
        super().__init__()
        self.dayfirst = bool(dayfirst)
        self.params = {"dayfirst": self.dayfirst}

    def _compare_present(self, extracted, filed) -> ComparisonResult:
        pe = parse_date(extracted, dayfirst=self.dayfirst)
        pf = parse_date(filed, dayfirst=self.dayfirst)
        if pe is None:
            return ComparisonResult(
                NOT_COMPARABLE, f"could not parse extracted value '{extracted}' as a date"
            )
        if pf is None:
            return ComparisonResult(
                NOT_COMPARABLE, f"could not parse filed value '{filed}' as a date"
            )
        if pe.value == pf.value:
            note = " (day/month order assumed)" if pe.ambiguous or pf.ambiguous else ""
            return ComparisonResult(MATCH, f"both normalise to {pe.value.isoformat()}{note}")
        swapped = _swap_day_month(pe.value)
        if swapped is not None and swapped == pf.value:
            return ComparisonResult(
                MISMATCH,
                f"{pe.value.isoformat()} vs {pf.value.isoformat()}: values agree only if "
                "day and month are swapped -- likely DD/MM vs MM/DD confusion",
            )
        return ComparisonResult(
            MISMATCH,
            f"normalise to different dates: {pe.value.isoformat()} vs {pf.value.isoformat()}",
        )


# ---------------------------------------------------------------------------
# iso_country
# ---------------------------------------------------------------------------


def normalise_country(value) -> str | None:
    """Uppercase, map a short list of spelled-out names, then require a valid
    ISO 3166-1 alpha-2 code.  Returns None when the value is not recognisable
    as a country -- that is not comparable, not a mismatch."""
    text = str(value).strip().upper()
    text = COUNTRY_ALIASES.get(text, text)
    return text if text in ISO_COUNTRIES else None


class IsoCountry(Comparator):
    """Country codes match when both normalise to the same alpha-2 code."""

    name = "iso_country"
    VERSION = "1.0"

    def _compare_present(self, extracted, filed) -> ComparisonResult:
        a, b = normalise_country(extracted), normalise_country(filed)
        if a is None:
            return ComparisonResult(
                NOT_COMPARABLE,
                f"extracted value '{extracted}' is not a recognised ISO 3166 country",
            )
        if b is None:
            return ComparisonResult(
                NOT_COMPARABLE,
                f"filed value '{filed}' is not a recognised ISO 3166 country",
            )
        if a == b:
            return ComparisonResult(MATCH, f"both normalise to {a}")
        return ComparisonResult(MISMATCH, f"{a} != {b} (from '{extracted}' vs '{filed}')")


# ---------------------------------------------------------------------------
# fuzzy_name
# ---------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^\w\s]")


class FuzzyName(Comparator):
    """Free-text values match when their similarity ratio meets `threshold`.

    Similarity is difflib.SequenceMatcher.ratio() on lowercased text with
    punctuation stripped and whitespace collapsed -- deliberately boring and
    inspectable, and the ratio appears in every reason string so a human can
    sanity-check the threshold.
    """

    name = "fuzzy_name"
    VERSION = "1.0"

    def __init__(self, threshold: float = 0.9):
        super().__init__()
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")
        self.threshold = float(threshold)
        self.params = {"threshold": self.threshold}

    @staticmethod
    def _normalise(value) -> str:
        return " ".join(_NON_WORD.sub(" ", str(value).casefold()).split())

    def _compare_present(self, extracted, filed) -> ComparisonResult:
        a, b = self._normalise(extracted), self._normalise(filed)
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        if ratio >= self.threshold:
            return ComparisonResult(
                MATCH, f"similarity {ratio:.3f} >= threshold {self.threshold}"
            )
        return ComparisonResult(
            MISMATCH,
            f"'{extracted}' vs '{filed}': similarity {ratio:.3f} below threshold {self.threshold}",
        )


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, type[Comparator]] = {
    ExactCode.name: ExactCode,
    NumericWithTolerance.name: NumericWithTolerance,
    NormalisedDate.name: NormalisedDate,
    IsoCountry.name: IsoCountry,
    FuzzyName.name: FuzzyName,
}


def from_config(name: str, params: dict | None = None) -> Comparator:
    """Build a comparator from a field_tiers.yaml entry."""
    if name not in REGISTRY:
        raise KeyError(f"unknown comparator '{name}'; known: {sorted(REGISTRY)}")
    return REGISTRY[name](**(params or {}))
