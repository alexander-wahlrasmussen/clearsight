"""Reader for AcmeExtract: a flat CSV export, one row per document.

Acme's export uses its own terse column names; the whole job of this module
is the mapping table below.  Per-field extraction confidence arrives as
extra columns named conf_<canonical_field>.

Column mapping (Acme -> canonical):
    shp_ref      -> shipment_id
    document_no  -> doc_id
    decl_country -> country
    tariff_code  -> hs_code
    inv_value    -> declared_value
    ccy          -> currency
    coo          -> origin_country
    qty          -> quantity
    grs_kg       -> gross_weight
    net_kg       -> net_weight
    pkgs         -> package_count
    terms        -> incoterm
    eori_no      -> importer_eori
    bl_no        -> bl_reference
    inv_date     -> invoice_date   (kept verbatim; see canonical.py)
    extracted_at -> timestamp
"""

from __future__ import annotations

import csv
from pathlib import Path

from canonical import CanonicalRecord

from ._common import datetime_or_none, float_or_none, int_or_none, text_or_none

SOURCE_SYSTEM = "acme_extract"

_CONFIDENCE_PREFIX = "conf_"


def read(path: str | Path) -> list[CanonicalRecord]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [_to_canonical(row) for row in csv.DictReader(fh)]


def _to_canonical(row: dict) -> CanonicalRecord:
    confidence = {
        column[len(_CONFIDENCE_PREFIX):]: float(value)
        for column, value in row.items()
        if column.startswith(_CONFIDENCE_PREFIX) and text_or_none(value) is not None
    }
    return CanonicalRecord(
        shipment_id=text_or_none(row["shp_ref"]),
        doc_id=row["document_no"].strip(),
        country=text_or_none(row["decl_country"]),
        source_system=SOURCE_SYSTEM,
        hs_code=text_or_none(row["tariff_code"]),
        declared_value=float_or_none(row["inv_value"]),
        currency=text_or_none(row["ccy"]),
        origin_country=text_or_none(row["coo"]),
        quantity=float_or_none(row["qty"]),
        gross_weight=float_or_none(row["grs_kg"]),
        net_weight=float_or_none(row["net_kg"]),
        package_count=int_or_none(row["pkgs"]),
        incoterm=text_or_none(row["terms"]),
        importer_eori=text_or_none(row["eori_no"]),
        bl_reference=text_or_none(row["bl_no"]),
        invoice_date=text_or_none(row["inv_date"]),
        extraction_confidence=confidence,
        timestamp=datetime_or_none(row["extracted_at"]),
    )
