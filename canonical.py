"""The one canonical shape that every source system is mapped into.

Readers (see readers/) do all the work of getting here.  Everything
downstream -- comparators, alignment, validity rules, evaluation,
reporting -- only ever sees these dataclasses and never needs to know
which system a record came from.  Adding a new source system therefore
means writing one new reader module and nothing else.

Structure (v2, multi-item): a customs declaration is a HEADER plus a list
of GOODS ITEMS, because that is what a declaration actually is.  Importer,
incoterm, currency, invoice total and date live on the header; HS code,
origin, quantity, values and weights live per item.  The earlier flat
single-item shape hid the hardest measurement problem -- deciding which
extracted item corresponds to which filed item -- which alignment.py now
handles explicitly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CanonicalItem:
    """One goods item (declaration line) within a declaration."""

    # Position in the source document, 1-based.  Extraction tools and the
    # filing system number items independently, so this is context for
    # humans and a tie-breaker for alignment -- never a join key.
    item_number: Optional[int]

    hs_code: Optional[str]
    origin_country: Optional[str]     # ISO 3166-1 alpha-2

    quantity: Optional[float]
    # Supplementary unit the quantity is expressed in (KGM, NAR, NPR, LTR).
    # A quantity without its unit is not a fact: 12 pieces and 12 kg look
    # identical as bare numbers.
    quantity_unit: Optional[str]

    item_value: Optional[float]       # this item's value, invoice currency
    gross_weight: Optional[float]     # kg
    net_weight: Optional[float]       # kg
    package_count: Optional[int]

    # Field name -> confidence in [0, 1] for THIS item's fields, as
    # reported by the extraction tool.  Empty for filed records.
    extraction_confidence: dict[str, float] = field(default_factory=dict)


@dataclass
class CanonicalRecord:
    """One customs declaration, from either side of the comparison.

    "Extracted" records come from an extraction tool, carry per-field
    confidence scores, and are what we are measuring.  "Filed" records come
    from the downstream filing system (a black box we can only read from)
    and are what we measure against.  Both use this same class; filed
    records simply have empty extraction_confidence dicts.
    """

    # --- identity / provenance -------------------------------------------
    shipment_id: Optional[str]
    doc_id: str                  # the join key between extracted and filed
    country: Optional[str]       # country of filing, ISO 3166-1 alpha-2
    source_system: str           # which system this record was read from

    # --- header content ---------------------------------------------------
    # Invoice total in `currency`.  NOT the customs value: no incoterm
    # freight/insurance adjustment and no exchange-rate conversion is
    # modelled here -- see "where this harness is knowingly wrong" in the
    # README.  Both sides carry the invoice-denominated total, so they are
    # at least compared like for like.
    declared_value: Optional[float]
    currency: Optional[str]
    incoterm: Optional[str]
    importer_eori: Optional[str]
    bl_reference: Optional[str]

    # Kept exactly as the source wrote it.  Readers must NOT parse or
    # reformat dates: whether "04/05/2026" means April or May is precisely
    # the kind of error this harness has to measure, and a reader that
    # "fixes" it would hide the problem.  Normalising is the job of the
    # normalised_date comparator.
    invoice_date: Optional[str]

    # --- goods items ------------------------------------------------------
    items: list[CanonicalItem] = field(default_factory=list)

    # --- extraction metadata ---------------------------------------------
    # Field name -> confidence in [0, 1] for HEADER fields.  Item-level
    # confidences live on each CanonicalItem.  Empty for filed records.
    extraction_confidence: dict[str, float] = field(default_factory=dict)
    # When the record was produced (extraction time / filing time).
    timestamp: Optional[datetime] = None

    def as_dict(self) -> dict:
        return asdict(self)
