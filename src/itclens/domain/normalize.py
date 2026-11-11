"""Normalization helpers for invoice numbers, money and dates.

Parsing helpers return None on bad input instead of raising.
"""

import math
import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_ABBREVIATIONS = tuple(name[:3] for name in MONTH_NAMES)

_ABBR_TO_MONTH = {name.upper(): i for i, name in enumerate(MONTH_ABBREVIATIONS, start=1)}
_FULL_TO_MONTH = {name.upper(): i for i, name in enumerate(MONTH_NAMES, start=1)}

# Year pair such as 2024-25, 2024/2025 or 24-25.
_FY = r"(?P<y1>\d{4}|\d{2})[-/](?P<y2>\d{4}|\d{2})"
_FY_SUFFIX = re.compile(rf"^(?P<rest>.*)[-/_ ]{_FY}$")
_FY_PREFIX = re.compile(rf"^{_FY}[-/_ ](?P<rest>.*)$")
_NON_ALNUM = re.compile(r"[^0-9A-Z]")
_ALNUM = re.compile(r"[0-9A-Z]")


def _is_fy_pair(y1: str, y2: str) -> bool:
    if len(y1) == 4 and len(y2) == 4:
        return int(y2) == int(y1) + 1
    if len(y2) == 2:
        return int(y2) == (int(y1) + 1) % 100
    return False


def _strip_fy_affix(text: str) -> str:
    for pattern in (_FY_SUFFIX, _FY_PREFIX):
        m = pattern.match(text)
        if m and _is_fy_pair(m["y1"], m["y2"]) and _ALNUM.search(m["rest"]):
            return m["rest"]
    return text


def normalize_invoice_no(raw: object, *, strip_fy_affixes: bool = False) -> str:
    """Normalize an invoice number for matching.

    Rules, in order:
    1. None becomes "", other non-strings go through str(); surrounding whitespace
       is removed and the text is uppercased.
    2. If strip_fy_affixes is set, one financial-year token at the very end, or
       else at the very start, is removed together with its single separator
       (/ - _ or space). A token is YYYY-YY, YYYY-YYYY or YY-YY (inner separator
       - or /) where the second year is the first plus one. Nothing is stripped
       if no letter or digit would remain.
    3. Everything except ASCII letters and digits is removed.
    4. Leading zeros of the whole string are removed; an all-zero string gives "0".

    Examples:
        "inv/0042"                               -> "INV0042"
        "000123"                                 -> "123"
        "INV-001/2024-25" with strip_fy_affixes  -> "INV001"

    The result has no separators left, so a second call can never find a year
    token again. The function is therefore idempotent for both option values.
    """
    if raw is None:
        return ""
    text = (raw if isinstance(raw, str) else str(raw)).strip().upper()
    if strip_fy_affixes:
        text = _strip_fy_affix(text)
    text = _NON_ALNUM.sub("", text)
    stripped = text.lstrip("0")
    if text and not stripped:
        return "0"
    return stripped


_CURRENCY_PREFIX = re.compile(r"^(?:₹|RS\.?|INR)\s*", re.IGNORECASE)
_PLAIN_NUMBER = re.compile(r"^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")
_WESTERN_GROUPS = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})+$")
_INDIAN_GROUPS = re.compile(r"^[0-9]{1,2}(?:,[0-9]{2})*,[0-9]{3}$")


def _take_sign(text: str) -> tuple[str, str]:
    if text[:1] in ("-", "+"):
        return text[0], text[1:].lstrip()
    return "", text


def _remove_grouping(number: str) -> str | None:
    integer, dot, fraction = number.partition(".")
    if "," in integer:
        if not (_WESTERN_GROUPS.match(integer) or _INDIAN_GROUPS.match(integer)):
            return None
        integer = integer.replace(",", "")
    return integer + dot + fraction


def _parse_money_text(text: str) -> Decimal | None:
    text = text.strip()
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    sign, text = _take_sign(text)
    text, prefix_count = _CURRENCY_PREFIX.subn("", text)
    if prefix_count and not sign:
        sign, text = _take_sign(text)
    if negative and sign:
        return None

    number = _remove_grouping(text)
    if number is None or not _PLAIN_NUMBER.match(number):
        return None
    amount = Decimal(number)
    return -amount if negative or sign == "-" else amount


def parse_money(value: object) -> Decimal | None:
    """Parse an amount without rounding.

    Accepts int, Decimal, float (converted through repr, so 0.1 gives
    Decimal("0.1")) and strings such as "1,23,456.78", "Rs. 500", "(250)" or
    "-₹10". Booleans, NaN, infinity and unrecognised text give None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value)) if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, str):
        return _parse_money_text(value)
    return None


def round_money(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def within_tolerance(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(round_money(a) - round_money(b)) <= tolerance


_DIRECTIVE = re.compile(r"%(.)")
# Directives whose meaning depends on the process locale.
_LOCALE_DIRECTIVES = frozenset("aApcxX")
_LETTERS = re.compile(r"[A-Za-z]+")


def _month_name_to_number(text: str, fmt: str) -> tuple[str, str] | None:
    """Replace a textual month in text with its number and %b/%B in fmt with %m."""
    directives = [m.group(1) for m in _DIRECTIVE.finditer(fmt)]
    if any(d in _LOCALE_DIRECTIVES for d in directives):
        return None
    month_directives = [d for d in directives if d in ("b", "B")]
    if not month_directives:
        return text, fmt
    if len(month_directives) > 1:
        return None

    words = _LETTERS.findall(text)
    if len(words) != 1:
        return None
    table = _ABBR_TO_MONTH if month_directives[0] == "b" else _FULL_TO_MONTH
    month = table.get(words[0].upper())
    if month is None:
        return None
    new_fmt = _DIRECTIVE.sub(lambda m: "%m" if m.group(1) in "bB" else m.group(0), fmt)
    return _LETTERS.sub(f"{month:02d}", text), new_fmt


def parse_date(value: object, formats: Sequence[str]) -> date | None:
    """Parse a date using the first matching format.

    date and datetime objects are accepted as they are. %b and %B are read with a
    fixed English month table (abbreviations such as "Jan" and full names such as
    "January", any case), so the result does not depend on the process locale.
    Formats that use other locale-dependent directives (%a, %A, %p, %c, %x, %X)
    are skipped. Excel serial numbers are not handled here.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in formats:
        prepared = _month_name_to_number(text, fmt)
        if prepared is None:
            continue
        try:
            return datetime.strptime(prepared[0], prepared[1]).date()
        except ValueError:
            continue
    return None
