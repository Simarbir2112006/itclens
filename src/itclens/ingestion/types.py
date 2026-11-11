"""Result types shared by the register and GSTR-2B parsers."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from itclens.domain.models import Document


class FieldFlagKind(Enum):
    MISSING = "missing"
    UNPARSEABLE = "unparseable"


@dataclass(frozen=True, slots=True)
class FieldFlag:
    field: str
    kind: FieldFlagKind


@dataclass(frozen=True, slots=True)
class ParsedRow:
    document: Document
    flags: tuple[FieldFlag, ...]


@dataclass(frozen=True, slots=True)
class SkippedRow:
    row_ref: str
    reason: Literal["BLANK", "TOTAL"]


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Parsed and skipped rows. input_rows counts source rows after the header row."""

    rows: tuple[ParsedRow, ...]
    skipped: tuple[SkippedRow, ...]
    input_rows: int

    def conserved(self) -> bool:
        return self.input_rows == len(self.rows) + len(self.skipped)
