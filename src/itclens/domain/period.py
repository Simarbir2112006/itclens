"""GST return period (month and year)."""

import re
from dataclasses import dataclass
from datetime import date

from itclens.domain.normalize import MONTH_ABBREVIATIONS

MIN_YEAR = 2000
MAX_YEAR = 2100

_MMYYYY = re.compile(r"^([0-9]{2})([0-9]{4})$")


@dataclass(frozen=True, order=True)
class ReturnPeriod:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not isinstance(self.year, int) or isinstance(self.year, bool):
            raise ValueError("year must be an integer")
        if not isinstance(self.month, int) or isinstance(self.month, bool):
            raise ValueError("month must be an integer")
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")
        if not MIN_YEAR <= self.year <= MAX_YEAR:
            raise ValueError(f"year must be between {MIN_YEAR} and {MAX_YEAR}")

    @classmethod
    def parse(cls, text: object) -> "ReturnPeriod | None":
        """Parse the "MMYYYY" form, for example "042024" for April 2024.

        This is believed to be the format the GST portal uses for return periods,
        but it has not been verified against a real GSTR-2B file yet.
        """
        if not isinstance(text, str):
            return None
        m = _MMYYYY.match(text.strip())
        if not m:
            return None
        month, year = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12 or not MIN_YEAR <= year <= MAX_YEAR:
            return None
        return cls(year=year, month=month)

    @classmethod
    def from_date(cls, d: date) -> "ReturnPeriod":
        return cls(year=d.year, month=d.month)

    def __str__(self) -> str:
        return f"{self.month:02d}{self.year:04d}"

    @property
    def label(self) -> str:
        return f"{MONTH_ABBREVIATIONS[self.month - 1]} {self.year}"
