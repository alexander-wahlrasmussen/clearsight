"""Reader for AcmeExtract: a flat CSV export, one row per GOODS ITEM.

Acme flattens multi-item declarations the way flat systems always do:
header columns repeated on every row, item columns varying per row.  This
reader groups rows by document number and rebuilds the header + items
shape; the header is taken from the first row of each document.

Confidence arrives as extra columns: hconf_<field> for header fields,
conf_<field> for the item fields on that row.

Column mapping (Acme -> canonical):
    header (repeated per row)          item (per row)
    shp_ref      -> shipment_id        line_no     -> item_number
    document_no  -> doc_id             tariff_code -> hs_code
    decl_country -> country            coo         -> origin_country
    inv_total    -> declared_value     qty         -> quantity
    ccy          -> currency           qty_unit    -> quantity_unit
    terms        -> incoterm           item_val    -> item_value
    eori_no      -> importer_eori      grs_kg      -> gross_weight
    bl_no        -> bl_reference       net_kg      -> net_weight
    inv_date     -> invoice_date       pkgs        -> package_count
    extracted_at -> timestamp
"""

from __future__ import annotations

import csv
from pathlib import Path

from canonical import CanonicalItem, CanonicalRecord

from ._common import datetime_or_none, float_or_none, int_or_none, text_or_none

SOURCE_SYSTEM = "acme_extract"

_HEADER_CONFIDENCE_PREFIX = "hconf_"
_ITEM_CONFIDENCE_PREFIX = "conf_"


def read(path: str | Path) -> list[CanonicalRecord]:
    # Group item rows per document, preserving file order.
    rows_by_doc: dict[str, list[dict]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows_by_doc.setdefault(row["document_no"].strip(), []).append(row)
    return [_to_canonical(doc_id, rows) for doc_id, rows in rows_by_doc.items()]


def _confidences(row: dict, prefix: str) -> dict[str, float]:
    return {
        column[len(prefix):]: float(value)
        for column, value in row.items()
        if column.startswith(prefix) and text_or_none(value) is not None
    }


def _to_item(row: dict) -> CanonicalItem:
    return CanonicalItem(
        item_number=int_or_none(row["line_no"]),
        hs_code=text_or_none(row["tariff_code"]),
        origin_country=text_or_none(row["coo"]),
        quantity=float_or_none(row["qty"]),
        quantity_unit=text_or_none(row["qty_unit"]),
        item_value=float_or_none(row["item_val"]),
        gross_weight=float_or_none(row["grs_kg"]),
        net_weight=float_or_none(row["net_kg"]),
        package_count=int_or_none(row["pkgs"]),
        extraction_confidence=_confidences(row, _ITEM_CONFIDENCE_PREFIX),
    )


def _to_canonical(doc_id: str, rows: list[dict]) -> CanonicalRecord:
    header = rows[0]
    return CanonicalRecord(
        shipment_id=text_or_none(header["shp_ref"]),
        doc_id=doc_id,
        country=text_or_none(header["decl_country"]),
        source_system=SOURCE_SYSTEM,
        declared_value=float_or_none(header["inv_total"]),
        currency=text_or_none(header["ccy"]),
        incoterm=text_or_none(header["terms"]),
        importer_eori=text_or_none(header["eori_no"]),
        bl_reference=text_or_none(header["bl_no"]),
        invoice_date=text_or_none(header["inv_date"]),
        items=[_to_item(row) for row in rows],
        extraction_confidence=_confidences(header, _HEADER_CONFIDENCE_PREFIX),
        timestamp=datetime_or_none(header["extracted_at"]),
    )
