"""GSTIN normalization and validation.

Known limitation: only the regular-taxpayer GSTIN structure is supported. Other
registration types (for example UIN holders or TDS/TCS registrants) may use
different structures and are not handled yet.
"""

import re
from dataclasses import dataclass
from enum import Enum

BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DEFAULT_CHAR = "Z"
GSTIN_LENGTH = 15

# state code, PAN (5 letters, 4 digits, 1 letter), entity char, literal Z, check char
GSTIN_PATTERN = re.compile(rf"^[0-9]{{2}}[A-Z]{{5}}[0-9]{{4}}[A-Z][1-9A-Z]{DEFAULT_CHAR}[0-9A-Z]$")

_CHAR_VALUES = {c: i for i, c in enumerate(BASE36_ALPHABET)}


class GstinFailure(Enum):
    EMPTY = "empty"
    LENGTH = "length"
    STRUCTURE = "structure"
    STATE_CODE = "state_code"
    CHECKSUM = "checksum"


@dataclass(frozen=True)
class GstinResult:
    normalized: str
    is_valid: bool
    failure: GstinFailure | None
    state_code: str | None
    pan: str | None


def normalize_gstin(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def compute_check_char(first14: str) -> str:
    if not isinstance(first14, str) or len(first14) != GSTIN_LENGTH - 1:
        raise ValueError("GSTIN body must be 14 characters long")
    total = 0
    for i, char in enumerate(first14):
        value = _CHAR_VALUES.get(char)
        if value is None:
            raise ValueError("GSTIN body contains a character outside 0-9 and A-Z")
        product = value * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return BASE36_ALPHABET[(36 - total % 36) % 36]


def with_check_char(first14: str) -> str:
    return first14 + compute_check_char(first14)


def state_code_of(g: str) -> str:
    return g[0:2]


def pan_of(g: str) -> str:
    return g[2:12]


def validate_gstin(value: object, valid_state_codes: frozenset[str] | None = None) -> GstinResult:
    """Check a GSTIN in this order: empty, length, structure, state code, checksum.

    The state code check runs only when valid_state_codes is given. state_code and
    pan are filled whenever the structure check passes.
    """
    g = normalize_gstin(value)
    if not g:
        return GstinResult(g, False, GstinFailure.EMPTY, None, None)
    if len(g) != GSTIN_LENGTH:
        return GstinResult(g, False, GstinFailure.LENGTH, None, None)
    if not GSTIN_PATTERN.match(g):
        return GstinResult(g, False, GstinFailure.STRUCTURE, None, None)

    state_code = state_code_of(g)
    pan = pan_of(g)
    if valid_state_codes is not None and state_code not in valid_state_codes:
        return GstinResult(g, False, GstinFailure.STATE_CODE, state_code, pan)
    if compute_check_char(g[:-1]) != g[-1]:
        return GstinResult(g, False, GstinFailure.CHECKSUM, state_code, pan)
    return GstinResult(g, True, None, state_code, pan)
