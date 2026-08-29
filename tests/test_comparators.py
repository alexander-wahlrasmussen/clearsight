import pytest

from comparators import (
    MATCH,
    MISMATCH,
    NOT_COMPARABLE,
    ExactCode,
    FuzzyName,
    IsoCountry,
    NormalisedDate,
    NumericWithTolerance,
    from_config,
    parse_date,
)


class TestExactCode:
    def test_identical(self):
        assert ExactCode().compare("CIF", "CIF").status == MATCH

    def test_normalises_case_and_punctuation(self):
        assert ExactCode().compare("8471.30.0000", "8471300000").status == MATCH
        assert ExactCode().compare("cif", "CIF").status == MATCH
        assert ExactCode().compare(" DE123456782 ", "DE123456782").status == MATCH

    def test_different_codes_mismatch(self):
        result = ExactCode().compare("CIF", "CFR")
        assert result.status == MISMATCH
        assert "CIF" in result.reason and "CFR" in result.reason

    def test_missing_values_not_comparable(self):
        assert ExactCode().compare(None, "CIF").status == NOT_COMPARABLE
        assert ExactCode().compare("CIF", "").status == NOT_COMPARABLE
        assert ExactCode().compare(None, None).status == NOT_COMPARABLE

    def test_missing_side_named_in_reason(self):
        assert "extracted" in ExactCode().compare(None, "CIF").reason
        assert "filed" in ExactCode().compare("CIF", None).reason


class TestParseNumber:
    def test_english_and_european_formats_agree(self):
        from comparators import parse_number

        assert parse_number("12,500.00") == 12500.0   # English grouping
        assert parse_number("12.500,00") == 12500.0   # European grouping
        assert parse_number("1234,56") == 1234.56     # lone comma = decimal mark
        assert parse_number("1,234") == 1234.0        # perfect 3-digit groups = grouping
        assert parse_number("12,34") == 12.34         # not groups of three = decimal
        assert parse_number("1.234") == 1.234         # lone dot stays a decimal mark

    def test_garbage_is_none_not_a_number(self):
        from comparators import parse_number

        assert parse_number("1,2,3") is None
        assert parse_number("12x34") is None
        assert parse_number(True) is None


class TestNumericWithTolerance:
    def test_exact_match_across_formats(self):
        comparator = NumericWithTolerance(pct=0.0)
        assert comparator.compare("12", "12.0").status == MATCH
        assert comparator.compare("12,500.00", 12500).status == MATCH
        assert comparator.compare("12.500,00", 12500).status == MATCH

    def test_within_tolerance(self):
        assert NumericWithTolerance(pct=0.5).compare(100.5, 100.0).status == MATCH

    def test_outside_tolerance(self):
        result = NumericWithTolerance(pct=0.5).compare(101.0, 100.0)
        assert result.status == MISMATCH
        assert "tolerance" in result.reason

    def test_zero_filed_requires_zero_extracted(self):
        comparator = NumericWithTolerance(pct=5.0)
        assert comparator.compare(0, 0).status == MATCH
        assert comparator.compare(0.01, 0).status == MISMATCH

    def test_unparseable_not_comparable(self):
        result = NumericWithTolerance(pct=1.0).compare("12x34", 100)
        assert result.status == NOT_COMPARABLE
        assert "extracted" in result.reason


class TestParseDate:
    def test_iso_is_unambiguous(self):
        parsed = parse_date("2026-04-05")
        assert (parsed.value.isoformat(), parsed.ambiguous) == ("2026-04-05", False)

    def test_day_over_twelve_is_unambiguous(self):
        parsed = parse_date("13/04/2026")
        assert (parsed.value.isoformat(), parsed.ambiguous) == ("2026-04-13", False)

    def test_ambiguous_uses_dayfirst_preference(self):
        assert parse_date("04/05/2026", dayfirst=True).value.isoformat() == "2026-05-04"
        assert parse_date("04/05/2026", dayfirst=False).value.isoformat() == "2026-04-05"
        assert parse_date("04/05/2026").ambiguous is True

    def test_same_day_and_month_not_ambiguous(self):
        assert parse_date("05/05/2026").ambiguous is False

    def test_invalid_dates_return_none(self):
        assert parse_date("31/02/2026") is None
        assert parse_date("not a date") is None
        assert parse_date("13/13/2026") is None


class TestNormalisedDate:
    def test_format_difference_is_not_an_error(self):
        result = NormalisedDate(dayfirst=True).compare("05/04/2026", "2026-04-05")
        assert result.status == MATCH

    def test_dot_separated_format(self):
        assert NormalisedDate().compare("05.04.2026", "2026-04-05").status == MATCH

    def test_swapped_day_month_flagged_in_reason(self):
        result = NormalisedDate(dayfirst=True).compare("04/05/2026", "2026-04-05")
        assert result.status == MISMATCH
        assert "swapped" in result.reason

    def test_plain_different_dates(self):
        result = NormalisedDate().compare("2026-04-05", "2026-07-20")
        assert result.status == MISMATCH
        assert "swapped" not in result.reason

    def test_unparseable_not_comparable(self):
        assert NormalisedDate().compare("soon", "2026-04-05").status == NOT_COMPARABLE


class TestIsoCountry:
    def test_same_code_matches(self):
        assert IsoCountry().compare("de", "DE").status == MATCH

    def test_spelled_out_alias_normalised(self):
        assert IsoCountry().compare("Germany", "DE").status == MATCH

    def test_different_countries_mismatch(self):
        assert IsoCountry().compare("DE", "FR").status == MISMATCH

    def test_unknown_value_not_comparable(self):
        result = IsoCountry().compare("XX", "DE")
        assert result.status == NOT_COMPARABLE
        assert "XX" in result.reason


class TestFuzzyName:
    def test_identical_matches(self):
        assert FuzzyName(threshold=0.9).compare("MSCU1234567", "MSCU1234567").status == MATCH

    def test_single_ocr_confusion_above_threshold(self):
        # One character off in an 11-character reference: ratio ~0.91.
        result = FuzzyName(threshold=0.9).compare("MSCU1234S67", "MSCU1234567")
        assert result.status == MATCH
        assert "similarity" in result.reason

    def test_clearly_different_below_threshold(self):
        assert FuzzyName(threshold=0.9).compare("MSCU1234567", "HLCU9876543").status == MISMATCH

    def test_threshold_validation(self):
        with pytest.raises(ValueError):
            FuzzyName(threshold=1.5)


class TestRegistry:
    def test_builds_configured_comparator(self):
        comparator = from_config("numeric_with_tolerance", {"pct": 2.0})
        assert comparator.compare(102.0, 100.0).status == MATCH

    def test_unknown_name_raises(self):
        with pytest.raises(KeyError):
            from_config("levenshtein_magic", {})

    def test_describe_includes_params_and_version(self):
        description = from_config("fuzzy_name", {"threshold": 0.8}).describe()
        assert "threshold=0.8" in description
        assert "v1.0" in description
