"""Generate a few thousand paired (extracted, filed) records with realistic,
deliberately injected error patterns.

The generator knows the ground truth; the harness never sees it.  The one
piece of truth written to disk is the amendments sidecar
(data/amendments_truth.csv): documents where the FILED value was
legitimately changed after filing (a revaluation, a corrected count, a
reclassification).  Those show up to the harness as mismatches that are
NOT extraction errors -- exactly the ambiguity a real deployment faces.
evaluate.py never reads that file; it exists so a human can check how much
of the measured error rate the ambiguity accounts for.

Injected extraction error patterns:
  - OCR digit confusion in declared value, weights, EORI and BL reference
  - HS codes truncated to 6 digits (valid internationally, invalid on an
    EU declaration) or with one misread digit
  - day/month swapped in the invoice date (the DD/MM vs MM/DD classic)
  - missing origin country
  - incoterm and currency confusion
  - net weight exceeding gross (caught by validity, no ground truth needed)
  - invoice line amounts not summing to the total (Globex XML only)
  - one country (TR) whose extraction is systematically ~3x worse, because
    someone always has worse scans

Confidence scores are generated to be *imperfectly* honest: corrupted
fields tend to get lower confidence, but the distributions overlap, so the
calibration table has something real to show.
"""

from __future__ import annotations

import argparse
import csv
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from validity import luhn_check_digit

ACME = "acme_extract"
GLOBEX = "globex_capture"

# --- population shape ------------------------------------------------------

COUNTRY_WEIGHTS = {"DE": 0.24, "FR": 0.20, "NL": 0.15, "ES": 0.14, "PL": 0.14, "TR": 0.13}
BAD_COUNTRY = "TR"          # scans from TR are systematically worse
BAD_COUNTRY_MULTIPLIER = 3.0

ORIGIN_COUNTRIES = ["CN", "US", "IN", "VN", "TR", "GB", "JP", "KR", "TW", "TH", "MY", "MX"]
CURRENCY_WEIGHTS = {"EUR": 0.80, "USD": 0.15, "GBP": 0.05}
INCOTERMS = ["EXW", "FOB", "CIF", "CFR", "DAP", "DDP", "FCA"]
CARRIER_PREFIXES = ["MSCU", "MAEU", "HLCU", "CMDU", "ONEY", "COSU"]

# Plausible 10-digit TARIC-style codes; the tariff list is derived from these.
HS_POOL = [
    "8471300000", "8471410000", "8471490000", "8517120019", "8517620090",
    "8528721000", "8544429090", "8708299000", "8708709950", "9403208090",
    "6109100010", "6203423500", "6403999600", "4202129150", "3926909790",
    "3304990090", "2106909890", "1905903000", "0901210000", "2204214290",
    "7318158090", "7326909890", "8302416000", "8481808190", "9018909900",
    "9503004900", "9603909990", "4819100000", "4911990000", "8523512000",
]
TARIFF_DECOYS = [
    "0102291000", "2710198100", "3004900000", "5407610000", "6110113000",
    "7208395000", "8409910000", "8703231900", "9401710000", "9506910010",
]

# --- error rates (base; multiplied for BAD_COUNTRY) ------------------------

RATES = {
    "value_ocr": 0.05,        # digit confusion in declared_value
    "weight_ocr": 0.03,       # digit confusion in gross or net weight
    "hs_truncate": 0.04,      # HS code truncated to 6 digits
    "hs_digit": 0.02,         # one HS digit misread
    "date_swap": 0.08,        # day/month swapped (only possible when day <= 12)
    "origin_missing": 0.06,   # origin country not extracted
    "currency_wrong": 0.015,
    "eori_digit": 0.02,
    "bl_ocr": 0.05,           # O/0, I/1, B/8, S/5 confusion in BL reference
    "incoterm_confusion": 0.02,
    "quantity_slip": 0.02,
    "package_off_by_one": 0.02,
    "net_exceeds_gross": 0.015,
    "line_sum_error": 0.03,   # Globex XML only: a line amount misread
}
AMENDMENT_RATE = 0.04         # per document; NOT an extraction error

OCR_DIGIT_CONFUSION = {"0": "8", "1": "7", "2": "7", "3": "8", "4": "9",
                       "5": "6", "6": "5", "7": "1", "8": "3", "9": "4"}
BL_CHAR_CONFUSION = {"0": "O", "O": "0", "1": "I", "I": "1",
                     "8": "B", "B": "8", "5": "S", "S": "5"}
INCOTERM_CONFUSION = {"CIF": "CFR", "CFR": "CIF", "FOB": "FCA", "FCA": "FOB",
                      "DAP": "DDP", "DDP": "DAP", "EXW": "FOB"}
CURRENCY_CONFUSION = {"EUR": "USD", "USD": "EUR", "GBP": "EUR"}

# Fields that carry an extraction confidence score.
CONFIDENCE_FIELDS = [
    "hs_code", "declared_value", "currency", "origin_country", "importer_eori",
    "quantity", "gross_weight", "net_weight", "invoice_date", "incoterm",
    "package_count", "bl_reference",
]


@dataclass
class TrueDoc:
    """What was actually on the paper -- the generator's private truth."""

    doc_id: str
    shipment_id: str
    country: str
    source_system: str
    hs_code: str
    declared_value: float
    currency: str
    origin_country: str
    quantity: int
    gross_weight: float
    net_weight: float
    package_count: int
    incoterm: str
    importer_eori: str
    bl_reference: str
    invoice_date: date
    line_amounts: list[float]
    extracted_at: datetime
    filed_at: datetime


# ---------------------------------------------------------------------------
# building true documents
# ---------------------------------------------------------------------------


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def _make_eori(rng: random.Random, country: str) -> str:
    payload = "".join(str(rng.randint(0, 9)) for _ in range(8))
    return f"{country}{payload}{luhn_check_digit(payload)}"


def _split_into_lines(rng: random.Random, total: float) -> list[float]:
    """Split an invoice total into 1-4 line amounts that sum exactly to it."""
    n_lines = rng.randint(1, 4)
    if n_lines == 1:
        return [total]
    cuts = sorted(rng.uniform(0.1, 0.9) for _ in range(n_lines - 1))
    fractions = [b - a for a, b in zip([0.0] + cuts, cuts + [1.0])]
    amounts = [round(total * f, 2) for f in fractions[:-1]]
    amounts.append(round(total - sum(amounts), 2))
    return amounts


def _make_true_doc(i: int, rng: random.Random) -> TrueDoc:
    invoice_date = date.today() - timedelta(days=rng.randint(20, 540))
    extracted_at = datetime.combine(
        invoice_date + timedelta(days=rng.randint(2, 12)),
        time(rng.randint(6, 20), rng.randint(0, 59)),
    )
    total = round(rng.uniform(500, 250_000), 2)
    gross = round(rng.uniform(5, 20_000), 1)
    return TrueDoc(
        doc_id=f"DOC-{i:06d}",
        shipment_id=f"SHP-{i:06d}",
        country=_weighted_choice(rng, COUNTRY_WEIGHTS),
        source_system=ACME if rng.random() < 0.55 else GLOBEX,
        hs_code=rng.choice(HS_POOL),
        declared_value=total,
        currency=_weighted_choice(rng, CURRENCY_WEIGHTS),
        origin_country=rng.choice(ORIGIN_COUNTRIES),
        quantity=rng.randint(1, 500),
        gross_weight=gross,
        net_weight=round(gross * rng.uniform(0.70, 0.98), 1),
        package_count=rng.randint(1, 60),
        incoterm=rng.choice(INCOTERMS),
        importer_eori=_make_eori(rng, _weighted_choice(rng, COUNTRY_WEIGHTS)),
        bl_reference=f"{rng.choice(CARRIER_PREFIXES)}{rng.randint(1_000_000, 9_999_999)}",
        invoice_date=invoice_date,
        line_amounts=_split_into_lines(rng, total),
        extracted_at=extracted_at,
        filed_at=extracted_at + timedelta(hours=rng.randint(1, 48)),
    )


# ---------------------------------------------------------------------------
# corrupting the extraction side
# ---------------------------------------------------------------------------


def _confuse_digit(text: str, rng: random.Random) -> str:
    positions = [i for i, ch in enumerate(text) if ch in OCR_DIGIT_CONFUSION]
    if not positions:
        return text
    i = rng.choice(positions)
    return text[:i] + OCR_DIGIT_CONFUSION[text[i]] + text[i + 1:]


def _confuse_bl_chars(text: str, rng: random.Random) -> str:
    positions = [i for i, ch in enumerate(text) if ch in BL_CHAR_CONFUSION]
    if not positions:
        return text
    for i in rng.sample(positions, k=min(len(positions), rng.randint(1, 2))):
        text = text[:i] + BL_CHAR_CONFUSION[text[i]] + text[i + 1:]
    return text


def _format_extracted_date(d: date, source_system: str, rng: random.Random) -> str:
    """Sources format dates differently; none of this is an error, and the
    normalised_date comparator must see through it.  All formats here are
    day-first or unambiguous -- the injected *error* is a swapped date
    value, not a format choice."""
    if source_system == ACME:
        fmt = "%d/%m/%Y" if rng.random() < 0.8 else "%d.%m.%Y"
    else:
        fmt = "%Y-%m-%d" if rng.random() < 0.7 else "%d/%m/%Y"
    return d.strftime(fmt)


def _corrupt_for_extraction(
    doc: TrueDoc, rng: random.Random
) -> tuple[dict, dict[str, float], list[float]]:
    """Return (extracted values, per-field confidence, extracted line amounts)."""
    multiplier = BAD_COUNTRY_MULTIPLIER if doc.country == BAD_COUNTRY else 1.0

    def hit(rate_name: str) -> bool:
        return rng.random() < min(RATES[rate_name] * multiplier, 0.95)

    ext = {
        "hs_code": doc.hs_code,
        "declared_value": doc.declared_value,
        "currency": doc.currency,
        "origin_country": doc.origin_country,
        "importer_eori": doc.importer_eori,
        "quantity": doc.quantity,
        "gross_weight": doc.gross_weight,
        "net_weight": doc.net_weight,
        "incoterm": doc.incoterm,
        "package_count": doc.package_count,
        "bl_reference": doc.bl_reference,
    }
    corrupted: set[str] = set()

    if hit("value_ocr"):
        ext["declared_value"] = float(_confuse_digit(f"{doc.declared_value:.2f}", rng))
        corrupted.add("declared_value")
    if hit("weight_ocr"):
        which = rng.choice(["gross_weight", "net_weight"])
        ext[which] = float(_confuse_digit(f"{ext[which]:.1f}", rng))
        corrupted.add(which)
    if hit("hs_truncate"):
        ext["hs_code"] = doc.hs_code[:6]
        corrupted.add("hs_code")
    elif hit("hs_digit"):
        ext["hs_code"] = _confuse_digit(doc.hs_code, rng)
        corrupted.add("hs_code")
    if hit("origin_missing"):
        ext["origin_country"] = None
        corrupted.add("origin_country")
    if hit("currency_wrong"):
        ext["currency"] = CURRENCY_CONFUSION[doc.currency]
        corrupted.add("currency")
    if hit("eori_digit"):
        ext["importer_eori"] = doc.importer_eori[:2] + _confuse_digit(doc.importer_eori[2:], rng)
        corrupted.add("importer_eori")
    if hit("bl_ocr"):
        ext["bl_reference"] = _confuse_bl_chars(doc.bl_reference, rng)
        corrupted.add("bl_reference")
    if hit("incoterm_confusion"):
        ext["incoterm"] = INCOTERM_CONFUSION[doc.incoterm]
        corrupted.add("incoterm")
    if hit("quantity_slip"):
        ext["quantity"] = rng.choice(
            [doc.quantity * 10, max(1, doc.quantity // 10), doc.quantity + 1]
        )
        corrupted.add("quantity")
    if hit("package_off_by_one"):
        ext["package_count"] = max(1, doc.package_count + rng.choice([-1, 1]))
        corrupted.add("package_count")
    if hit("net_exceeds_gross"):
        ext["net_weight"] = round(doc.gross_weight * rng.uniform(1.02, 1.15), 1)
        corrupted.add("net_weight")

    # Invoice date: possibly swap day and month, then render in the source's
    # own format.
    extracted_date = doc.invoice_date
    d = doc.invoice_date
    if d.day <= 12 and d.day != d.month and hit("date_swap"):
        extracted_date = date(d.year, d.day, d.month)
        corrupted.add("invoice_date")
    ext["invoice_date"] = _format_extracted_date(extracted_date, doc.source_system, rng)

    # Invoice lines (only serialised for Globex XML).
    line_amounts = list(doc.line_amounts)
    if doc.source_system == GLOBEX and hit("line_sum_error"):
        i = rng.randrange(len(line_amounts))
        line_amounts[i] = float(_confuse_digit(f"{line_amounts[i]:.2f}", rng))

    confidence: dict[str, float] = {}
    for field in CONFIDENCE_FIELDS:
        if ext.get(field) is None:
            continue  # nothing extracted, no score reported
        if field in corrupted:
            score = rng.uniform(0.45, 0.93)
        elif rng.random() < 0.05:
            score = rng.uniform(0.55, 0.82)  # correct but unsure
        else:
            score = rng.uniform(0.82, 0.995)
        confidence[field] = round(score, 3)

    return ext, confidence, line_amounts


# ---------------------------------------------------------------------------
# the filed side (with legitimate post-filing amendments)
# ---------------------------------------------------------------------------


def _file_with_possible_amendment(
    doc: TrueDoc, rng: random.Random
) -> tuple[dict, list[dict]]:
    """Return (filed row, amendment truth rows).  Filed values start as the
    truth; a small share of documents is then legitimately amended after
    filing, so the filed value differs from what any perfect extraction of
    the original document would say."""
    filed = {
        "shipment_id": doc.shipment_id,
        "doc_id": doc.doc_id,
        "country": doc.country,
        "hs_code": doc.hs_code,
        "declared_value": f"{doc.declared_value:.2f}",
        "currency": doc.currency,
        "origin_country": doc.origin_country,
        "quantity": doc.quantity,
        "gross_weight": doc.gross_weight,
        "net_weight": doc.net_weight,
        "package_count": doc.package_count,
        "incoterm": doc.incoterm,
        "importer_eori": doc.importer_eori,
        "bl_reference": doc.bl_reference,
        "invoice_date": doc.invoice_date.isoformat(),
        "filed_at": doc.filed_at.isoformat(sep=" ", timespec="seconds"),
    }
    amendments: list[dict] = []
    if rng.random() < AMENDMENT_RATE:
        field = rng.choice(["declared_value", "quantity", "hs_code"])
        if field == "declared_value":
            new = round(doc.declared_value * rng.choice([0.90, 0.95, 1.05, 1.10]), 2)
            filed["declared_value"] = f"{new:.2f}"
            note = "value revised after filing (e.g. credit note / recalculated freight)"
        elif field == "quantity":
            new = max(1, doc.quantity + rng.choice([-2, -1, 1, 2]))
            filed["quantity"] = new
            note = "quantity corrected after physical inspection"
        else:
            new = rng.choice([c for c in HS_POOL if c != doc.hs_code])
            filed["hs_code"] = new
            note = "reclassified by customs broker after filing"
        amendments.append(
            {
                "doc_id": doc.doc_id,
                "field": field,
                "value_on_document": getattr(doc, field),
                "filed_value": new,
                "note": note,
            }
        )
    return filed, amendments


# ---------------------------------------------------------------------------
# writers for the fake source formats
# ---------------------------------------------------------------------------


def _write_acme_csv(entries: list[tuple[TrueDoc, dict, dict]], path: Path) -> None:
    columns = [
        "shp_ref", "document_no", "decl_country", "tariff_code", "inv_value",
        "ccy", "coo", "qty", "grs_kg", "net_kg", "pkgs", "terms", "eori_no",
        "bl_no", "inv_date", "extracted_at",
    ] + [f"conf_{field}" for field in CONFIDENCE_FIELDS]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for doc, ext, confidence in entries:
            row = {
                "shp_ref": doc.shipment_id,
                "document_no": doc.doc_id,
                "decl_country": doc.country,
                "tariff_code": ext["hs_code"],
                "inv_value": f"{ext['declared_value']:.2f}",
                "ccy": ext["currency"],
                "coo": ext["origin_country"] or "",
                "qty": ext["quantity"],
                "grs_kg": ext["gross_weight"],
                "net_kg": ext["net_weight"],
                "pkgs": ext["package_count"],
                "terms": ext["incoterm"],
                "eori_no": ext["importer_eori"],
                "bl_no": ext["bl_reference"],
                "inv_date": ext["invoice_date"],
                "extracted_at": doc.extracted_at.isoformat(sep=" ", timespec="seconds"),
            }
            for field in CONFIDENCE_FIELDS:
                row[f"conf_{field}"] = confidence.get(field, "")
            writer.writerow(row)


def _write_globex_xml(
    entries: list[tuple[TrueDoc, dict, dict, list[float]]], path: Path
) -> None:
    root = ET.Element("Extractions", system="GlobexCapture")
    for doc, ext, confidence, line_amounts in entries:
        document = ET.SubElement(
            root, "Document", id=doc.doc_id, extractedAt=doc.extracted_at.isoformat()
        )
        ET.SubElement(
            document, "Shipment", ref=doc.shipment_id, declarationCountry=doc.country
        )
        goods = ET.SubElement(document, "Goods")
        item_attrs = {"tariffCode": ext["hs_code"]}
        if ext["origin_country"] is not None:
            item_attrs["originCountry"] = ext["origin_country"]
        item = ET.SubElement(goods, "Item", **item_attrs)
        ET.SubElement(item, "Quantity").text = str(ext["quantity"])
        ET.SubElement(
            item, "Weights",
            gross=str(ext["gross_weight"]), net=str(ext["net_weight"]),
        )
        ET.SubElement(item, "Packages").text = str(ext["package_count"])
        commercial = ET.SubElement(document, "Commercial", incoterm=ext["incoterm"])
        invoice = ET.SubElement(
            commercial, "Invoice",
            date=ext["invoice_date"], currency=ext["currency"],
            total=f"{ext['declared_value']:.2f}",
        )
        for amount in line_amounts:
            ET.SubElement(invoice, "Line", amount=f"{amount:.2f}")
        ET.SubElement(commercial, "Transport", blReference=ext["bl_reference"])
        ET.SubElement(commercial, "Importer", eori=ext["importer_eori"])
        conf_el = ET.SubElement(document, "Confidence")
        for field, score in confidence.items():
            ET.SubElement(conf_el, "Field", name=field, score=str(score))
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _write_filed_csv(rows: list[dict], path: Path) -> None:
    columns = [
        "shipment_id", "doc_id", "country", "hs_code", "declared_value",
        "currency", "origin_country", "quantity", "gross_weight", "net_weight",
        "package_count", "incoterm", "importer_eori", "bl_reference",
        "invoice_date", "filed_at",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _write_tariff_csv(path: Path) -> None:
    """All 10-digit codes plus their 8-digit CN prefixes, and some decoys,
    so both accepted formats validate against the same list."""
    codes = sorted(
        {code for code in HS_POOL + TARIFF_DECOYS}
        | {code[:8] for code in HS_POOL + TARIFF_DECOYS}
    )
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["hs_code"])
        writer.writerows([code] for code in codes)


def _write_amendments_csv(rows: list[dict], path: Path) -> None:
    columns = ["doc_id", "field", "value_on_document", "filed_value", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def generate(n_docs: int = 3000, seed: int = 42, data_dir: str | Path = "data") -> dict:
    """Write all synthetic files into data_dir and return a summary dict."""
    rng = random.Random(seed)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    acme_entries: list[tuple[TrueDoc, dict, dict]] = []
    globex_entries: list[tuple[TrueDoc, dict, dict, list[float]]] = []
    filed_rows: list[dict] = []
    amendment_rows: list[dict] = []

    for i in range(n_docs):
        doc = _make_true_doc(i, rng)
        ext, confidence, line_amounts = _corrupt_for_extraction(doc, rng)
        filed, amendments = _file_with_possible_amendment(doc, rng)
        filed_rows.append(filed)
        amendment_rows.extend(amendments)
        if doc.source_system == ACME:
            acme_entries.append((doc, ext, confidence))
        else:
            globex_entries.append((doc, ext, confidence, line_amounts))

    paths = {
        "acme_csv": data_dir / "extracted_acme.csv",
        "globex_xml": data_dir / "extracted_globex.xml",
        "filed_csv": data_dir / "filed_customs_ledger.csv",
        "tariff_csv": data_dir / "tariff_codes.csv",
        "amendments_csv": data_dir / "amendments_truth.csv",
    }
    _write_acme_csv(acme_entries, paths["acme_csv"])
    _write_globex_xml(globex_entries, paths["globex_xml"])
    _write_filed_csv(filed_rows, paths["filed_csv"])
    _write_tariff_csv(paths["tariff_csv"])
    _write_amendments_csv(amendment_rows, paths["amendments_csv"])

    return {
        "n_docs": n_docs,
        "seed": seed,
        "n_acme": len(acme_entries),
        "n_globex": len(globex_entries),
        "n_amended_docs": len({row["doc_id"] for row in amendment_rows}),
        "paths": {name: str(p) for name, p in paths.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    summary = generate(n_docs=args.docs, seed=args.seed, data_dir=args.data_dir)
    print(
        f"Generated {summary['n_docs']} documents "
        f"({summary['n_acme']} Acme CSV, {summary['n_globex']} Globex XML), "
        f"{summary['n_amended_docs']} legitimately amended after filing."
    )
    for name, path in summary["paths"].items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
