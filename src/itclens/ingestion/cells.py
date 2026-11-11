"""Conversion of single spreadsheet cells. Helpers return None on bad input."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TypeGuard

from itclens.domain.normalize import parse_date, parse_money, plain_number_text

EXCEL_EPOCH = date(1899, 12, 30)
EXCEL_SERIAL_MIN = 36526  # 2000-01-01
EXCEL_SERIAL_MAX = 73415  # 2100-12-31


def is_number(value: object) -> TypeGuard[int | float | Decimal]:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def excel_serial_to_date(value: object) -> date | None:
    """Convert an Excel serial day number in the supported range; the time part is dropped."""
    if not is_number(value) or (isinstance(value, Decimal) and not value.is_finite()):
        return None
    if not EXCEL_SERIAL_MIN <= value <= EXCEL_SERIAL_MAX:
        return None
    return EXCEL_EPOCH + timedelta(days=int(value))


def cell_date(value: object, formats: Sequence[str]) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if is_number(value):
        return excel_serial_to_date(value)
    if isinstance(value, str):
        return parse_date(value, formats)
    return None


def cell_text(value: object) -> str | None:
    """Text of a cell with surrounding whitespace removed; integral numbers have no ".0"."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    elif is_number(value):
        text = plain_number_text(value)
    else:
        text = str(value)
    return text.strip() or None


def parse_rate(value: object) -> Decimal | None:
    """Parse a tax rate. A trailing "%" is allowed on text; numbers are used as they are."""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1]
        return parse_money(text)
    return parse_money(value)
