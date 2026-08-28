"""Pair extracted goods items with filed goods items.

This is the hardest part of comparing multi-item declarations, and it is
kept in its own module so the policy is visible and testable.  Item
numbers cannot be trusted as a join key: extraction tools invent their own
ordering, extraction can merge two document lines into one or drop a line
entirely, and customs can renumber.  So items are paired by how similar
their content is, greedily, best pair first.

The scoring is deliberately boring and inspectable (no ML):

    0.5 * HS code agreement    -- deepest shared prefix, using the real
                                  nomenclature hierarchy: 2 digits =
                                  chapter, 4 = heading, 6 = subheading,
                                  8 = CN code, 10 = TARIC code
    0.3 * value closeness      -- 1 - relative difference, floored at 0
    0.1 * origin agreement
    0.1 * quantity closeness

Pairs below `min_score` (configured in field_tiers.yaml) are refused:
better to report two unmatched items than to invent a nonsense pairing.
Every accepted pair carries a human-readable `basis` string that ends up
in the report, so a suspicious pairing can be audited like any other
number.

Like comparators, the aligner is versioned: pairing policy changes move
metrics, and a metric shift must be attributable.

Known limitations (also in the README): greedy matching is not globally
optimal; a merged line whose totals happen to match a single filed item
pairs cleanly and the drop shows up only as the OTHER filed item going
unmatched; and when two items of the same chapter carry similar values,
the aligner can legitimately pick the wrong partner -- the basis string is
there so a human can spot it.
"""

from __future__ import annotations

from dataclasses import dataclass

from canonical import CanonicalItem
from comparators import parse_number

ALIGNER_NAME = "greedy_item_aligner"
ALIGNER_VERSION = "1.0"

# Score for the deepest shared HS prefix, keyed by digits of agreement.
# Only the hierarchy's real boundaries count (chapter/heading/subheading/
# CN/TARIC); 7 shared digits are worth no more than 6.
_HS_DEPTH_SCORE = {10: 1.0, 8: 0.9, 6: 0.75, 4: 0.5, 2: 0.25, 0: 0.0}

_WEIGHT_HS = 0.5
_WEIGHT_VALUE = 0.3
_WEIGHT_ORIGIN = 0.1
_WEIGHT_QUANTITY = 0.1


@dataclass(frozen=True)
class AlignedPair:
    extracted: CanonicalItem
    filed: CanonicalItem
    score: float
    basis: str  # plain-language justification, shown in drill-downs


@dataclass
class ItemAlignment:
    pairs: list[AlignedPair]
    unmatched_extracted: list[CanonicalItem]  # extracted, no filed partner (spurious?)
    unmatched_filed: list[CanonicalItem]      # filed, no extracted partner (missed?)


def hs_prefix_agreement(a, b) -> int:
    """Digits of shared HS prefix, floored to the nearest real nomenclature
    boundary (0, 2, 4, 6, 8, 10).  Missing/blank codes agree to 0 digits."""
    code_a = _clean(a)
    code_b = _clean(b)
    if not code_a or not code_b:
        return 0
    shared = 0
    for char_a, char_b in zip(code_a[:10], code_b[:10]):
        if char_a != char_b:
            break
        shared += 1
    return shared - (shared % 2)


def _clean(code) -> str:
    return "" if code is None else str(code).replace(".", "").replace(" ", "").strip()


def _closeness(a, b) -> float | None:
    """1.0 for equal numbers, falling to 0.0 at a 100% relative difference.
    None when either side is missing/unparseable (treated as neutral)."""
    number_a, number_b = parse_number(a), parse_number(b)
    if number_a is None or number_b is None:
        return None
    largest = max(abs(number_a), abs(number_b))
    if largest == 0:
        return 1.0
    return 1.0 - min(1.0, abs(number_a - number_b) / largest)


def _pair_score(extracted: CanonicalItem, filed: CanonicalItem) -> tuple[float, str]:
    depth = hs_prefix_agreement(extracted.hs_code, filed.hs_code)
    hs_score = _HS_DEPTH_SCORE[depth]

    value_closeness = _closeness(extracted.item_value, filed.item_value)
    quantity_closeness = _closeness(extracted.quantity, filed.quantity)

    if extracted.origin_country and filed.origin_country:
        origin_score = 1.0 if extracted.origin_country.strip().upper() == filed.origin_country.strip().upper() else 0.0
        origin_text = "origin agrees" if origin_score else "origin differs"
    else:
        origin_score = 0.5  # unknown: neither evidence for nor against
        origin_text = "origin unknown on one side"

    score = (
        _WEIGHT_HS * hs_score
        + _WEIGHT_VALUE * (0.5 if value_closeness is None else value_closeness)
        + _WEIGHT_ORIGIN * origin_score
        + _WEIGHT_QUANTITY * (0.5 if quantity_closeness is None else quantity_closeness)
    )
    basis_parts = [f"HS agrees to {depth} digits"]
    if value_closeness is not None:
        basis_parts.append(f"values differ by {(1 - value_closeness) * 100:.1f}%")
    else:
        basis_parts.append("value missing on one side")
    basis_parts.append(origin_text)
    if quantity_closeness is not None:
        basis_parts.append(f"quantities differ by {(1 - quantity_closeness) * 100:.1f}%")
    return score, "; ".join(basis_parts)


def align_items(
    extracted_items: list[CanonicalItem],
    filed_items: list[CanonicalItem],
    min_score: float,
) -> ItemAlignment:
    """Greedy best-pair-first matching.

    All candidate pairs are scored, sorted best first (ties broken by item
    number so runs are deterministic), and accepted while both sides are
    still unclaimed and the score clears `min_score`."""
    candidates = []
    for i, extracted in enumerate(extracted_items):
        for j, filed in enumerate(filed_items):
            score, basis = _pair_score(extracted, filed)
            candidates.append((score, i, j, basis))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    used_extracted: set[int] = set()
    used_filed: set[int] = set()
    pairs: list[AlignedPair] = []
    for score, i, j, basis in candidates:
        if score < min_score:
            break  # sorted descending: nothing acceptable remains
        if i in used_extracted or j in used_filed:
            continue
        used_extracted.add(i)
        used_filed.add(j)
        pairs.append(
            AlignedPair(
                extracted=extracted_items[i],
                filed=filed_items[j],
                score=round(score, 3),
                basis=basis,
            )
        )
    return ItemAlignment(
        pairs=pairs,
        unmatched_extracted=[
            item for i, item in enumerate(extracted_items) if i not in used_extracted
        ],
        unmatched_filed=[
            item for j, item in enumerate(filed_items) if j not in used_filed
        ],
    )


def describe_item(item: CanonicalItem) -> str:
    """One-line item summary for reasons and drill-down cells."""
    quantity = "?" if item.quantity is None else f"{item.quantity:g}"
    unit = item.quantity_unit or "?"
    value = "?" if item.item_value is None else f"{item.item_value:,.2f}"
    return (
        f"item {item.item_number}: HS {item.hs_code or '?'}, "
        f"{quantity} {unit}, value {value}, origin {item.origin_country or '?'}"
    )
