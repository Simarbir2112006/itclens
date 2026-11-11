import logging
from collections.abc import Callable
from pathlib import Path

import pytest

import itclens.ingestion
from itclens.domain.config import ReconConfig
from itclens.domain.gstin import with_check_char
from itclens.ingestion import errors
from itclens.ingestion.columns import apply_overrides, propose_mapping
from itclens.ingestion.errors import IngestError
from itclens.ingestion.register import parse_register
from itclens.ingestion.table_reader import ReaderLimits, read_table

CsvBuilder = Callable[..., bytes]
XlsxBuilder = Callable[[dict[str, list[list[object]]]], bytes]
SENTINEL = "ZZ-SENTINEL-77"
GSTIN = with_check_char("27ABCDE1234F1Z")


def assert_no_leak(action: Callable[[], object], code: str) -> None:
    with pytest.raises(IngestError) as excinfo:
        action()
    error = excinfo.value
    assert error.code == code
    for text in (str(error), repr(error), repr(error.args)):
        assert SENTINEL not in text
    assert error.__cause__ is None


def sentinel_table(csv_bytes: CsvBuilder) -> bytes:
    return csv_bytes(
        [
            [SENTINEL],
            ["GSTIN", "Invoice No", "Invoice Date", "Taxable Value", SENTINEL],
            [GSTIN, SENTINEL, "15-04-2024", "100", SENTINEL],
        ]
    )


def test_reader_errors_do_not_leak(csv_bytes: CsvBuilder, xlsx_bytes: XlsxBuilder) -> None:
    text = SENTINEL.encode()
    workbook = xlsx_bytes({SENTINEL: [[SENTINEL, SENTINEL], [SENTINEL]]})
    cases: list[tuple[Callable[[], object], str]] = [
        (lambda: read_table(text * 10, limits=ReaderLimits(max_bytes=5)), errors.FILE_TOO_LARGE),
        (
            lambda: read_table(workbook, limits=ReaderLimits(max_uncompressed_bytes=10)),
            errors.FILE_TOO_LARGE,
        ),
        (lambda: read_table(bytes.fromhex("D0CF11E0A1B11AE1") + text), errors.UNSUPPORTED_XLS),
        (lambda: read_table(b"PK\x03\x04" + text), errors.UNREADABLE_FILE),
        (lambda: read_table(workbook[: len(workbook) // 2]), errors.UNREADABLE_FILE),
        (lambda: read_table(b"\xff\xfe" + text + b"!"), errors.UNREADABLE_FILE),
        (lambda: read_table(text + b'\n"' + text * 20_000 + b'"'), errors.UNREADABLE_FILE),
        (lambda: read_table(text + b"\x00"), errors.NOT_A_TABLE),
        (lambda: read_table(xlsx_bytes({SENTINEL: []})), errors.EMPTY_FILE),
        (lambda: read_table(workbook, limits=ReaderLimits(max_rows=1)), errors.ROW_LIMIT),
        (lambda: read_table(workbook, limits=ReaderLimits(max_cols=1)), errors.COLUMN_LIMIT),
        (lambda: read_table(workbook, sheet=SENTINEL + "x"), errors.SHEET_NOT_FOUND),
    ]
    for action, code in cases:
        assert_no_leak(action, code)


def test_mapping_errors_do_not_leak(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    table = read_table(sentinel_table(csv_bytes))
    mapping = propose_mapping(table, config)
    no_header = read_table(csv_bytes([[SENTINEL, SENTINEL], [SENTINEL]]))
    incomplete = apply_overrides(mapping, {"doc_no": None})
    cases: list[tuple[Callable[[], object], str]] = [
        (lambda: propose_mapping(no_header, config), errors.HEADER_NOT_FOUND),
        (lambda: apply_overrides(mapping, {SENTINEL: 1}), errors.BAD_MAPPING),
        (lambda: apply_overrides(mapping, {"doc_no": 99}), errors.BAD_MAPPING),
        (lambda: parse_register(table, incomplete, config), errors.MAPPING_INCOMPLETE),
        (lambda: parse_register(no_header, mapping, config), errors.BAD_MAPPING),
    ]
    for action, code in cases:
        assert_no_leak(action, code)


def test_no_log_records(
    caplog: pytest.LogCaptureFixture, xlsx_bytes: XlsxBuilder, config: ReconConfig
) -> None:
    rows: list[list[object]] = [
        [SENTINEL],
        ["GSTIN", "Invoice No", "Invoice Date", "Taxable Value", "Rate"],
        [SENTINEL, SENTINEL, SENTINEL, SENTINEL, SENTINEL],
        [GSTIN, "1", "15-04-2024", "100", "18"],
    ]
    with caplog.at_level(logging.DEBUG):
        table = read_table(xlsx_bytes({SENTINEL: rows}))
        mapping = propose_mapping(table, config)
        result = parse_register(table, mapping, config)
    assert result.conserved()
    assert caplog.records == []


def test_source_has_no_logging_or_file_access() -> None:
    package = Path(itclens.ingestion.__file__).parent
    sources = sorted(package.glob("*.py"))
    assert sources
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "import logging" not in text, path.name
        assert "open(" not in text, path.name
