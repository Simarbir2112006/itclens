"""Read an uploaded CSV or XLSX file from bytes into a plain table of cell values.

The file type is detected from the content, never from the file name. Nothing is
written to disk.
"""

import csv
import io
import warnings
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import openpyxl

from itclens.ingestion.errors import (
    COLUMN_LIMIT,
    EMPTY_FILE,
    FILE_TOO_LARGE,
    NOT_A_TABLE,
    ROW_LIMIT,
    SHEET_NOT_FOUND,
    UNREADABLE_FILE,
    UNSUPPORTED_XLS,
    IngestError,
)

XLSX_SIGNATURE = b"PK\x03\x04"
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
CSV_DELIMITERS = (",", ";", "\t", "|")
DELIMITER_SAMPLE_LINES = 50


@dataclass(frozen=True)
class ReaderLimits:
    """Upper bounds for an uploaded file.

    Placeholder values, to be tuned in the performance phase.
    """

    max_bytes: int = 15_000_000
    max_rows: int = 100_000
    max_cols: int = 100
    max_uncompressed_bytes: int = 150_000_000


DEFAULT_LIMITS = ReaderLimits()


@dataclass(frozen=True, slots=True)
class RawTable:
    """Cell values of one sheet or CSV file.

    Row index 0 is source row 1. All rows have the same width, padded with None.
    Empty or whitespace-only strings are None; other values are kept as read, so
    CSV cells are always strings and XLSX cells keep their types.
    """

    rows: tuple[tuple[object, ...], ...]
    kind: Literal["csv", "xlsx"]
    sheet_name: str | None
    sheet_names: tuple[str, ...]

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0


def _clean(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class _RowCollector:
    """Collects rows while enforcing limits.

    Trailing empty cells and trailing blank rows are dropped, so limits apply to
    the area that actually holds data and not to reported sheet dimensions.
    """

    def __init__(self, limits: ReaderLimits) -> None:
        self._limits = limits
        self._rows: list[tuple[object, ...]] = []
        self._pending_blank = 0
        self._width = 0

    def add(self, values: Iterable[object]) -> None:
        cells = [_clean(v) for v in values]
        while cells and cells[-1] is None:
            cells.pop()
        if not cells:
            self._pending_blank += 1
            return
        if len(cells) > self._limits.max_cols:
            raise IngestError(COLUMN_LIMIT)
        if len(self._rows) + self._pending_blank >= self._limits.max_rows:
            raise IngestError(ROW_LIMIT)
        self._rows.extend([()] * self._pending_blank)
        self._pending_blank = 0
        self._rows.append(tuple(cells))
        self._width = max(self._width, len(cells))

    def has_rows(self) -> bool:
        return bool(self._rows)

    def rows(self) -> tuple[tuple[object, ...], ...]:
        width = self._width
        return tuple(row + (None,) * (width - len(row)) for row in self._rows)


def read_table(
    data: bytes, *, sheet: str | None = None, limits: ReaderLimits = DEFAULT_LIMITS
) -> RawTable:
    """Read XLSX or CSV bytes. The sheet argument is ignored for CSV input."""
    if len(data) > limits.max_bytes:
        raise IngestError(FILE_TOO_LARGE)
    if data.startswith(XLSX_SIGNATURE):
        return _read_xlsx(data, sheet, limits)
    if data.startswith(OLE_SIGNATURE):
        raise IngestError(UNSUPPORTED_XLS)
    return _read_csv(data, limits)


def _read_xlsx(data: bytes, sheet: str | None, limits: ReaderLimits) -> RawTable:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            uncompressed = sum(info.file_size for info in archive.infolist())
    except Exception:
        raise IngestError(UNREADABLE_FILE) from None
    if uncompressed > limits.max_uncompressed_bytes:
        raise IngestError(FILE_TOO_LARGE)

    try:
        # openpyxl warnings can quote parts of the workbook, so they are silenced.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                return _read_workbook(workbook, sheet, limits)
            finally:
                workbook.close()
    except IngestError:
        raise
    except Exception:
        raise IngestError(UNREADABLE_FILE) from None


def _read_workbook(workbook: Any, sheet: str | None, limits: ReaderLimits) -> RawTable:
    sheet_names = tuple(workbook.sheetnames)
    worksheets = list(workbook.worksheets)
    if sheet is not None:
        worksheets = [ws for ws in worksheets if ws.title == sheet]
        if not worksheets:
            raise IngestError(SHEET_NOT_FOUND)

    for worksheet in worksheets:
        worksheet.reset_dimensions()
        collector = _RowCollector(limits)
        for values in worksheet.iter_rows(values_only=True):
            collector.add(values)
        if collector.has_rows():
            return RawTable(collector.rows(), "xlsx", worksheet.title, sheet_names)
    raise IngestError(EMPTY_FILE)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if data[:2] in UTF16_BOMS:
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            raise IngestError(UNREADABLE_FILE) from None
    return data.decode("cp1252", errors="replace")


def _field_count(line: str, delimiter: str) -> int | None:
    try:
        return len(next(csv.reader([line], delimiter=delimiter), []))
    except csv.Error:
        return None


def detect_delimiter(text: str) -> str:
    """Pick the CSV delimiter from a sample of non-empty lines.

    For each candidate the most common field count is found; it must be at least 2.
    The candidate whose most common count appears on the most lines wins, with ties
    going to the earlier entry in CSV_DELIMITERS. Comma is the fallback.
    """
    sample: list[str] = []
    for line in io.StringIO(text, newline=None):
        if line.strip():
            sample.append(line.rstrip("\n"))
            if len(sample) == DELIMITER_SAMPLE_LINES:
                break

    best, best_lines = CSV_DELIMITERS[0], 0
    for delimiter in CSV_DELIMITERS:
        counts = Counter(
            count
            for count in (_field_count(line, delimiter) for line in sample)
            if count is not None
        )
        if not counts:
            continue
        mode, lines = max(counts.items(), key=lambda item: (item[1], item[0]))
        if mode >= 2 and lines > best_lines:
            best, best_lines = delimiter, lines
    return best


def _read_csv(data: bytes, limits: ReaderLimits) -> RawTable:
    text = _decode(data)
    if "\x00" in text:
        raise IngestError(NOT_A_TABLE)

    collector = _RowCollector(limits)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=detect_delimiter(text))
    try:
        for record in reader:
            collector.add(record)
    except csv.Error:
        raise IngestError(UNREADABLE_FILE) from None
    if not collector.has_rows():
        raise IngestError(EMPTY_FILE)
    return RawTable(collector.rows(), "csv", None, ())
