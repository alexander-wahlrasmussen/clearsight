"""Reader for GlobexCapture: a nested XML export.

Globex spreads one document's fields across several nested elements:

    <Extractions system="GlobexCapture">
      <Document id="DOC-000123" extractedAt="2026-08-01T12:00:00">
        <Shipment ref="SHP-000123" declarationCountry="DE"/>
        <Goods>
          <Item tariffCode="8471300000" originCountry="CN">
            <Quantity>12</Quantity>
            <Weights gross="140.2" net="128.9"/>
            <Packages>3</Packages>
          </Item>
        </Goods>
        <Commercial incoterm="CIF">
          <Invoice date="03/04/2026" currency="EUR" total="10450.00">
            <Line amount="5000.00"/>
            <Line amount="5450.00"/>
          </Invoice>
          <Transport blReference="MSCU1234567"/>
          <Importer eori="DE123456782"/>
        </Commercial>
        <Confidence>
          <Field name="hs_code" score="0.97"/>
          ...
        </Confidence>
      </Document>
    </Extractions>

This module flattens that into the same CanonicalRecord the CSV reader
produces -- which is the whole point of the readers layer.

Invoice line items have no home on the canonical record (only the total
does), so read_invoice_lines() exposes them separately for the
invoice_lines_sum_to_total validity rule.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from canonical import CanonicalRecord

from ._common import datetime_or_none, float_or_none, int_or_none, text_or_none

SOURCE_SYSTEM = "globex_capture"


def read(path: str | Path) -> list[CanonicalRecord]:
    root = ET.parse(path).getroot()
    return [_to_canonical(doc) for doc in root.findall("Document")]


def read_invoice_lines(path: str | Path) -> dict[str, tuple[list[float], float | None]]:
    """doc_id -> (line amounts, invoice total), for the line-sum validity rule."""
    root = ET.parse(path).getroot()
    result: dict[str, tuple[list[float], float | None]] = {}
    for doc in root.findall("Document"):
        invoice = doc.find("Commercial/Invoice")
        if invoice is None:
            continue
        amounts = [
            amount
            for line in invoice.findall("Line")
            if (amount := float_or_none(line.get("amount"))) is not None
        ]
        result[doc.get("id", "")] = (amounts, float_or_none(invoice.get("total")))
    return result


def _to_canonical(doc: ET.Element) -> CanonicalRecord:
    shipment = doc.find("Shipment")
    item = doc.find("Goods/Item")
    commercial = doc.find("Commercial")
    invoice = doc.find("Commercial/Invoice")
    confidence = {
        f.get("name"): float(f.get("score"))
        for f in doc.findall("Confidence/Field")
        if f.get("name") and f.get("score")
    }
    return CanonicalRecord(
        shipment_id=_attr(shipment, "ref"),
        doc_id=doc.get("id", "").strip(),
        country=_attr(shipment, "declarationCountry"),
        source_system=SOURCE_SYSTEM,
        hs_code=_attr(item, "tariffCode"),
        declared_value=float_or_none(_attr(invoice, "total")),
        currency=_attr(invoice, "currency"),
        origin_country=_attr(item, "originCountry"),
        quantity=float_or_none(_text(doc, "Goods/Item/Quantity")),
        gross_weight=float_or_none(_attr(doc.find("Goods/Item/Weights"), "gross")),
        net_weight=float_or_none(_attr(doc.find("Goods/Item/Weights"), "net")),
        package_count=int_or_none(_text(doc, "Goods/Item/Packages")),
        incoterm=_attr(commercial, "incoterm"),
        importer_eori=_attr(doc.find("Commercial/Importer"), "eori"),
        bl_reference=_attr(doc.find("Commercial/Transport"), "blReference"),
        invoice_date=_attr(invoice, "date"),
        extraction_confidence=confidence,
        timestamp=datetime_or_none(doc.get("extractedAt")),
    )


def _attr(element: ET.Element | None, name: str) -> str | None:
    return text_or_none(element.get(name)) if element is not None else None


def _text(doc: ET.Element, xpath: str) -> str | None:
    element = doc.find(xpath)
    return text_or_none(element.text) if element is not None else None
