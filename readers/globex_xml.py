"""Reader for GlobexCapture: a nested XML export.

Globex keeps the declaration's natural nesting -- one Document, a header
spread over a few elements, and one Item element per goods item:

    <Extractions system="GlobexCapture">
      <Document id="DOC-000123" extractedAt="2026-08-01T12:00:00">
        <Shipment ref="SHP-000123" declarationCountry="DE"/>
        <Goods>
          <Item number="1" tariffCode="8471300000" originCountry="CN">
            <Quantity unit="NAR">12</Quantity>
            <Value>5000.00</Value>
            <Weights gross="140.2" net="128.9"/>
            <Packages>3</Packages>
            <Confidence>
              <Field name="hs_code" score="0.97"/>
              ...
            </Confidence>
          </Item>
          ...
        </Goods>
        <Commercial incoterm="CIF">
          <Invoice date="03/04/2026" currency="EUR" total="10450.00"/>
          <Transport blReference="MSCU1234567"/>
          <Importer eori="DE123456789012345"/>
        </Commercial>
        <Confidence>              <!-- header fields -->
          <Field name="declared_value" score="0.93"/>
          ...
        </Confidence>
      </Document>
    </Extractions>

This module maps that into the same CanonicalRecord + CanonicalItem shape
the CSV reader produces -- which is the whole point of the readers layer.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from canonical import CanonicalItem, CanonicalRecord

from ._common import datetime_or_none, float_or_none, int_or_none, text_or_none

SOURCE_SYSTEM = "globex_capture"


def read(path: str | Path) -> list[CanonicalRecord]:
    root = ET.parse(path).getroot()
    return [_to_canonical(doc) for doc in root.findall("Document")]


def _confidences(parent: ET.Element | None) -> dict[str, float]:
    if parent is None:
        return {}
    return {
        f.get("name"): float(f.get("score"))
        for f in parent.findall("Confidence/Field")
        if f.get("name") and f.get("score")
    }


def _to_item(item: ET.Element) -> CanonicalItem:
    quantity = item.find("Quantity")
    weights = item.find("Weights")
    return CanonicalItem(
        item_number=int_or_none(item.get("number")),
        hs_code=_attr(item, "tariffCode"),
        origin_country=_attr(item, "originCountry"),
        quantity=float_or_none(quantity.text if quantity is not None else None),
        quantity_unit=_attr(quantity, "unit"),
        item_value=float_or_none(_text(item, "Value")),
        gross_weight=float_or_none(_attr(weights, "gross")),
        net_weight=float_or_none(_attr(weights, "net")),
        package_count=int_or_none(_text(item, "Packages")),
        extraction_confidence=_confidences(item),
    )


def _to_canonical(doc: ET.Element) -> CanonicalRecord:
    shipment = doc.find("Shipment")
    commercial = doc.find("Commercial")
    invoice = doc.find("Commercial/Invoice")
    return CanonicalRecord(
        shipment_id=_attr(shipment, "ref"),
        doc_id=doc.get("id", "").strip(),
        country=_attr(shipment, "declarationCountry"),
        source_system=SOURCE_SYSTEM,
        declared_value=float_or_none(_attr(invoice, "total")),
        currency=_attr(invoice, "currency"),
        incoterm=_attr(commercial, "incoterm"),
        importer_eori=_attr(doc.find("Commercial/Importer"), "eori"),
        bl_reference=_attr(doc.find("Commercial/Transport"), "blReference"),
        invoice_date=_attr(invoice, "date"),
        items=[_to_item(item) for item in doc.findall("Goods/Item")],
        # Direct child only: item Confidence elements must not leak into
        # the header confidences.
        extraction_confidence={
            f.get("name"): float(f.get("score"))
            for f in doc.findall("./Confidence/Field")
            if f.get("name") and f.get("score")
        },
        timestamp=datetime_or_none(doc.get("extractedAt")),
    )


def _attr(element: ET.Element | None, name: str) -> str | None:
    return text_or_none(element.get(name)) if element is not None else None


def _text(parent: ET.Element, xpath: str) -> str | None:
    element = parent.find(xpath)
    return text_or_none(element.text) if element is not None else None
