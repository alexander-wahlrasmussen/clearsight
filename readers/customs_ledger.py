"""Reader for CustomsLedger: the downstream filing system's export.

CustomsLedger is the black box the declarations were actually filed in.
We can only read from it; its nightly export is a flat CSV with one row
per goods item, header columns repeated, already using names close to our
canonical ones.  Filed records carry no extraction confidence (nothing
was extracted -- this is what ended up filed) and their timestamp is the
filing time.
"""

from __future__ import annotations

import csv
from pathlib import Path

from canonical import CanonicalItem, CanonicalRecord

from ._common import datetime_or_none, float_or_none, int_or_none, text_or_none

SOURCE_SYSTEM = "customs_ledger"


def read(path: str | Path) -> list[CanonicalRecord]:
    rows_by_doc: dict[str, list[dict]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows_by_doc.setdefault(row["doc_id"].strip(), []).append(row)
    return [_to_canonical(doc_id, rows) for doc_id, rows in rows_by_doc.items()]


def _to_item(row: dict) -> CanonicalItem:
    return CanonicalItem(
        item_number=int_or_none(row["item_number"]),
        hs_code=text_or_none(row["hs_code"]),
        origin_country=text_or_none(row["origin_country"]),
        quantity=float_or_none(row["quantity"]),
        quantity_unit=text_or_none(row["quantity_unit"]),
        item_value=float_or_none(row["item_value"]),
        gross_weight=float_or_none(row["gross_weight"]),
        net_weight=float_or_none(row["net_weight"]),
        package_count=int_or_none(row["package_count"]),
        extraction_confidence={},
    )


def _to_canonical(doc_id: str, rows: list[dict]) -> CanonicalRecord:
    header = rows[0]
    return CanonicalRecord(
        shipment_id=text_or_none(header["shipment_id"]),
        doc_id=doc_id,
        country=text_or_none(header["country"]),
        source_system=SOURCE_SYSTEM,
        declared_value=float_or_none(header["declared_value"]),
        currency=text_or_none(header["currency"]),
        incoterm=text_or_none(header["incoterm"]),
        importer_eori=text_or_none(header["importer_eori"]),
        bl_reference=text_or_none(header["bl_reference"]),
        invoice_date=text_or_none(header["invoice_date"]),
        items=[_to_item(row) for row in rows],
        extraction_confidence={},
        timestamp=datetime_or_none(header["filed_at"]),
    )
