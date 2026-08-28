from alignment import align_items, describe_item, hs_prefix_agreement
from canonical import CanonicalItem

MIN_SCORE = 0.35


def item(number, hs_code, item_value, origin="CN", quantity=10.0, unit="NAR"):
    return CanonicalItem(
        item_number=number,
        hs_code=hs_code,
        origin_country=origin,
        quantity=quantity,
        quantity_unit=unit,
        item_value=item_value,
        gross_weight=100.0,
        net_weight=90.0,
        package_count=1,
        extraction_confidence={},
    )


class TestHsPrefixAgreement:
    def test_identical_codes_agree_fully(self):
        assert hs_prefix_agreement("8471300000", "8471300000") == 10

    def test_same_heading_different_subheading(self):
        assert hs_prefix_agreement("8471300000", "8471410000") == 4

    def test_truncated_code_agrees_to_its_length(self):
        assert hs_prefix_agreement("847130", "8471300000") == 6

    def test_odd_shared_prefix_floors_to_nomenclature_boundary(self):
        # 5 shared digits are worth no more than the 4-digit heading.
        assert hs_prefix_agreement("8471300000", "8471310000") == 4

    def test_different_chapters_agree_to_zero(self):
        assert hs_prefix_agreement("8471300000", "6403999600") == 0

    def test_missing_code_agrees_to_zero(self):
        assert hs_prefix_agreement(None, "8471300000") == 0

    def test_punctuation_is_ignored(self):
        assert hs_prefix_agreement("8471.30.0000", "8471300000") == 10


class TestAlignItems:
    def test_identical_single_items_pair(self):
        alignment = align_items(
            [item(1, "8471300000", 5000.0)], [item(1, "8471300000", 5000.0)], MIN_SCORE
        )
        assert len(alignment.pairs) == 1
        assert alignment.pairs[0].score == 1.0
        assert alignment.unmatched_extracted == [] and alignment.unmatched_filed == []

    def test_pairs_by_content_not_by_item_number(self):
        # The two sides list the same goods in opposite order.
        extracted = [item(1, "8471300000", 100.0), item(2, "6403999600", 200.0, unit="NPR")]
        filed = [item(1, "6403999600", 200.0, unit="NPR"), item(2, "8471300000", 100.0)]
        alignment = align_items(extracted, filed, MIN_SCORE)
        paired = {(p.extracted.item_number, p.filed.item_number) for p in alignment.pairs}
        assert paired == {(1, 2), (2, 1)}

    def test_dropped_item_reported_as_unmatched_filed(self):
        extracted = [item(1, "8471300000", 100.0)]
        filed = [item(1, "8471300000", 100.0), item(2, "6403999600", 900.0, unit="NPR")]
        alignment = align_items(extracted, filed, MIN_SCORE)
        assert len(alignment.pairs) == 1
        assert [i.item_number for i in alignment.unmatched_filed] == [2]

    def test_merged_line_pairs_once_and_strands_the_other(self):
        # Extraction summed two same-heading lines into one: the merged line
        # pairs with the item sharing its full HS code; the second filed item
        # has nothing left to pair with.
        merged = item(1, "8471300000", 250.0, quantity=20.0)
        filed = [
            item(1, "8471300000", 100.0, quantity=10.0),
            item(2, "8471410000", 150.0, quantity=10.0),
        ]
        alignment = align_items([merged], filed, MIN_SCORE)
        assert len(alignment.pairs) == 1
        assert alignment.pairs[0].filed.item_number == 1
        assert [i.item_number for i in alignment.unmatched_filed] == [2]

    def test_nonsense_pairing_refused(self):
        # Nothing in common: better two unmatched items than a fake pair.
        alignment = align_items(
            [item(1, "6109100010", 50_000.0, origin="CN", quantity=400.0)],
            [item(1, "0901210000", 100.0, origin="BR", quantity=5.0, unit="KGM")],
            MIN_SCORE,
        )
        assert alignment.pairs == []
        assert len(alignment.unmatched_extracted) == 1
        assert len(alignment.unmatched_filed) == 1

    def test_empty_sides(self):
        alignment = align_items([], [item(1, "8471300000", 100.0)], MIN_SCORE)
        assert alignment.pairs == []
        assert len(alignment.unmatched_filed) == 1
        assert align_items([], [], MIN_SCORE).pairs == []

    def test_basis_explains_the_pairing(self):
        alignment = align_items(
            [item(1, "8471300000", 102.0)], [item(1, "8471300000", 100.0)], MIN_SCORE
        )
        basis = alignment.pairs[0].basis
        assert "HS agrees to 10 digits" in basis
        assert "values differ by 2.0%" in basis
        assert "origin agrees" in basis


class TestDescribeItem:
    def test_mentions_the_facts_a_human_needs(self):
        text = describe_item(item(3, "8471300000", 1234.5, quantity=12.0))
        assert "item 3" in text
        assert "8471300000" in text
        assert "12 NAR" in text
        assert "1,234.50" in text

    def test_survives_missing_values(self):
        empty = CanonicalItem(
            item_number=None, hs_code=None, origin_country=None, quantity=None,
            quantity_unit=None, item_value=None, gross_weight=None,
            net_weight=None, package_count=None, extraction_confidence={},
        )
        assert "?" in describe_item(empty)
