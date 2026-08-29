"""Generate a few thousand paired (extracted, filed) multi-item declarations
with realistic, deliberately injected error patterns.

The generator knows the ground truth; the harness never sees it.  The one
piece of truth written to disk is the amendments sidecar
(data/amendments_truth.csv): values the FILED side legitimately changed
after filing (revaluations, corrected counts, reclassifications).  Those
show up to the harness as mismatches that are NOT extraction errors --
exactly the ambiguity a real deployment faces.  evaluate.py never reads
that file; it exists so a human can check how much of the measured error
rate the ambiguity accounts for.

Population: declarations filed in Germany and the Netherlands, 1-5 goods
items each, with multi-item documents sometimes drawing several items from
the same HS chapter (that is what real invoices look like, and it is what
makes item alignment genuinely hard).  NL documents are systematically
~2.5x worse -- a worse scan lane, nothing about the country itself.

Errors come in three styles, and the style column in the sidecar matters:

  NOISE (the OCR-era failure mode) -- detectable-ish garbage:
  - OCR digit confusion in the invoice total, item values, weights, EORI
    and BL reference
  - HS codes truncated to 6 digits or with one misread digit
  - day/month swapped in the invoice date (the DD/MM vs MM/DD classic)
  - missing origin country, wrong supplementary unit
  - incoterm and currency confusion
  - net weight exceeding gross (caught by validity, no ground truth needed)

  FABRICATION (the LLM-era failure mode) -- fluent garbage that passes
  every validity rule by construction, delivered with HIGH confidence:
  - a different but perfectly valid EORI (right country, right length,
    correct check digit)
  - a different but in-tariff HS code from the same chapter (right format,
    in the tariff list, same supplementary unit)
  - a different but real origin country
  No blind signal exists for these; only the proxy or an audited gold
  sample can find them.

  STRUCTURAL (the reason this harness is multi-item):
  - a dropped goods item (the last line falls off a page break)
  - two same-chapter items merged into one (values/quantities summed)
  - a spurious item: the invoice's subtotal line read as goods
  A drop or a spurious line breaks the items-sum-to-total arithmetic, so
  validity can catch it before filing; a merge preserves the sum and
  cannot be caught that way -- deliberately.

Truth written to disk (the harness proper never reads any of it):
  gold_truth.csv            the true records, in the ledger export schema
                            -- what a perfect extraction would produce.
                            Only exists because this data is synthetic; in
                            production its stand-in is a human-graded
                            audit sample.
  injected_errors_truth.csv every injected error with its kind and style,
                            so a human (or run_demo's synthetic-only
                            diagnostic) can check which styles the blind
                            signals actually catch.
  amendments_truth.csv      legitimate post-filing changes on the FILED
                            side -- mismatches that are not extraction
                            errors.

Confidence scores are generated to be *imperfectly* honest: noise-corrupted
fields tend to get lower confidence, fabricated fields get high confidence
(fluent garbage reads as confident), and dropped items produce no low
score anywhere -- a confidence gate cannot see what is not there.
"""

from __future__ import annotations

import argparse
import csv
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from reference_data import EORI_FORMATS, VALID_QUANTITY_UNITS, expected_quantity_unit
from validity import luhn_check_digit

ACME = "acme_extract"
GLOBEX = "globex_capture"

# --- population shape ------------------------------------------------------

COUNTRY_WEIGHTS = {"DE": 0.58, "NL": 0.42}
BAD_COUNTRY = "NL"          # scans from the NL lane are systematically worse
BAD_COUNTRY_MULTIPLIER = 2.5

ORIGIN_COUNTRIES = ["CN", "US", "IN", "VN", "TR", "GB", "JP", "KR", "TW", "TH", "MY", "MX"]
CURRENCY_WEIGHTS = {"EUR": 0.80, "USD": 0.15, "GBP": 0.05}
INCOTERMS = ["EXW", "FOB", "CIF", "CFR", "DAP", "DDP", "FCA"]
CARRIER_PREFIXES = ["MSCU", "MAEU", "HLCU", "CMDU", "ONEY", "COSU"]

# Goods items per declaration: mostly small, occasionally busy.
N_ITEMS_WEIGHTS = {1: 0.45, 2: 0.25, 3: 0.15, 4: 0.10, 5: 0.05}
SAME_CHAPTER_DOC_SHARE = 0.35  # multi-item docs drawing all items from one chapter

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

_POOL_BY_CHAPTER: dict[str, list[str]] = {}
for _code in HS_POOL:
    _POOL_BY_CHAPTER.setdefault(_code[:2], []).append(_code)
_MERGEABLE_CHAPTERS = [c for c, codes in _POOL_BY_CHAPTER.items() if len(codes) >= 2]

# --- error rates (base; multiplied for BAD_COUNTRY) ------------------------

RATES = {
    # header-level, per document
    "total_ocr": 0.03,        # digit confusion in the printed invoice total
    "currency_wrong": 0.015,
    "eori_digit": 0.02,
    "bl_ocr": 0.05,           # O/0, I/1, B/8, S/5 confusion in BL reference
    "incoterm_confusion": 0.02,
    "date_swap": 0.08,        # day/month swapped (only possible when day <= 12)
    # item-level, per goods item
    "item_value_ocr": 0.04,
    "hs_truncate": 0.03,      # HS code truncated to 6 digits
    "hs_digit": 0.015,        # one HS digit misread
    "origin_missing": 0.05,
    "quantity_slip": 0.02,
    "unit_wrong": 0.02,       # quantity read against the wrong unit
    "weight_ocr": 0.025,
    "net_exceeds_gross": 0.012,
    "package_off_by_one": 0.015,
    # fabrication (LLM-style): plausible, valid, wrong, confident
    "eori_fabricated": 0.01,      # per document
    "hs_fabricated": 0.015,       # per item (same-chapter sibling)
    "origin_fabricated": 0.015,   # per item (different real country)
    # structural, per document
    "drop_item": 0.025,       # last line falls off a page break (needs >= 2 items)
    "merge_items": 0.02,      # two same-chapter lines read as one (needs candidates)
    "spurious_item": 0.012,   # subtotal line read as a goods item
}
AMENDMENT_RATE = 0.05         # per document; NOT an extraction error

# Error styles recorded in the injected-errors sidecar.
NOISE = "noise"               # OCR-era: garbage that often looks like garbage
FABRICATION = "fabrication"   # LLM-era: valid-looking, high-confidence, wrong
STRUCTURAL = "structural"     # whole goods items dropped / merged / invented

# Model profiles for the A/B demo (generate_ab): per-rate multipliers on
# RATES, so the same documents can be "extracted" by two different kinds of
# tool.  The OCR model barely fabricates (it mangles what it sees); the LLM
# model reads mangled scans far better but makes things up and invents
# lines from subtotals -- each model failing in its own characteristic way.
MODEL_PROFILES: dict[str, dict[str, float]] = {
    "ocr_model": {
        "eori_fabricated": 0.2, "hs_fabricated": 0.15, "origin_fabricated": 0.15,
    },
    "llm_model": {
        # reads through the noise...
        "total_ocr": 0.3, "item_value_ocr": 0.3, "weight_ocr": 0.3,
        "eori_digit": 0.25, "bl_ocr": 0.4, "hs_truncate": 0.25, "hs_digit": 0.3,
        "date_swap": 0.4, "quantity_slip": 0.5, "package_off_by_one": 0.5,
        "unit_wrong": 0.6, "origin_missing": 0.4, "net_exceeds_gross": 0.6,
        "currency_wrong": 0.6, "incoterm_confusion": 0.6,
        # ...but fabricates and invents lines
        "eori_fabricated": 2.5, "hs_fabricated": 2.0, "origin_fabricated": 2.0,
        "drop_item": 0.5, "merge_items": 0.8, "spurious_item": 1.5,
    },
}


def profile_rates(model: str) -> dict[str, float]:
    """RATES with one model profile's multipliers applied."""
    multipliers = MODEL_PROFILES[model]
    return {name: rate * multipliers.get(name, 1.0) for name, rate in RATES.items()}

OCR_DIGIT_CONFUSION = {"0": "8", "1": "7", "2": "7", "3": "8", "4": "9",
                       "5": "6", "6": "5", "7": "1", "8": "3", "9": "4"}
BL_CHAR_CONFUSION = {"0": "O", "O": "0", "1": "I", "I": "1",
                     "8": "B", "B": "8", "5": "S", "S": "5"}
INCOTERM_CONFUSION = {"CIF": "CFR", "CFR": "CIF", "FOB": "FCA", "FCA": "FOB",
                      "DAP": "DDP", "DDP": "DAP", "EXW": "FOB"}
CURRENCY_CONFUSION = {"EUR": "USD", "USD": "EUR", "GBP": "EUR"}

# Fields that carry an extraction confidence score.
HEADER_CONFIDENCE_FIELDS = [
    "declared_value", "currency", "importer_eori", "incoterm",
    "invoice_date", "bl_reference",
]
ITEM_CONFIDENCE_FIELDS = [
    "hs_code", "origin_country", "item_value", "quantity", "quantity_unit",
    "net_weight", "gross_weight", "package_count",
]


@dataclass
class TrueItem:
    """One goods item as it actually was on the paper."""

    item_number: int
    hs_code: str
    origin_country: str
    quantity: float
    quantity_unit: str
    item_value: float
    gross_weight: float
    net_weight: float
    package_count: int


@dataclass
class TrueDoc:
    """What was actually on the paper -- the generator's private truth."""

    doc_id: str
    shipment_id: str
    country: str
    source_system: str
    currency: str
    incoterm: str
    importer_eori: str
    bl_reference: str
    invoice_date: date
    invoice_total: float  # always the exact sum of item values
    items: list[TrueItem]
    extracted_at: datetime
    filed_at: datetime


# ---------------------------------------------------------------------------
# building true documents
# ---------------------------------------------------------------------------


def _weighted_choice(rng: random.Random, weights: dict) -> object:
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def _make_eori(rng: random.Random, country: str) -> str:
    """Real national digit counts (see reference_data.EORI_FORMATS), with
    our synthetic Luhn check digit at the end."""
    n_digits = EORI_FORMATS[country]
    payload = "".join(str(rng.randint(0, 9)) for _ in range(n_digits - 1))
    return f"{country}{payload}{luhn_check_digit(payload)}"


def _make_true_item(number: int, hs_code: str, rng: random.Random) -> TrueItem:
    unit = expected_quantity_unit(hs_code)
    net = round(rng.uniform(2, 4000), 1)
    if unit == "KGM":
        quantity = net  # weight-based goods: the supplementary quantity IS net mass
    elif unit == "NPR":
        quantity = float(rng.randint(1, 1000))
    elif unit == "LTR":
        quantity = round(rng.uniform(10, 10_000), 1)
    else:  # NAR
        quantity = float(rng.randint(1, 500))
    return TrueItem(
        item_number=number,
        hs_code=hs_code,
        origin_country=rng.choice(ORIGIN_COUNTRIES),
        quantity=quantity,
        quantity_unit=unit,
        item_value=round(rng.uniform(300, 80_000), 2),
        gross_weight=round(net * rng.uniform(1.02, 1.30), 1),
        net_weight=net,
        package_count=rng.randint(1, 40),
    )


def _pick_item_codes(n_items: int, rng: random.Random) -> list[str]:
    """Multi-item invoices often list several lines from one chapter (three
    kinds of shirt, two computer models).  Same-chapter items are also the
    ones alignment can genuinely confuse, so they must exist."""
    if n_items >= 2 and rng.random() < SAME_CHAPTER_DOC_SHARE:
        chapter_codes = _POOL_BY_CHAPTER[rng.choice(_MERGEABLE_CHAPTERS)]
        return [rng.choice(chapter_codes) for _ in range(n_items)]
    return [rng.choice(HS_POOL) for _ in range(n_items)]


def _make_true_doc(i: int, rng: random.Random) -> TrueDoc:
    invoice_date = date.today() - timedelta(days=rng.randint(20, 540))
    extracted_at = datetime.combine(
        invoice_date + timedelta(days=rng.randint(2, 12)),
        time(rng.randint(6, 20), rng.randint(0, 59)),
    )
    country = _weighted_choice(rng, COUNTRY_WEIGHTS)
    n_items = _weighted_choice(rng, N_ITEMS_WEIGHTS)
    items = [
        _make_true_item(number, code, rng)
        for number, code in enumerate(_pick_item_codes(n_items, rng), start=1)
    ]
    return TrueDoc(
        doc_id=f"DOC-{i:06d}",
        shipment_id=f"SHP-{i:06d}",
        country=country,
        source_system=ACME if rng.random() < 0.55 else GLOBEX,
        currency=_weighted_choice(rng, CURRENCY_WEIGHTS),
        incoterm=rng.choice(INCOTERMS),
        importer_eori=_make_eori(rng, country),
        bl_reference=f"{rng.choice(CARRIER_PREFIXES)}{rng.randint(1_000_000, 9_999_999)}",
        invoice_date=invoice_date,
        invoice_total=round(sum(item.item_value for item in items), 2),
        items=items,
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
        fmt = rng.choices(["%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"], weights=[70, 15, 15])[0]
    else:
        fmt = "%Y-%m-%d" if rng.random() < 0.7 else "%d/%m/%Y"
    return d.strftime(fmt)


@dataclass
class ExtractedItem:
    """One goods item as the extraction tool produced it."""

    item_number: int
    hs_code: str | None
    origin_country: str | None
    quantity: float
    quantity_unit: str
    item_value: float
    gross_weight: float
    net_weight: float
    package_count: int
    confidence: dict[str, float]


def _corrupt_item(
    item: TrueItem, rng, hit
) -> tuple[ExtractedItem, list[dict]]:
    """Field-level corruption of one goods item.  Returns the extracted
    item (confidence filled in later) and a list of injected errors, each
    {field, kind, style}."""
    values = {
        "hs_code": item.hs_code,
        "origin_country": item.origin_country,
        "quantity": item.quantity,
        "quantity_unit": item.quantity_unit,
        "item_value": item.item_value,
        "gross_weight": item.gross_weight,
        "net_weight": item.net_weight,
        "package_count": item.package_count,
    }
    errors: list[dict] = []

    def log(field: str, kind: str, style: str) -> None:
        errors.append({"field": field, "kind": kind, "style": style})

    if hit("item_value_ocr"):
        values["item_value"] = float(_confuse_digit(f"{item.item_value:.2f}", rng))
        log("item_value", "ocr_digit", NOISE)
    # HS: fabrication first (a plausible same-chapter sibling that passes
    # every validity rule), else the OCR-era noise variants.
    hs_siblings = [c for c in _POOL_BY_CHAPTER[item.hs_code[:2]] if c != item.hs_code]
    if hs_siblings and hit("hs_fabricated"):
        values["hs_code"] = rng.choice(hs_siblings)
        log("hs_code", "fabricated_code", FABRICATION)
    elif hit("hs_truncate"):
        values["hs_code"] = item.hs_code[:6]
        log("hs_code", "truncated_code", NOISE)
    elif hit("hs_digit"):
        values["hs_code"] = _confuse_digit(item.hs_code, rng)
        log("hs_code", "ocr_digit", NOISE)
    # Origin: a confidently wrong real country, or simply missing.
    if hit("origin_fabricated"):
        values["origin_country"] = rng.choice(
            [c for c in ORIGIN_COUNTRIES if c != item.origin_country]
        )
        log("origin_country", "fabricated_value", FABRICATION)
    elif hit("origin_missing"):
        values["origin_country"] = None
        log("origin_country", "missing", NOISE)
    if hit("quantity_slip"):
        values["quantity"] = rng.choice(
            [item.quantity * 10, max(1.0, item.quantity // 10), item.quantity + 1]
        )
        log("quantity", "quantity_slip", NOISE)
    if hit("unit_wrong"):
        values["quantity_unit"] = rng.choice(
            sorted(VALID_QUANTITY_UNITS - {item.quantity_unit})
        )
        log("quantity_unit", "unit_confusion", NOISE)
    if hit("weight_ocr"):
        which = rng.choice(["gross_weight", "net_weight"])
        values[which] = float(_confuse_digit(f"{values[which]:.1f}", rng))
        log(which, "ocr_digit", NOISE)
    if hit("net_exceeds_gross"):
        values["net_weight"] = round(item.gross_weight * rng.uniform(1.02, 1.15), 1)
        log("net_weight", "net_gross_inversion", NOISE)
    if hit("package_off_by_one"):
        values["package_count"] = max(1, item.package_count + rng.choice([-1, 1]))
        log("package_count", "count_slip", NOISE)

    return ExtractedItem(item_number=item.item_number, confidence={}, **values), errors


def _merge_candidates(items: list[ExtractedItem]) -> list[int]:
    """Indexes i where items[i] and items[i+1] share an HS chapter."""
    return [
        i
        for i in range(len(items) - 1)
        if (items[i].hs_code or "")[:2] == (items[i + 1].hs_code or "")[:2]
    ]


def _describe_true_item(item: TrueItem) -> str:
    return f"HS {item.hs_code}, {item.quantity:g} {item.quantity_unit}, value {item.item_value:,.2f}"


def _corrupt_for_extraction(
    doc: TrueDoc, rng: random.Random, rates: dict[str, float] | None = None
) -> tuple[dict, dict[str, float], list[ExtractedItem], list[dict]]:
    """Return (extracted header, header confidence, extracted items,
    injected error rows for the truth sidecar).  `rates` defaults to the
    module RATES; generate_ab passes a model profile's rates instead."""
    rates = rates if rates is not None else RATES
    multiplier = BAD_COUNTRY_MULTIPLIER if doc.country == BAD_COUNTRY else 1.0

    def hit(rate_name: str) -> bool:
        return rng.random() < min(rates[rate_name] * multiplier, 0.95)

    error_rows: list[dict] = []

    def log_error(item_number, field, kind, style, true_value, extracted_value) -> None:
        error_rows.append(
            {
                "doc_id": doc.doc_id,
                "item_number": item_number,
                "field": field,
                "kind": kind,
                "style": style,
                "value_on_document": true_value,
                "extracted_value": extracted_value,
            }
        )

    # --- items: field corruption first, then structure -------------------
    items: list[ExtractedItem] = []
    true_by_position: list[TrueItem] = list(doc.items)
    errors_by_item: list[list[dict]] = []
    for item in doc.items:
        extracted, errors = _corrupt_item(item, rng, hit)
        items.append(extracted)
        errors_by_item.append(errors)

    if len(items) >= 2 and hit("drop_item"):
        dropped = true_by_position.pop()
        items.pop()          # the last line fell off a page break
        errors_by_item.pop()  # its field-level errors vanished with it
        log_error(dropped.item_number, "(whole item)", "dropped_item", STRUCTURAL,
                  _describe_true_item(dropped), "")

    candidates = _merge_candidates(items)
    if candidates and hit("merge_items"):
        i = rng.choice(candidates)
        first, second = items[i], items[i + 1]
        items[i] = replace(
            first,
            quantity=first.quantity + second.quantity,
            item_value=round(first.item_value + second.item_value, 2),
            gross_weight=round(first.gross_weight + second.gross_weight, 1),
            net_weight=round(first.net_weight + second.net_weight, 1),
            package_count=first.package_count + second.package_count,
        )
        absorbed = true_by_position.pop(i + 1)
        items.pop(i + 1)
        errors_by_item.pop(i + 1)  # the absorbed line's own errors vanished
        for field in ("quantity", "item_value", "gross_weight", "net_weight", "package_count"):
            errors_by_item[i].append({"field": field, "kind": "merged_items", "style": STRUCTURAL})
        log_error(absorbed.item_number, "(whole item)", "merged_items", STRUCTURAL,
                  _describe_true_item(absorbed),
                  f"(absorbed into extracted line {i + 1})")

    spurious_index: int | None = None
    if hit("spurious_item"):
        # The invoice's subtotal line, read as one more goods item.
        template = items[-1]
        items.append(
            replace(
                template,
                quantity=1.0,
                item_value=round(sum(it.item_value for it in items), 2),
                gross_weight=round(sum(it.gross_weight for it in items), 1),
                net_weight=round(sum(it.net_weight for it in items), 1),
                package_count=sum(it.package_count for it in items),
                confidence={},  # replace() would otherwise share template's dict
            )
        )
        errors_by_item.append([
            {"field": field, "kind": "spurious_item", "style": STRUCTURAL}
            for field in ITEM_CONFIDENCE_FIELDS
        ])
        spurious_index = len(items) - 1

    # Renumber as the extraction tool would: in the order it saw them.
    for position, item in enumerate(items, start=1):
        item.item_number = position
    if spurious_index is not None:
        spurious = items[spurious_index]
        log_error(spurious.item_number, "(whole item)", "spurious_item", STRUCTURAL,
                  "", f"subtotal read as goods: {spurious.hs_code}, value {spurious.item_value:,.2f}")

    # --- item confidence + sidecar rows for surviving field errors -------
    # (the spurious item, if any, sits past the end of true_by_position:
    # it has no true counterpart to log against, only confidence to score)
    for index, (item, errors) in enumerate(zip(items, errors_by_item)):
        styles = {error["field"]: error["style"] for error in errors}
        for field in ITEM_CONFIDENCE_FIELDS:
            if getattr(item, field) is None:
                continue  # nothing extracted, no score reported
            item.confidence[field] = _confidence_score(styles.get(field), rng)
        if index >= len(true_by_position):
            continue  # spurious: already logged as one whole-item event
        true_item = true_by_position[index]
        for error in errors:
            if error["kind"] in ("merged_items", "spurious_item"):
                continue  # already logged as one whole-item event
            log_error(item.item_number, error["field"], error["kind"], error["style"],
                      getattr(true_item, error["field"]), getattr(item, error["field"]))

    # --- header -----------------------------------------------------------
    header = {
        "declared_value": doc.invoice_total,
        "currency": doc.currency,
        "incoterm": doc.incoterm,
        "importer_eori": doc.importer_eori,
        "bl_reference": doc.bl_reference,
    }
    header_errors: list[dict] = []

    def log_header(field: str, kind: str, style: str) -> None:
        header_errors.append({"field": field, "kind": kind, "style": style})

    if hit("total_ocr"):
        header["declared_value"] = float(_confuse_digit(f"{doc.invoice_total:.2f}", rng))
        log_header("declared_value", "ocr_digit", NOISE)
    if hit("currency_wrong"):
        header["currency"] = CURRENCY_CONFUSION[doc.currency]
        log_header("currency", "code_confusion", NOISE)
    if hit("eori_fabricated"):
        # A perfectly valid, entirely wrong EORI: right country, right
        # length, correct check digit.  No blind signal exists for this.
        header["importer_eori"] = _make_eori(rng, doc.importer_eori[:2])
        log_header("importer_eori", "fabricated_value", FABRICATION)
    elif hit("eori_digit"):
        header["importer_eori"] = doc.importer_eori[:2] + _confuse_digit(
            doc.importer_eori[2:], rng
        )
        log_header("importer_eori", "ocr_digit", NOISE)
    if hit("bl_ocr"):
        header["bl_reference"] = _confuse_bl_chars(doc.bl_reference, rng)
        log_header("bl_reference", "ocr_characters", NOISE)
    if hit("incoterm_confusion"):
        header["incoterm"] = INCOTERM_CONFUSION[doc.incoterm]
        log_header("incoterm", "code_confusion", NOISE)

    extracted_date = doc.invoice_date
    d = doc.invoice_date
    if d.day <= 12 and d.day != d.month and hit("date_swap"):
        extracted_date = date(d.year, d.day, d.month)
        log_header("invoice_date", "day_month_swap", NOISE)
    header["invoice_date"] = _format_extracted_date(extracted_date, doc.source_system, rng)

    header_styles = {error["field"]: error["style"] for error in header_errors}
    header_confidence = {
        field: _confidence_score(header_styles.get(field), rng)
        for field in HEADER_CONFIDENCE_FIELDS
    }
    true_header = {
        "declared_value": doc.invoice_total, "currency": doc.currency,
        "incoterm": doc.incoterm, "importer_eori": doc.importer_eori,
        "bl_reference": doc.bl_reference, "invoice_date": doc.invoice_date.isoformat(),
    }
    for error in header_errors:
        log_error(None, error["field"], error["kind"], error["style"],
                  true_header[error["field"]], header[error["field"]])

    return header, header_confidence, items, error_rows


def _confidence_score(error_style: str | None, rng: random.Random) -> float:
    """Confidence for one extracted field, given how it was corrupted.

    Fabricated values score HIGH -- fluent garbage reads as confident,
    which is exactly why LLM-era errors slip through confidence gates.
    Noise and structural corruption score lower but overlapping the
    correct range, so calibration is imperfect rather than cartoonish."""
    if error_style == FABRICATION:
        return round(rng.uniform(0.75, 0.97), 3)
    if error_style is not None:
        return round(rng.uniform(0.45, 0.93), 3)
    if rng.random() < 0.05:
        return round(rng.uniform(0.55, 0.82), 3)  # correct but unsure
    return round(rng.uniform(0.82, 0.995), 3)


# ---------------------------------------------------------------------------
# the filed side (with legitimate post-filing amendments)
# ---------------------------------------------------------------------------


def _file_with_possible_amendment(
    doc: TrueDoc, rng: random.Random
) -> tuple[list[TrueItem], float, list[dict]]:
    """Return (filed items, filed invoice total, amendment truth rows).

    Filed values start as the truth; a small share of documents is then
    legitimately amended after filing.  The ledger stays internally
    consistent: amend an item value and the filed invoice total is
    recomputed, exactly as a filing system would."""
    filed_items = [replace(item) for item in doc.items]
    amendments: list[dict] = []

    def note_amendment(item: TrueItem, field: str, old, new, note: str) -> None:
        amendments.append(
            {
                "doc_id": doc.doc_id,
                "item_number": item.item_number,
                "field": field,
                "value_on_document": old,
                "filed_value": new,
                "note": note,
            }
        )

    if rng.random() < AMENDMENT_RATE:
        kind = rng.choice(["item_value", "hs_reclass", "quantity"])
        item = rng.choice(filed_items)
        if kind == "quantity" and item.quantity_unit == "KGM":
            kind = "item_value"  # KGM quantity is net mass; keep amendments simple
        # Broker reclassifications stay within the chapter (a different CN
        # split of the same kind of goods), so the supplementary unit is
        # untouched.  No sibling code in the pool -> amend the value instead.
        same_chapter = [
            c for c in _POOL_BY_CHAPTER.get(item.hs_code[:2], []) if c != item.hs_code
        ]
        if kind == "hs_reclass" and not same_chapter:
            kind = "item_value"
        if kind == "item_value":
            new_value = round(item.item_value * rng.choice([0.90, 0.95, 1.05, 1.10]), 2)
            note_amendment(
                item, "item_value", item.item_value, new_value,
                "value revised after filing (credit note / recalculated freight)",
            )
            item.item_value = new_value
        elif kind == "hs_reclass":
            new_code = rng.choice(same_chapter)
            note_amendment(
                item, "hs_code", item.hs_code, new_code,
                "reclassified by customs broker after filing",
            )
            item.hs_code = new_code
        else:
            new_quantity = float(max(1, int(item.quantity) + rng.choice([-2, -1, 1, 2])))
            note_amendment(
                item, "quantity", item.quantity, new_quantity,
                "quantity corrected after physical inspection",
            )
            item.quantity = new_quantity

    filed_total = round(sum(item.item_value for item in filed_items), 2)
    if filed_total != doc.invoice_total:
        amendments.append(
            {
                "doc_id": doc.doc_id,
                "item_number": None,
                "field": "declared_value",
                "value_on_document": doc.invoice_total,
                "filed_value": filed_total,
                "note": "invoice total recomputed from the amended item",
            }
        )
    return filed_items, filed_total, amendments


# ---------------------------------------------------------------------------
# writers for the fake source formats
# ---------------------------------------------------------------------------

_ACME_HEADER_CONF = [f"hconf_{field}" for field in HEADER_CONFIDENCE_FIELDS]
_ACME_ITEM_CONF = [f"conf_{field}" for field in ITEM_CONFIDENCE_FIELDS]


def _write_acme_csv(entries: list[tuple[TrueDoc, dict, dict, list[ExtractedItem]]], path: Path) -> None:
    columns = [
        "shp_ref", "document_no", "decl_country", "line_no", "tariff_code",
        "coo", "qty", "qty_unit", "item_val", "grs_kg", "net_kg", "pkgs",
        "inv_total", "ccy", "terms", "eori_no", "bl_no", "inv_date",
        "extracted_at",
    ] + _ACME_HEADER_CONF + _ACME_ITEM_CONF
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for doc, header, header_conf, items in entries:
            base = {
                "shp_ref": doc.shipment_id,
                "document_no": doc.doc_id,
                "decl_country": doc.country,
                "inv_total": f"{header['declared_value']:.2f}",
                "ccy": header["currency"],
                "terms": header["incoterm"],
                "eori_no": header["importer_eori"],
                "bl_no": header["bl_reference"],
                "inv_date": header["invoice_date"],
                "extracted_at": doc.extracted_at.isoformat(sep=" ", timespec="seconds"),
            }
            for field in HEADER_CONFIDENCE_FIELDS:
                base[f"hconf_{field}"] = header_conf.get(field, "")
            for item in items:
                row = base | {
                    "line_no": item.item_number,
                    "tariff_code": item.hs_code,
                    "coo": item.origin_country or "",
                    "qty": item.quantity,
                    "qty_unit": item.quantity_unit,
                    "item_val": f"{item.item_value:.2f}",
                    "grs_kg": item.gross_weight,
                    "net_kg": item.net_weight,
                    "pkgs": item.package_count,
                }
                for field in ITEM_CONFIDENCE_FIELDS:
                    row[f"conf_{field}"] = item.confidence.get(field, "")
                writer.writerow(row)


def _write_globex_xml(
    entries: list[tuple[TrueDoc, dict, dict, list[ExtractedItem]]], path: Path
) -> None:
    root = ET.Element("Extractions", system="GlobexCapture")
    for doc, header, header_conf, items in entries:
        document = ET.SubElement(
            root, "Document", id=doc.doc_id, extractedAt=doc.extracted_at.isoformat()
        )
        ET.SubElement(
            document, "Shipment", ref=doc.shipment_id, declarationCountry=doc.country
        )
        goods = ET.SubElement(document, "Goods")
        for item in items:
            item_attrs = {"number": str(item.item_number), "tariffCode": item.hs_code or ""}
            if item.origin_country is not None:
                item_attrs["originCountry"] = item.origin_country
            item_el = ET.SubElement(goods, "Item", **item_attrs)
            quantity_el = ET.SubElement(item_el, "Quantity", unit=item.quantity_unit)
            quantity_el.text = str(item.quantity)
            ET.SubElement(item_el, "Value").text = f"{item.item_value:.2f}"
            ET.SubElement(
                item_el, "Weights",
                gross=str(item.gross_weight), net=str(item.net_weight),
            )
            ET.SubElement(item_el, "Packages").text = str(item.package_count)
            conf_el = ET.SubElement(item_el, "Confidence")
            for field, score in item.confidence.items():
                ET.SubElement(conf_el, "Field", name=field, score=str(score))
        commercial = ET.SubElement(document, "Commercial", incoterm=header["incoterm"])
        ET.SubElement(
            commercial, "Invoice",
            date=header["invoice_date"], currency=header["currency"],
            total=f"{header['declared_value']:.2f}",
        )
        ET.SubElement(commercial, "Transport", blReference=header["bl_reference"])
        ET.SubElement(commercial, "Importer", eori=header["importer_eori"])
        conf_el = ET.SubElement(document, "Confidence")
        for field, score in header_conf.items():
            ET.SubElement(conf_el, "Field", name=field, score=str(score))
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _write_filed_csv(
    entries: list[tuple[TrueDoc, list[TrueItem], float]], path: Path
) -> None:
    columns = [
        "shipment_id", "doc_id", "country", "item_number", "hs_code",
        "origin_country", "quantity", "quantity_unit", "item_value",
        "gross_weight", "net_weight", "package_count", "declared_value",
        "currency", "incoterm", "importer_eori", "bl_reference",
        "invoice_date", "filed_at",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for doc, filed_items, filed_total in entries:
            for item in filed_items:
                writer.writerow(
                    {
                        "shipment_id": doc.shipment_id,
                        "doc_id": doc.doc_id,
                        "country": doc.country,
                        "item_number": item.item_number,
                        "hs_code": item.hs_code,
                        "origin_country": item.origin_country,
                        "quantity": item.quantity,
                        "quantity_unit": item.quantity_unit,
                        "item_value": f"{item.item_value:.2f}",
                        "gross_weight": item.gross_weight,
                        "net_weight": item.net_weight,
                        "package_count": item.package_count,
                        "declared_value": f"{filed_total:.2f}",
                        "currency": doc.currency,
                        "incoterm": doc.incoterm,
                        "importer_eori": doc.importer_eori,
                        "bl_reference": doc.bl_reference,
                        "invoice_date": doc.invoice_date.isoformat(),
                        "filed_at": doc.filed_at.isoformat(sep=" ", timespec="seconds"),
                    }
                )


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
    columns = ["doc_id", "item_number", "field", "value_on_document", "filed_value", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _write_injected_errors_csv(rows: list[dict], path: Path) -> None:
    columns = [
        "doc_id", "item_number", "field", "kind", "style",
        "value_on_document", "extracted_value",
    ]
    if rows and "model" in rows[0]:  # A/B data: which model made the error
        columns = ["model"] + columns
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

    acme_entries = []
    globex_entries = []
    filed_entries = []
    gold_entries = []
    amendment_rows: list[dict] = []
    injected_error_rows: list[dict] = []
    n_items_total = 0

    for i in range(n_docs):
        doc = _make_true_doc(i, rng)
        n_items_total += len(doc.items)
        header, header_conf, items, error_rows = _corrupt_for_extraction(doc, rng)
        injected_error_rows.extend(error_rows)
        filed_items, filed_total, amendments = _file_with_possible_amendment(doc, rng)
        filed_entries.append((doc, filed_items, filed_total))
        # Gold: the truth, in the same ledger export schema, so the same
        # reader loads it and evaluate() can score against it unchanged.
        gold_entries.append((doc, doc.items, doc.invoice_total))
        amendment_rows.extend(amendments)
        entry = (doc, header, header_conf, items)
        if doc.source_system == ACME:
            acme_entries.append(entry)
        else:
            globex_entries.append(entry)

    paths = {
        "acme_csv": data_dir / "extracted_acme.csv",
        "globex_xml": data_dir / "extracted_globex.xml",
        "filed_csv": data_dir / "filed_customs_ledger.csv",
        "gold_csv": data_dir / "gold_truth.csv",
        "tariff_csv": data_dir / "tariff_codes.csv",
        "amendments_csv": data_dir / "amendments_truth.csv",
        "errors_csv": data_dir / "injected_errors_truth.csv",
    }
    _write_acme_csv(acme_entries, paths["acme_csv"])
    _write_globex_xml(globex_entries, paths["globex_xml"])
    _write_filed_csv(filed_entries, paths["filed_csv"])
    _write_filed_csv(gold_entries, paths["gold_csv"])
    _write_tariff_csv(paths["tariff_csv"])
    _write_amendments_csv(amendment_rows, paths["amendments_csv"])
    _write_injected_errors_csv(injected_error_rows, paths["errors_csv"])

    styles = [row["style"] for row in injected_error_rows]
    return {
        "n_docs": n_docs,
        "n_items": n_items_total,
        "seed": seed,
        "n_acme": len(acme_entries),
        "n_globex": len(globex_entries),
        "n_amended_docs": len({row["doc_id"] for row in amendment_rows}),
        "n_injected_errors": len(injected_error_rows),
        "n_noise_errors": styles.count(NOISE),
        "n_fabrications": styles.count(FABRICATION),
        "n_structural_errors": styles.count(STRUCTURAL),
        "paths": {name: str(p) for name, p in paths.items()},
    }


def generate_ab(
    n_docs: int = 3000,
    seed: int = 42,
    data_dir: str | Path = "data/ab",
    models: tuple[str, ...] = ("ocr_model", "llm_model"),
) -> dict:
    """A/B data: the SAME documents extracted by several models, each with
    its own failure profile (see MODEL_PROFILES), against ONE shared filed
    ledger and ONE shared gold truth -- the setup a fair model comparison
    needs.  Every model's output is written in the Acme flat-CSV format as
    extracted_<model>.csv; read it with
    readers.acme_csv.read(path, source_system=<model>)."""
    rng = random.Random(seed)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    docs = [_make_true_doc(i, rng) for i in range(n_docs)]

    # The reference side exists once: same filings, same amendments, same
    # truth, whichever model is being judged.
    filed_entries = []
    amendment_rows: list[dict] = []
    for doc in docs:
        filed_items, filed_total, amendments = _file_with_possible_amendment(doc, rng)
        filed_entries.append((doc, filed_items, filed_total))
        amendment_rows.extend(amendments)

    paths = {
        "filed_csv": data_dir / "filed_customs_ledger.csv",
        "gold_csv": data_dir / "gold_truth.csv",
        "tariff_csv": data_dir / "tariff_codes.csv",
        "amendments_csv": data_dir / "amendments_truth.csv",
        "errors_csv": data_dir / "injected_errors_truth.csv",
    }
    _write_filed_csv(filed_entries, paths["filed_csv"])
    _write_filed_csv([(doc, doc.items, doc.invoice_total) for doc in docs], paths["gold_csv"])
    _write_tariff_csv(paths["tariff_csv"])
    _write_amendments_csv(amendment_rows, paths["amendments_csv"])

    injected_error_rows: list[dict] = []
    error_counts: dict[str, int] = {}
    for model in models:
        rates = profile_rates(model)
        # A separate deterministic stream per model: the models' mistakes
        # must be independent of each other, not copies.
        model_rng = random.Random(f"{seed}/{model}")
        entries = []
        n_errors = 0
        for doc in docs:
            header, header_conf, items, error_rows = _corrupt_for_extraction(
                doc, model_rng, rates
            )
            entries.append((doc, header, header_conf, items))
            n_errors += len(error_rows)
            injected_error_rows.extend({"model": model} | row for row in error_rows)
        path = data_dir / f"extracted_{model}.csv"
        _write_acme_csv(entries, path)
        paths[f"extracted_{model}"] = path
        error_counts[model] = n_errors
    _write_injected_errors_csv(injected_error_rows, paths["errors_csv"])

    return {
        "n_docs": n_docs,
        "n_items": sum(len(doc.items) for doc in docs),
        "seed": seed,
        "models": list(models),
        "n_amended_docs": len({row["doc_id"] for row in amendment_rows}),
        "errors_per_model": error_counts,
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
        f"Generated {summary['n_docs']} documents with {summary['n_items']} goods items "
        f"({summary['n_acme']} Acme CSV, {summary['n_globex']} Globex XML), "
        f"{summary['n_amended_docs']} legitimately amended after filing."
    )
    print(
        f"Injected {summary['n_injected_errors']} errors: {summary['n_noise_errors']} noise, "
        f"{summary['n_fabrications']} fabrications, {summary['n_structural_errors']} structural."
    )
    for name, path in summary["paths"].items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
