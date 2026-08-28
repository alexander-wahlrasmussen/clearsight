from datetime import date

import validity
from canonical import CanonicalRecord

TODAY = date(2026, 8, 28)  # fixed clock for deterministic date tests
TARIFF = frozenset({"8471300000", "84713000", "6109100010", "61091000"})


def make_record(**overrides) -> CanonicalRecord:
    """A record that passes every rule; tests override one field at a time."""
    payload = "12345678"
    values = dict(
        shipment_id="SHP-000001",
        doc_id="DOC-000001",
        country="DE",
        source_system="test",
        hs_code="8471300000",
        declared_value=1000.0,
        currency="EUR",
        origin_country="CN",
        quantity=10.0,
        gross_weight=100.0,
        net_weight=90.0,
        package_count=2,
        incoterm="CIF",
        importer_eori=f"DE{payload}{validity.luhn_check_digit(payload)}",
        bl_reference="MSCU1234567",
        invoice_date="2026-05-04",
    )
    values.update(overrides)
    return CanonicalRecord(**values)


class TestHsCodeFormat:
    def test_ten_digits_pass(self):
        assert validity.hs_code_format(make_record()).passed

    def test_eight_digits_pass(self):
        assert validity.hs_code_format(make_record(hs_code="84713000")).passed

    def test_dotted_format_normalised(self):
        assert validity.hs_code_format(make_record(hs_code="8471.30.0000")).passed

    def test_six_digit_truncation_fails(self):
        result = validity.hs_code_format(make_record(hs_code="847130"))
        assert not result.passed
        assert "6 digits" in result.reason

    def test_non_digits_fail(self):
        assert not validity.hs_code_format(make_record(hs_code="84713000AB")).passed

    def test_missing_fails(self):
        assert not validity.hs_code_format(make_record(hs_code=None)).passed


class TestHsCodeInTariff:
    def test_listed_code_passes(self):
        assert validity.hs_code_in_tariff(make_record(), TARIFF).passed

    def test_unlisted_code_fails(self):
        result = validity.hs_code_in_tariff(make_record(hs_code="9999999999"), TARIFF)
        assert not result.passed
        assert "not found" in result.reason


class TestCountryAndCurrency:
    def test_valid_codes_pass(self):
        record = make_record()
        assert validity.country_is_iso(record).passed
        assert validity.origin_country_is_iso(record).passed
        assert validity.currency_is_iso(record).passed

    def test_invalid_country_fails(self):
        assert not validity.country_is_iso(make_record(country="XX")).passed

    def test_missing_origin_fails(self):
        result = validity.origin_country_is_iso(make_record(origin_country=None))
        assert not result.passed
        assert "missing" in result.reason

    def test_invalid_currency_fails(self):
        assert not validity.currency_is_iso(make_record(currency="EUROS")).passed


class TestEoriChecksum:
    def test_valid_checksum_passes(self):
        assert validity.eori_checksum(make_record()).passed

    def test_corrupted_digit_fails(self):
        good = make_record().importer_eori
        # Flip the check digit to a different one.
        bad_check = str((int(good[-1]) + 1) % 10)
        result = validity.eori_checksum(make_record(importer_eori=good[:-1] + bad_check))
        assert not result.passed
        assert "check digit" in result.reason

    def test_wrong_shape_fails(self):
        assert not validity.eori_checksum(make_record(importer_eori="DE1234")).passed

    def test_unknown_country_prefix_fails(self):
        payload = "12345678"
        eori = f"ZZ{payload}{validity.luhn_check_digit(payload)}"
        assert not validity.eori_checksum(make_record(importer_eori=eori)).passed

    def test_luhn_check_digit_known_value(self):
        # 7992739871 is the classic Luhn example: check digit 3.
        assert validity.luhn_check_digit("7992739871") == 3


class TestInvoiceDatePlausible:
    def test_recent_date_passes(self):
        assert validity.invoice_date_plausible(make_record(), today=TODAY).passed

    def test_ancient_date_fails(self):
        record = make_record(invoice_date="1999-01-01")
        assert not validity.invoice_date_plausible(record, today=TODAY).passed

    def test_far_future_fails(self):
        record = make_record(invoice_date="2027-12-31")
        result = validity.invoice_date_plausible(record, today=TODAY)
        assert not result.passed
        assert "future" in result.reason

    def test_unparseable_fails(self):
        record = make_record(invoice_date="sometime in spring")
        assert not validity.invoice_date_plausible(record, today=TODAY).passed


class TestNetVersusGross:
    def test_net_below_gross_passes(self):
        assert validity.net_weight_not_exceeding_gross(make_record()).passed

    def test_equal_weights_pass(self):
        record = make_record(net_weight=100.0, gross_weight=100.0)
        assert validity.net_weight_not_exceeding_gross(record).passed

    def test_net_above_gross_fails(self):
        record = make_record(net_weight=101.0, gross_weight=100.0)
        result = validity.net_weight_not_exceeding_gross(record)
        assert not result.passed
        assert "exceeds" in result.reason

    def test_missing_weight_fails(self):
        record = make_record(net_weight=None)
        assert not validity.net_weight_not_exceeding_gross(record).passed


class TestInvoiceLinesSumToTotal:
    def test_exact_sum_passes(self):
        assert validity.invoice_lines_sum_to_total([60.0, 40.0], 100.0).passed

    def test_rounding_within_tolerance_passes(self):
        assert validity.invoice_lines_sum_to_total([33.33, 33.33, 33.33], 100.0).passed

    def test_wrong_sum_fails(self):
        result = validity.invoice_lines_sum_to_total([60.0, 49.0], 100.0)
        assert not result.passed
        assert "off by" in result.reason

    def test_no_lines_fails(self):
        assert not validity.invoice_lines_sum_to_total([], 100.0).passed

    def test_missing_total_fails(self):
        assert not validity.invoice_lines_sum_to_total([50.0], None).passed


class TestRunRecordRules:
    def test_clean_record_passes_everything(self):
        results = validity.run_record_rules(make_record(), TARIFF)
        failed = [r for r in results if not r.passed]
        # invoice_date_plausible uses the real clock here; the fixed date in
        # make_record is well inside the window either way.
        assert failed == []

    def test_every_rule_reports_once(self):
        results = validity.run_record_rules(make_record(), TARIFF)
        assert len({r.rule for r in results}) == len(results) == 8
