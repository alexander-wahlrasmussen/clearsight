"""Static reference data shared by comparators and validity rules.

Deliberately plain data, no logic.  If a code list needs to grow (e.g. the
currency list is a common subset, not the full ISO 4217 registry), extend
it here and nothing else changes.
"""

from __future__ import annotations

# ISO 3166-1 alpha-2 country codes (the full official list).
ISO_COUNTRIES: frozenset[str] = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
    BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
    DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
    HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP
    KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY
    MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
    NA NC NE NF NG NI NL NO NP NR NU NZ OM
    PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
    TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
    UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    """.split()
)

# A handful of spelled-out names that show up in real extraction output.
# This is normalisation, not fuzzy matching: only exact (case-insensitive)
# names listed here are mapped.  Anything else that is not a valid alpha-2
# code is treated as not comparable rather than guessed at.
COUNTRY_ALIASES: dict[str, str] = {
    "GERMANY": "DE",
    "FRANCE": "FR",
    "NETHERLANDS": "NL",
    "THE NETHERLANDS": "NL",
    "SPAIN": "ES",
    "POLAND": "PL",
    "TURKEY": "TR",
    "TURKIYE": "TR",
    "CHINA": "CN",
    "PEOPLES REPUBLIC OF CHINA": "CN",
    "UNITED STATES": "US",
    "USA": "US",
    "UNITED KINGDOM": "GB",
    "GREAT BRITAIN": "GB",
    "UK": "GB",
    "INDIA": "IN",
    "VIETNAM": "VN",
    "VIET NAM": "VN",
    "JAPAN": "JP",
    "SOUTH KOREA": "KR",
    "REPUBLIC OF KOREA": "KR",
    "TAIWAN": "TW",
    "THAILAND": "TH",
    "MALAYSIA": "MY",
    "MEXICO": "MX",
}

# National EORI number lengths: digits after the two-letter country prefix.
# These lengths are real (DE issues 15-digit numbers, NL uses the 9-digit
# RSIN); the Luhn check digit our validity rule expects at the end is NOT --
# see validity.eori_checksum.  A prefix missing from this map means we do
# not know the national format and the rule fails rather than guesses.
EORI_FORMATS: dict[str, int] = {
    "DE": 15,
    "NL": 9,
}

# Expected supplementary quantity unit by HS chapter (first two digits).
# Real declarations owe customs a quantity in the unit the nomenclature
# prescribes -- footwear in pairs, beverages in litres, most bulk goods as
# net mass.  This map is a plausible working subset of that idea, not the
# real (per-code, per-year) unit table.
CHAPTER_QUANTITY_UNITS: dict[str, str] = {
    "09": "KGM", "19": "KGM", "21": "KGM",   # coffee, baked goods, food preps
    "22": "LTR",                              # beverages
    "33": "KGM", "39": "KGM",                 # cosmetics, plastics
    "42": "NAR",                              # bags and cases
    "48": "KGM", "49": "KGM",                 # paper, printed matter
    "61": "NAR", "62": "NAR", "63": "NAR",    # apparel and textiles
    "64": "NPR",                              # footwear: pairs
    "73": "KGM",                              # articles of iron/steel
    "83": "NAR", "84": "NAR", "85": "NAR",    # metal fittings, machinery, electronics
    "87": "NAR", "90": "NAR",                 # vehicles/parts, instruments
    "94": "NAR", "95": "NAR", "96": "NAR",    # furniture, toys, misc
}
DEFAULT_QUANTITY_UNIT = "KGM"  # weight-based when the chapter is not listed
VALID_QUANTITY_UNITS: frozenset[str] = frozenset({"KGM", "NAR", "NPR", "LTR"})


def expected_quantity_unit(hs_code: str | None) -> str | None:
    """The supplementary unit the HS chapter prescribes, or None when the
    code is unusable (no chapter to look up)."""
    if not hs_code:
        return None
    chapter = str(hs_code).strip()[:2]
    if not chapter.isdigit():
        return None
    return CHAPTER_QUANTITY_UNITS.get(chapter, DEFAULT_QUANTITY_UNIT)


# Common ISO 4217 currency codes.  A working subset, not the full registry;
# a currency missing from this list fails the validity rule, which is the
# honest behaviour for a prototype (better a false alarm a human can waive
# than a silent pass).
ISO_CURRENCIES: frozenset[str] = frozenset(
    """
    AED ARS AUD BGN BRL CAD CHF CLP CNY COP CZK DKK EGP EUR GBP HKD HUF
    IDR ILS INR ISK JPY KES KRW MAD MXN MYR NGN NOK NZD PHP PLN QAR RON
    RSD SAR SEK SGD THB TRY TWD UAH USD VND ZAR
    """.split()
)
