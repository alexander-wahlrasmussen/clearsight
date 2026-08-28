"""Reader registry: one module per source system.

Each reader module exposes
    SOURCE_SYSTEM: str
    read(path) -> list[CanonicalRecord]

and does all the format-specific work of mapping its system's export into
the canonical shape.  Adding a source system = writing one new module and
registering it here; comparators, validity, evaluation and reporting do
not change.
"""

from . import acme_csv, customs_ledger, globex_xml

READERS = {
    acme_csv.SOURCE_SYSTEM: acme_csv.read,
    globex_xml.SOURCE_SYSTEM: globex_xml.read,
    customs_ledger.SOURCE_SYSTEM: customs_ledger.read,
}
