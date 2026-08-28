"""Reader for CustomsLedger: the downstream filing system's export.

CustomsLedger is the black box the declarations were actually filed in.
We can only read from it; its nightly export is a flat CSV that already
uses names close to our canonical ones.  Filed records carry no extraction
confidence (nothing was extracted -- this is what ended up filed) and
their timestamp is the filing time.
"""

from __future__ import annotations

import csv
from pathlib import Path

from canonical import CanonicalRecord

from ._common import datetime_or_none, float_or_none, int_or_none, text_or_none

SOURCE_SYSTEM = "customs_ledger"


def read(path: str | Path) -> list[CanonicalRecord]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [_to_canonical(row) for row in csv.DictReader(fh)]


def _to_canonical(row: dict) -> CanonicalRecord:
    return CanonicalRecord(
        shipment_id=text_or_none(row["shipment_id"]),
        doc_id=row["doc_id"].strip(),
        country=text_or_none(row["country"]),
        source_system=SOURCE_SYSTEM,
        hs_code=text_or_none(row["hs_code"]),
        declared_value=float_or_none(row["declared_value"]),
        currency=text_or_none(row["currency"]),
        origin_country=text_or_none(row["origin_country"]),
        quantity=float_or_none(row["quantity"]),
        gross_weight=float_or_none(row["gross_weight"]),
        net_weight=float_or_none(row["net_weight"]),
        package_count=int_or_none(row["package_count"]),
        incoterm=text_or_none(row["incoterm"]),
        importer_eori=text_or_none(row["importer_eori"]),
        bl_reference=text_or_none(row["bl_reference"]),
        invoice_date=text_or_none(row["invoice_date"]),
        extraction_confidence={},
        timestamp=datetime_or_none(row["filed_at"]),
    )
