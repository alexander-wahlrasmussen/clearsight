from datetime import date

import validity
from canonical import CanonicalItem, CanonicalRecord
from reference_data import EORI_FORMATS

TODAY = date(2026, 8, 28)  # fixed clock for deterministic date tests
TARIFF = frozenset({"8471300000", "84713000", "6403999600", "64039996"})


def valid_eori(country: str) -> str:
    """A synthetic EORI with the country's real digit count and a correct
    Luhn check digit."""
    payload = "123456789012345678"[: EORI_FORMATS[country] - 1]
    return f"{country}{payload}{validity.luhn_check_digit(payload)}"


def make_item(**overrides) -> CanonicalItem:
    """An item that passes every item rule; tests override one field at a time."""
    values = dict(
        item_number=1,
        hs_code="8471300000",   # chapter 84 -> unit NAR
        origin_country="CN",
        quantity=10.0,
        quantity_unit="NAR",
        item_value=1000.0,
        gross_weight=100.0,
        net_weight=90.0,
        package_count=2,
        extraction_confidence={},
    )
    values.update(overrides)
    return CanonicalItem(**values)


def make_record(**overrides) -> CanonicalRecord:
    """A record that passes every header rule; tests override one field at a time."""
    values = dict(
        shipment_id="SHP-000001",
        doc_id="DOC-000001",
        country="DE",
        source_system="test",
        declared_value=1000.0,   # equals the single default item's value
        currency="EUR",
        incoterm="CIF",
        importer_eori=valid_eori("DE"),
        bl_reference="MSCU1234567",
        invoice_date="2026-05-04",
        items=[make_item()],
        extraction_confidence={},
        timestamp=None,
    )
    values.update(overrides)
    return CanonicalRecord(**values)


class TestHsCodeFormat:
    def test_ten_digits_pass(self):
        assert validity.hs_code_format(make_item()).passed

    def test_eight_digits_pass(self):
        assert validity.hs_code_format(make_item(hs_code="84713000")).passed

    def test_dotted_format_normalised(self):
        assert validity.hs_code_format(make_item(hs_code="8471.30.0000")).passed

    def test_six_digit_truncation_fails(self):
        result = validity.hs_code_format(make_item(hs_code="847130"))
        assert not result.passed
        assert "6 digits" in result.reason

    def test_non_digits_fail(self):
        assert not validity.hs_code_format(make_item(hs_code="84713000AB")).passed

    def test_missing_fails(self):
        assert not validity.hs_code_format(make_item(hs_code=None)).passed


class TestHsCodeInTariff:
    def test_listed_code_passes(self):
        assert validity.hs_code_in_tariff(make_item(), TARIFF).passed

    def test_unlisted_code_fails(self):
        result = validity.hs_code_in_tariff(make_item(hs_code="9999999999"), TARIFF)
        assert not result.passed
        assert "not found" in result.reason


class TestCountryAndCurrency:
    def test_valid_codes_pass(self):
        assert validity.country_is_iso(make_record()).passed
        assert validity.origin_country_is_iso(make_item()).passed
        assert validity.currency_is_iso(make_record()).passed

    def test_invalid_country_fails(self):
        assert not validity.country_is_iso(make_record(country="XX")).passed

    def test_missing_origin_fails(self):
        result = validity.origin_country_is_iso(make_item(origin_country=None))
        assert not result.passed
        assert "missing" in result.reason

    def test_invalid_currency_fails(self):
        assert not validity.currency_is_iso(make_record(currency="EUROS")).passed


class TestEoriChecksum:
    def test_valid_german_eori_passes(self):
        result = validity.eori_checksum(make_record(importer_eori=valid_eori("DE")))
        assert result.passed
        assert "DE" in result.reason

    def test_valid_dutch_eori_passes(self):
        assert validity.eori_checksum(make_record(importer_eori=valid_eori("NL"))).passed

    def test_wrong_national_length_fails(self):
        # A Dutch-length number under a German prefix.
        eori = "DE" + valid_eori("NL")[2:]
        result = validity.eori_checksum(make_record(importer_eori=eori))
        assert not result.passed
        assert "15 digits" in result.reason

    def test_corrupted_check_digit_fails(self):
        good = valid_eori("DE")
        bad_check = str((int(good[-1]) + 1) % 10)
        result = validity.eori_checksum(make_record(importer_eori=good[:-1] + bad_check))
        assert not result.passed
        assert "check digit" in result.reason

    def test_unknown_country_format_fails(self):
        result = validity.eori_checksum(make_record(importer_eori="FR12345678"))
        assert not result.passed
        assert "no known national" in result.reason

    def test_wrong_shape_fails(self):
        assert not validity.eori_checksum(make_record(importer_eori="DE12AB")).passed

    def test_luhn_check_digit_known_value(self):
        # 7992739871 is the classic Luhn example: check digit 3.
        assert validity.luhn_check_digit("7992739871") == 3


class TestQuantityUnitExpected:
    def test_machinery_in_pieces_passes(self):
        assert validity.quantity_unit_expected(make_item()).passed  # chapter 84 -> NAR

    def test_footwear_in_pairs_passes(self):
        item = make_item(hs_code="6403999600", quantity_unit="NPR")
        assert validity.quantity_unit_expected(item).passed

    def test_footwear_in_pieces_fails(self):
        item = make_item(hs_code="6403999600", quantity_unit="NAR")
        result = validity.quantity_unit_expected(item)
        assert not result.passed
        assert "NPR" in result.reason

    def test_unlisted_chapter_defaults_to_weight(self):
        item = make_item(hs_code="2710198100", quantity_unit="KGM")
        assert validity.quantity_unit_expected(item).passed

    def test_unknown_unit_code_fails(self):
        assert not validity.quantity_unit_expected(make_item(quantity_unit="XYZ")).passed

    def test_missing_unit_fails(self):
        assert not validity.quantity_unit_expected(make_item(quantity_unit=None)).passed


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
        assert validity.net_weight_not_exceeding_gross(make_item()).passed

    def test_equal_weights_pass(self):
        item = make_item(net_weight=100.0, gross_weight=100.0)
        assert validity.net_weight_not_exceeding_gross(item).passed

    def test_net_above_gross_fails(self):
        item = make_item(net_weight=101.0, gross_weight=100.0)
        result = validity.net_weight_not_exceeding_gross(item)
        assert not result.passed
        assert "exceeds" in result.reason

    def test_missing_weight_fails(self):
        assert not validity.net_weight_not_exceeding_gross(make_item(net_weight=None)).passed


class TestItemValuesSumToInvoiceTotal:
    def test_exact_sum_passes(self):
        record = make_record(
            declared_value=1000.0,
            items=[make_item(item_value=600.0), make_item(item_number=2, item_value=400.0)],
        )
        assert validity.item_values_sum_to_invoice_total(record).passed

    def test_rounding_within_tolerance_passes(self):
        record = make_record(
            declared_value=100.0,
            items=[make_item(item_value=33.33), make_item(item_number=2, item_value=66.66)],
        )
        assert validity.item_values_sum_to_invoice_total(record).passed

    def test_dropped_item_breaks_the_sum(self):
        # Extraction dropped a 400.00 line; arithmetic catches it with no
        # ground truth in sight.
        record = make_record(declared_value=1000.0, items=[make_item(item_value=600.0)])
        result = validity.item_values_sum_to_invoice_total(record)
        assert not result.passed
        assert "off by" in result.reason

    def test_no_items_fails(self):
        assert not validity.item_values_sum_to_invoice_total(make_record(items=[])).passed

    def test_missing_total_fails(self):
        record = make_record(declared_value=None)
        assert not validity.item_values_sum_to_invoice_total(record).passed

    def test_missing_item_value_fails(self):
        record = make_record(items=[make_item(item_value=None)])
        assert not validity.item_values_sum_to_invoice_total(record).passed


class TestRunRecordRules:
    def test_clean_record_passes_everything(self):
        results = validity.run_record_rules(make_record(), TARIFF)
        assert [r for r in results if not r.passed] == []

    def test_header_rules_once_item_rules_per_item(self):
        record = make_record(
            declared_value=2000.0,
            items=[make_item(item_value=1000.0), make_item(item_number=2, item_value=1000.0)],
        )
        results = validity.run_record_rules(record, TARIFF)
        assert len(results) == 5 + 5 * 2  # 5 header rules + 5 item rules per item

    def test_item_results_carry_their_item_number(self):
        record = make_record(
            declared_value=2000.0,
            items=[make_item(item_value=1000.0), make_item(item_number=2, item_value=1000.0)],
        )
        results = validity.run_record_rules(record, TARIFF)
        item_numbers = {r.item_number for r in results if r.rule == "hs_code_format"}
        assert item_numbers == {1, 2}
        assert all(r.item_number is None for r in results if r.rule == "eori_checksum")
