import dataclasses
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from itclens.domain.models import DocType, Document, Source, TaxAmounts, TaxLine
from itclens.domain.period import ReturnPeriod

D = Decimal

LINES = (
    TaxLine(rate=D("18"), taxable_value=D("1000.00"), igst=D("180.00")),
    TaxLine(rate=D("5"), taxable_value=D("200.00"), cgst=D("5.00"), sgst=D("5.00"), cess=D("1.50")),
)


def make_doc(**changes: Any) -> Document:
    fields: dict[str, Any] = {
        "source": Source.BOOKS,
        "row_ref": "2",
        "doc_type": DocType.INVOICE,
        "supplier_gstin": "27AAPFU0939F1ZV",
        "supplier_name": "Supplier",
        "recipient_gstin": None,
        "doc_no_raw": "INV/1",
        "doc_no_norm": "INV1",
        "doc_date": date(2024, 4, 15),
        "place_of_supply": "27",
        "declared_value": D("1391.50"),
        "tax_lines": LINES,
        "reverse_charge": False,
        "itc_available": None,
        "itc_reason": None,
        "return_period": ReturnPeriod(2024, 4),
    }
    fields.update(changes)
    return Document(**fields)


def test_tax_line_defaults() -> None:
    line = TaxLine(rate=None)
    assert line.taxable_value == line.igst == line.cgst == line.sgst == line.cess == D("0")


def test_totals_over_tax_lines() -> None:
    doc = make_doc()
    assert doc.taxable_value == D("1200.00")
    assert doc.igst == D("180.00")
    assert doc.cgst == D("5.00")
    assert doc.sgst == D("5.00")
    assert doc.cess == D("1.50")
    assert doc.total_tax == D("191.50")
    assert doc.computed_value == D("1391.50")


def test_no_tax_lines() -> None:
    doc = make_doc(tax_lines=())
    assert doc.taxable_value == 0
    assert doc.computed_value == 0


EXPECTED = TaxAmounts(
    taxable_value=D("1200.00"),
    igst=D("180.00"),
    cgst=D("5.00"),
    sgst=D("5.00"),
    cess=D("1.50"),
    total_tax=D("191.50"),
    computed_value=D("1391.50"),
)


def negated(amounts: TaxAmounts) -> TaxAmounts:
    return TaxAmounts(*(-getattr(amounts, f.name) for f in dataclasses.fields(TaxAmounts)))


def negative_lines() -> tuple[TaxLine, ...]:
    return tuple(
        TaxLine(
            rate=line.rate,
            taxable_value=-line.taxable_value,
            igst=-line.igst,
            cgst=-line.cgst,
            sgst=-line.sgst,
            cess=-line.cess,
        )
        for line in LINES
    )


def test_invoice_is_positive() -> None:
    assert make_doc(doc_type=DocType.INVOICE).signed_amounts() == EXPECTED


def test_debit_note_is_positive() -> None:
    assert make_doc(doc_type=DocType.DEBIT_NOTE).signed_amounts() == EXPECTED


def test_credit_note_is_negative() -> None:
    assert make_doc(doc_type=DocType.CREDIT_NOTE).signed_amounts() == negated(EXPECTED)


def test_credit_note_already_negative() -> None:
    doc = make_doc(doc_type=DocType.CREDIT_NOTE, tax_lines=negative_lines())
    assert doc.signed_amounts() == negated(EXPECTED)


def test_negative_invoice_becomes_positive() -> None:
    doc = make_doc(doc_type=DocType.INVOICE, tax_lines=negative_lines())
    assert doc.signed_amounts() == EXPECTED


def test_zero_credit_note_has_no_negative_zero() -> None:
    doc = make_doc(doc_type=DocType.CREDIT_NOTE, tax_lines=(TaxLine(rate=None),))
    assert not doc.signed_amounts().cess.is_signed()


def test_frozen() -> None:
    doc = make_doc()
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.doc_no_norm = "X"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        LINES[0].igst = D("1")  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        EXPECTED.igst = D("1")  # type: ignore[misc]


def test_document_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        Document(Source.BOOKS)  # type: ignore[misc]
