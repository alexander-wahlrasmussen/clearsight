"""The one canonical shape that every source system is mapped into.

Readers (see readers/) do all the work of getting here.  Everything
downstream -- comparators, validity rules, evaluation, reporting -- only
ever sees this dataclass and never needs to know which system a record
came from.  Adding a new source system therefore means writing one new
reader module and nothing else.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CanonicalRecord:
    """One customs declaration record, from either side of the comparison.

    "Extracted" records come from an extraction tool, carry per-field
    confidence scores, and are what we are measuring.  "Filed" records come
    from the downstream filing system (a black box we can only read from)
    and are what we measure against.  Both are represented by this same
    class; filed records simply have an empty extraction_confidence dict.
    """

    # --- identity / provenance -------------------------------------------
    shipment_id: Optional[str]
    doc_id: str                  # the join key between extracted and filed
    country: Optional[str]       # country of filing, ISO 3166-1 alpha-2
    source_system: str           # which system this record was read from

    # --- declaration content ---------------------------------------------
    hs_code: Optional[str]
    declared_value: Optional[float]
    currency: Optional[str]
    origin_country: Optional[str]
    quantity: Optional[float]
    gross_weight: Optional[float]
    net_weight: Optional[float]
    package_count: Optional[int]
    incoterm: Optional[str]
    importer_eori: Optional[str]
    bl_reference: Optional[str]

    # Kept exactly as the source wrote it.  Readers must NOT parse or
    # reformat dates: whether "04/05/2026" means April or May is precisely
    # the kind of error this harness has to measure, and a reader that
    # "fixes" it would hide the problem.  Normalising is the job of the
    # normalised_date comparator.
    invoice_date: Optional[str]

    # --- extraction metadata ---------------------------------------------
    # Field name -> confidence in [0, 1], as reported by the extraction
    # tool.  Empty for filed records.
    extraction_confidence: dict[str, float] = field(default_factory=dict)
    # When the record was produced (extraction time / filing time).
    timestamp: Optional[datetime] = None

    def as_dict(self) -> dict:
        return asdict(self)
