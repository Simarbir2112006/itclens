import csv
import io
from collections.abc import Callable, Sequence

import openpyxl
import pytest

from itclens.domain.config import ReconConfig, load_config
from itclens.domain.gstin import with_check_char


def build_xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_csv(
    rows: Sequence[Sequence[object]], delimiter: str = ",", encoding: str = "utf-8"
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerows(rows)
    return buffer.getvalue().encode(encoding)


@pytest.fixture
def xlsx_bytes() -> Callable[[dict[str, list[list[object]]]], bytes]:
    return build_xlsx


@pytest.fixture
def csv_bytes() -> Callable[..., bytes]:
    return build_csv


@pytest.fixture
def config() -> ReconConfig:
    return load_config()


GSTIN_A = with_check_char("27ABCDE1234F1Z")
GSTIN_B = with_check_char("29PQRST5678K2Z")

REGISTER_HEADER = [
    "GSTIN of Supplier",
    "Supplier Name",
    "Invoice No",
    "Invoice Date",
    "Taxable Value",
    "IGST",
    "CGST",
    "SGST",
    "Cess",
    "Rate",
    "Invoice Value",
    "Place of Supply",
    "Voucher Type",
    "Reverse Charge",
]

REGISTER_ROWS = [
    [
        GSTIN_A,
        "Alpha Traders",
        "INV/001",
        "15-04-2024",
        "1,000.00",
        "",
        "90.00",
        "90.00",
        "",
        "18%",
        "1,180.00",
        "27-Maharashtra",
        "Purchase",
        "No",
    ],
    [
        GSTIN_B,
        "Beta Supplies",
        "B-77",
        "2024-04-20",
        "500",
        "25",
        "",
        "",
        "1.50",
        "5",
        "526.50",
        "Karnataka",
        "Purchase",
        "N",
    ],
    [
        GSTIN_A,
        "Alpha Traders",
        "CN/3",
        "30/04/2024",
        "-200",
        "",
        "-18",
        "-18",
        "",
        "18",
        "-236",
        "27",
        "Credit Note",
        "",
    ],
]


@pytest.fixture
def register_rows() -> list[list[object]]:
    return [list(REGISTER_HEADER)] + [list(row) for row in REGISTER_ROWS]
