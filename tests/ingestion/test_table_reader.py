import io
import zipfile
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

from itclens.ingestion import errors
from itclens.ingestion.errors import IngestError
from itclens.ingestion.table_reader import ReaderLimits, detect_delimiter, read_table

XlsxBuilder = Callable[[dict[str, list[list[object]]]], bytes]
CsvBuilder = Callable[..., bytes]


def code_of(data: bytes, **kwargs: Any) -> str:
    with pytest.raises(IngestError) as excinfo:
        read_table(data, **kwargs)
    return excinfo.value.code


def test_xlsx_basic(xlsx_bytes: XlsxBuilder) -> None:
    when = datetime(2024, 4, 15)
    table = read_table(xlsx_bytes({"Data": [["a", "b", "c"], [1, 2.5, when]]}))
    assert table.kind == "xlsx"
    assert table.sheet_name == "Data"
    assert table.sheet_names == ("Data",)
    assert table.rows == (("a", "b", "c"), (1, 2.5, when))
    assert table.width == 3


def test_csv_basic(csv_bytes: CsvBuilder) -> None:
    table = read_table(csv_bytes([["a", "b"], ["1", " 2.5 "]]))
    assert table.kind == "csv"
    assert table.sheet_name is None
    assert table.sheet_names == ()
    assert table.rows == (("a", "b"), ("1", " 2.5 "))


def test_title_rows_blank_rows_and_padding(xlsx_bytes: XlsxBuilder) -> None:
    rows: list[list[object]] = [["Purchase Register"], [], ["A", "B", "C"], [1, None, 3], ["x"]]
    table = read_table(xlsx_bytes({"S": rows}))
    assert table.rows == (
        ("Purchase Register", None, None),
        (None, None, None),
        ("A", "B", "C"),
        (1, None, 3),
        ("x", None, None),
    )


def test_whitespace_cells_become_none(csv_bytes: CsvBuilder) -> None:
    table = read_table(csv_bytes([["a", "  ", "b"], ["", "\t", " c "]]))
    assert table.rows == (("a", None, "b"), (None, None, " c "))


def test_trailing_blank_rows_and_cells_dropped(csv_bytes: CsvBuilder) -> None:
    table = read_table(csv_bytes([["a", "b", "", ""], ["1", "2", "", ""], ["", "", "", ""], []]))
    assert table.rows == (("a", "b"), ("1", "2"))


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_csv_delimiters(csv_bytes: CsvBuilder, delimiter: str) -> None:
    rows = [["Purchase Register"], ["GSTIN", "Name", "Amount"], ["X", "A, B; C", "1,000.00"]]
    table = read_table(csv_bytes(rows, delimiter=delimiter))
    assert table.rows[1] == ("GSTIN", "Name", "Amount")
    assert table.rows[2] == ("X", "A, B; C", "1,000.00")


def test_delimiter_tie_prefers_listed_order() -> None:
    assert detect_delimiter("a,b;c\nd,e;f\n") == ","
    assert detect_delimiter("a;b|c\nd;e|f\n") == ";"


def test_delimiter_fallback_is_comma() -> None:
    assert detect_delimiter("single\ncolumn\n") == ","


def test_quoted_newline_is_one_record(csv_bytes: CsvBuilder) -> None:
    table = read_table(csv_bytes([["a", "b"], ["line1\nline2", "x"], ["c", "d"]]))
    assert table.rows == (("a", "b"), ("line1\nline2", "x"), ("c", "d"))


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-8"])
def test_unicode_encodings(csv_bytes: CsvBuilder, encoding: str) -> None:
    table = read_table(csv_bytes([["Name", "Amount"], ["Kāraṇ ₹", "5"]], encoding=encoding))
    assert table.rows == (("Name", "Amount"), ("Kāraṇ ₹", "5"))


def test_cp1252_fallback(csv_bytes: CsvBuilder) -> None:
    table = read_table(csv_bytes([["Name", "Amount"], ["Café €", "5"]], encoding="cp1252"))
    assert table.rows[1] == ("Café €", "5")


def test_xls_signature_rejected() -> None:
    assert code_of(bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 100) == errors.UNSUPPORTED_XLS


def test_binary_garbage_rejected() -> None:
    assert code_of(bytes(range(256)) * 4) == errors.NOT_A_TABLE
    assert code_of(b"PK\x03\x04" + bytes(range(256))) == errors.UNREADABLE_FILE


def test_truncated_xlsx(xlsx_bytes: XlsxBuilder) -> None:
    data = xlsx_bytes({"S": [["a", "b"], [1, 2]]})
    assert code_of(data[: len(data) // 2]) == errors.UNREADABLE_FILE


def test_zip_that_is_not_a_workbook() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "a,b\n1,2\n")
    assert code_of(buffer.getvalue()) == errors.UNREADABLE_FILE


def test_bad_utf16() -> None:
    assert code_of(b"\xff\xfea\x00b") == errors.UNREADABLE_FILE


def test_oversized_csv_field() -> None:
    assert code_of(b'a,b\n"' + b"x" * 200_000 + b'",1\n') == errors.UNREADABLE_FILE


@pytest.mark.parametrize("data", [b"", b"\r\n\r\n   \n", b",,,\n,,\n"])
def test_empty_csv(data: bytes) -> None:
    assert code_of(data) == errors.EMPTY_FILE


def test_empty_workbook(xlsx_bytes: XlsxBuilder) -> None:
    assert code_of(xlsx_bytes({"A": [], "B": [["  "]]})) == errors.EMPTY_FILE


def test_row_limit(csv_bytes: CsvBuilder, xlsx_bytes: XlsxBuilder) -> None:
    limits = ReaderLimits(max_rows=3)
    rows: list[list[object]] = [["a"], ["b"], ["c"]]
    assert len(read_table(csv_bytes(rows + [[""]] * 5), limits=limits).rows) == 3
    assert code_of(csv_bytes(rows + [["d"]]), limits=limits) == errors.ROW_LIMIT
    assert code_of(xlsx_bytes({"S": rows + [["d"]]}), limits=limits) == errors.ROW_LIMIT
    assert code_of(xlsx_bytes({"S": [["a"], [], [], ["d"]]}), limits=limits) == errors.ROW_LIMIT


def test_column_limit(csv_bytes: CsvBuilder, xlsx_bytes: XlsxBuilder) -> None:
    limits = ReaderLimits(max_cols=3)
    assert read_table(csv_bytes([["a", "b", "c", "", ""]]), limits=limits).width == 3
    assert code_of(csv_bytes([["a", "b", "c", "d"]]), limits=limits) == errors.COLUMN_LIMIT
    assert code_of(xlsx_bytes({"S": [["a", None, None, "d"]]}), limits=limits) == (
        errors.COLUMN_LIMIT
    )


def test_size_limit(csv_bytes: CsvBuilder, xlsx_bytes: XlsxBuilder) -> None:
    limits = ReaderLimits(max_bytes=10)
    assert code_of(csv_bytes([["abcdef", "ghijkl"]]), limits=limits) == errors.FILE_TOO_LARGE
    assert code_of(xlsx_bytes({"S": [["a"]]}), limits=limits) == errors.FILE_TOO_LARGE


def test_uncompressed_size_limit(xlsx_bytes: XlsxBuilder) -> None:
    data = xlsx_bytes({"S": [["a"]]})
    limits = ReaderLimits(max_uncompressed_bytes=100)
    assert code_of(data, limits=limits) == errors.FILE_TOO_LARGE


def test_sheet_selection(xlsx_bytes: XlsxBuilder) -> None:
    data = xlsx_bytes({"First": [["one"]], "Second": [["two"]], "Blank": []})
    assert read_table(data).sheet_name == "First"
    table = read_table(data, sheet="Second")
    assert table.rows == (("two",),)
    assert table.sheet_names == ("First", "Second", "Blank")
    assert code_of(data, sheet="Missing") == errors.SHEET_NOT_FOUND
    assert code_of(data, sheet="Blank") == errors.EMPTY_FILE


def test_first_non_empty_sheet(xlsx_bytes: XlsxBuilder) -> None:
    data = xlsx_bytes({"Cover": [], "Notes": [[None, "   "]], "Data": [["x", 1]]})
    table = read_table(data)
    assert table.sheet_name == "Data"
    assert table.rows == (("x", 1),)


def test_sheet_argument_ignored_for_csv(csv_bytes: CsvBuilder) -> None:
    assert read_table(csv_bytes([["a", "b"]]), sheet="Anything").rows == (("a", "b"),)
