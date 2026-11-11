from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

import pytest

from itclens.domain.config import ReconConfig, load_config
from itclens.domain.gstin import with_check_char
from itclens.domain.models import DocType, Document, Source, TaxLine
from itclens.ingestion import errors
from itclens.ingestion.columns import ColumnMapping, apply_overrides, propose_mapping
from itclens.ingestion.errors import IngestError
from itclens.ingestion.register import parse_register
from itclens.ingestion.table_reader import read_table
from itclens.ingestion.types import FieldFlag, FieldFlagKind, ParseResult

CsvBuilder = Callable[..., bytes]
XlsxBuilder = Callable[[dict[str, list[list[object]]]], bytes]
D = Decimal
MISSING = FieldFlagKind.MISSING
UNPARSEABLE = FieldFlagKind.UNPARSEABLE
GSTIN_A = with_check_char("27ABCDE1234F1Z")
GSTIN_B = with_check_char("29PQRST5678K2Z")

HEADER = ["GSTIN", "Invoice No", "Invoice Date", "Taxable Value"]


def parse(data: bytes, config: ReconConfig | None = None) -> ParseResult:
    config = config or load_config()
    table = read_table(data)
    return parse_register(table, propose_mapping(table, config), config)


def flags_of(result: ParseResult, index: int = 0) -> set[tuple[str, FieldFlagKind]]:
    return {(f.field, f.kind) for f in result.rows[index].flags}


def one_row(csv_bytes: CsvBuilder, field: str, header: str, value: str) -> ParseResult:
    """Parse one row with an extra column pinned to field, bypassing content checks."""
    config = load_config()
    table = read_table(csv_bytes([HEADER + [header], [GSTIN_A, "1", "15-04-2024", "100", value]]))
    mapping = apply_overrides(propose_mapping(table, config), {field: 4})
    return parse_register(table, mapping, config)


def test_clean_register(csv_bytes: CsvBuilder, register_rows: list[list[object]]) -> None:
    result = parse(csv_bytes(register_rows))
    assert result.skipped == ()
    assert result.input_rows == 3
    assert result.conserved()
    assert all(row.flags == () for row in result.rows)
    common = {
        "source": Source.BOOKS,
        "recipient_gstin": None,
        "itc_available": None,
        "itc_reason": None,
        "return_period": None,
    }
    assert [row.document for row in result.rows] == [
        Document(
            row_ref="2",
            doc_type=DocType.INVOICE,
            supplier_gstin=GSTIN_A,
            supplier_name="Alpha Traders",
            doc_no_raw="INV/001",
            doc_no_norm="INV001",
            doc_date=date(2024, 4, 15),
            place_of_supply="27",
            declared_value=D("1180.00"),
            tax_lines=(TaxLine(D("18"), D("1000.00"), cgst=D("90.00"), sgst=D("90.00")),),
            reverse_charge=False,
            **common,
        ),
        Document(
            row_ref="3",
            doc_type=DocType.INVOICE,
            supplier_gstin=GSTIN_B,
            supplier_name="Beta Supplies",
            doc_no_raw="B-77",
            doc_no_norm="B77",
            doc_date=date(2024, 4, 20),
            place_of_supply="29",
            declared_value=D("526.50"),
            tax_lines=(TaxLine(D("5"), D("500"), igst=D("25"), cess=D("1.50")),),
            reverse_charge=False,
            **common,
        ),
        Document(
            row_ref="4",
            doc_type=DocType.CREDIT_NOTE,
            supplier_gstin=GSTIN_A,
            supplier_name="Alpha Traders",
            doc_no_raw="CN/3",
            doc_no_norm="CN3",
            doc_date=date(2024, 4, 30),
            place_of_supply="27",
            declared_value=D("-236"),
            tax_lines=(TaxLine(D("18"), D("-200"), cgst=D("-18"), sgst=D("-18")),),
            reverse_charge=None,
            **common,
        ),
    ]


def test_credit_note_keeps_given_sign(
    csv_bytes: CsvBuilder, register_rows: list[list[object]]
) -> None:
    document = parse(csv_bytes(register_rows)).rows[2].document
    assert document.taxable_value == D("-200")
    assert document.signed_amounts().taxable_value == D("-200")


def test_blank_and_total_rows(csv_bytes: CsvBuilder) -> None:
    rows: list[list[object]] = [
        ["Purchase Register"],
        HEADER + ["Supplier Name"],
        [GSTIN_A, "1", "15-04-2024", "100", "Alpha"],
        [],
        ["", "", "", "100", "Sub Total"],
        [GSTIN_B, "2", "16-04-2024", "200", "Beta"],
        ["Grand Total", "", "", "300", ""],
        ["", "", "", "300", "TOTAL"],
        ["", "", "", "300", "subtotal"],
    ]
    result = parse(csv_bytes(rows))
    assert [r.document.row_ref for r in result.rows] == ["3", "6"]
    assert [(s.row_ref, s.reason) for s in result.skipped] == [
        ("4", "BLANK"),
        ("5", "TOTAL"),
        ("7", "TOTAL"),
        ("8", "TOTAL"),
        ("9", "TOTAL"),
    ]
    assert result.input_rows == 7
    assert result.conserved()


def test_total_label_needs_empty_keys(csv_bytes: CsvBuilder) -> None:
    rows: list[list[object]] = [
        HEADER + ["Supplier Name"],
        [GSTIN_A, "", "15-04-2024", "100", "Total Traders"],
        ["", "", "", "100", "Totally Real Ltd"],
    ]
    result = parse(csv_bytes(rows))
    assert len(result.rows) == 2
    assert result.skipped == ()


def test_unlabelled_total_row_is_kept(csv_bytes: CsvBuilder) -> None:
    result = parse(csv_bytes([HEADER, [GSTIN_A, "1", "15-04-2024", "100"], ["", "", "", "100"]]))
    assert len(result.rows) == 2
    assert flags_of(result, 1) == {
        ("supplier_gstin", MISSING),
        ("doc_no", MISSING),
        ("doc_date", MISSING),
    }
    assert result.conserved()


def test_missing_and_unparseable_core_fields(csv_bytes: CsvBuilder) -> None:
    rows: list[list[object]] = [
        HEADER,
        ["", "", "31-02-2024", ""],
        [GSTIN_A, "1", "", "abc"],
        [GSTIN_A, "2", "15-04-2024", "100"],
    ]
    result = parse(csv_bytes(rows))
    first, second, _ = result.rows
    assert flags_of(result, 0) == {
        ("supplier_gstin", MISSING),
        ("doc_no", MISSING),
        ("doc_date", UNPARSEABLE),
        ("taxable_value", MISSING),
    }
    assert first.document.supplier_gstin == ""
    assert first.document.doc_no_raw == first.document.doc_no_norm == ""
    assert first.document.doc_date is None
    assert first.document.taxable_value == 0
    assert flags_of(result, 1) == {("doc_date", MISSING), ("taxable_value", UNPARSEABLE)}
    assert second.document.taxable_value == 0


@pytest.mark.parametrize(
    ("field", "value", "flag", "attribute", "expected"),
    [
        ("igst", "n/a", ("igst", UNPARSEABLE), "igst", D("0")),
        ("igst", "", None, "igst", D("0")),
        ("cgst", "x", ("cgst", UNPARSEABLE), "cgst", D("0")),
        ("sgst", "x", ("sgst", UNPARSEABLE), "sgst", D("0")),
        ("cess", "x", ("cess", UNPARSEABLE), "cess", D("0")),
        ("invoice_value", "x", ("invoice_value", UNPARSEABLE), "declared_value", None),
        ("invoice_value", "", None, "declared_value", None),
        ("doc_type", "Journal", ("doc_type", UNPARSEABLE), "doc_type", DocType.INVOICE),
        ("doc_type", "Dr. Note", None, "doc_type", DocType.DEBIT_NOTE),
        ("doc_type", "", None, "doc_type", DocType.INVOICE),
        ("reverse_charge", "maybe", ("reverse_charge", UNPARSEABLE), "reverse_charge", None),
        ("reverse_charge", "Yes", None, "reverse_charge", True),
        ("reverse_charge", "", None, "reverse_charge", None),
        ("place_of_supply", "Atlantis", ("place_of_supply", UNPARSEABLE), "place_of_supply", None),
        ("place_of_supply", "25", ("place_of_supply", UNPARSEABLE), "place_of_supply", None),
        ("place_of_supply", "27", None, "place_of_supply", "27"),
        ("place_of_supply", "27-Maharashtra", None, "place_of_supply", "27"),
        ("place_of_supply", "  tamil   NADU ", None, "place_of_supply", "33"),
        ("place_of_supply", "7", None, "place_of_supply", "07"),
        ("place_of_supply", "270", ("place_of_supply", UNPARSEABLE), "place_of_supply", None),
        ("place_of_supply", "", None, "place_of_supply", None),
        ("supplier_name", "  Alpha  ", None, "supplier_name", "Alpha"),
    ],
)
def test_optional_fields(
    csv_bytes: CsvBuilder,
    field: str,
    value: str,
    flag: tuple[str, FieldFlagKind] | None,
    attribute: str,
    expected: object,
) -> None:
    result = one_row(csv_bytes, field, "Extra", value)
    assert flags_of(result) == ({flag} if flag else set())
    assert getattr(result.rows[0].document, attribute) == expected


@pytest.mark.parametrize(
    ("value", "flag", "expected"),
    [
        ("18%", None, D("18")),
        (" 12.5 % ", None, D("12.5")),
        ("5", None, D("5")),
        ("0.18", None, D("0.18")),
        ("abc", ("rate", UNPARSEABLE), None),
        ("", None, None),
    ],
)
def test_rate(
    csv_bytes: CsvBuilder, value: str, flag: tuple[str, FieldFlagKind] | None, expected: object
) -> None:
    result = one_row(csv_bytes, "rate", "Rate", value)
    assert flags_of(result) == ({flag} if flag else set())
    assert result.rows[0].document.tax_lines[0].rate == expected


def test_unmapped_optional_fields_default(csv_bytes: CsvBuilder) -> None:
    document = parse(csv_bytes([HEADER, [GSTIN_A, "1", "15-04-2024", "100"]])).rows[0].document
    assert document.doc_type is DocType.INVOICE
    assert document.supplier_name is None
    assert document.declared_value is None
    assert document.place_of_supply is None
    assert document.reverse_charge is None
    assert document.tax_lines == (TaxLine(None, D("100")),)


def test_xlsx_cell_types(xlsx_bytes: XlsxBuilder) -> None:
    rows: list[list[object]] = [
        ["Title"],
        HEADER + ["Rate", "Place of Supply", "Reverse Charge", "IGST"],
        [GSTIN_A, 1001.0, datetime(2024, 4, 15, 10, 30), 1000, 18, 7, True, 180.5],
        [GSTIN_A, 1002, 45397, 2000.25, 0.18, 27.0, False, 0],
        [GSTIN_A, 12.5, date(2024, 4, 17), 1, 5, 99, None, None],
        [12345, "X", 100, 1, None, 25, "Y", True],
    ]
    result = parse(xlsx_bytes({"Purchases": rows}))
    first, second, third, fourth = (r.document for r in result.rows)
    assert (first.doc_no_raw, first.doc_no_norm) == ("1001", "1001")
    assert first.doc_date == date(2024, 4, 15)
    assert first.taxable_value == D("1000")
    assert first.tax_lines[0].rate == D("18")
    assert first.place_of_supply == "07"
    assert first.reverse_charge is True
    assert first.igst == D("180.5")
    assert second.doc_no_raw == "1002"
    assert second.doc_date == date(2024, 4, 15)
    assert second.tax_lines[0].rate == D("0.18")
    assert second.place_of_supply == "27"
    assert second.reverse_charge is False
    assert third.doc_no_raw == "12.5"
    assert third.doc_date == date(2024, 4, 17)
    assert third.place_of_supply == "99"
    assert result.rows[2].flags == ()
    assert fourth.supplier_gstin == ""
    assert {(f.field, f.kind) for f in result.rows[3].flags} == {
        ("supplier_gstin", UNPARSEABLE),
        ("doc_date", UNPARSEABLE),
        ("place_of_supply", UNPARSEABLE),
        ("igst", UNPARSEABLE),
    }
    assert [r.document.row_ref for r in result.rows] == ["3", "4", "5", "6"]


def test_excel_serial_range(xlsx_bytes: XlsxBuilder) -> None:
    rows: list[list[object]] = [
        HEADER,
        [GSTIN_A, "1", 36526, 1],
        [GSTIN_A, "2", 73415.0, 1],
        [GSTIN_A, "5", 45397.75, 1],
        [GSTIN_A, "3", 36525, 1],
        [GSTIN_A, "4", 73416, 1],
    ]
    result = parse(xlsx_bytes({"S": rows}))
    dates = [r.document.doc_date for r in result.rows]
    assert dates == [date(2000, 1, 1), date(2100, 12, 31), date(2024, 4, 15), None, None]


def test_strip_fy_affixes_from_config(csv_bytes: CsvBuilder) -> None:
    data = csv_bytes([HEADER, [GSTIN_A, "INV/7/2024-25", "15-04-2024", "100"]])
    assert parse(data).rows[0].document.doc_no_norm == "INV7202425"
    stripped = parse(data, load_config({"strip_fy_affixes": True})).rows[0].document
    assert stripped.doc_no_norm == "INV7"
    assert stripped.doc_no_raw == "INV/7/2024-25"


def test_row_ref_counts_csv_records(csv_bytes: CsvBuilder) -> None:
    rows: list[list[object]] = [
        ["Title"],
        HEADER + ["Supplier Name"],
        [GSTIN_A, "1", "15-04-2024", "100", "Line one\nline two"],
        [GSTIN_A, "2", "15-04-2024", "100", "B"],
    ]
    assert [r.document.row_ref for r in parse(csv_bytes(rows)).rows] == ["3", "4"]


def test_flags_are_field_flags(csv_bytes: CsvBuilder) -> None:
    result = parse(csv_bytes([HEADER, ["", "1", "15-04-2024", "100"]]))
    assert result.rows[0].flags == (FieldFlag("supplier_gstin", MISSING),)


def mapping_for(csv_bytes: CsvBuilder) -> tuple[bytes, ColumnMapping]:
    data = csv_bytes([HEADER, [GSTIN_A, "1", "15-04-2024", "100"]])
    return data, propose_mapping(read_table(data), load_config())


def test_mapping_incomplete(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    data, mapping = mapping_for(csv_bytes)
    incomplete = apply_overrides(mapping, {"doc_date": None})
    with pytest.raises(IngestError) as excinfo:
        parse_register(read_table(data), incomplete, config)
    assert excinfo.value.code == errors.MAPPING_INCOMPLETE


def test_mapping_from_another_table(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    _, mapping = mapping_for(csv_bytes)
    narrow = read_table(csv_bytes([["a", "b"]]))
    with pytest.raises(IngestError) as excinfo:
        parse_register(narrow, mapping, config)
    assert excinfo.value.code == errors.BAD_MAPPING
