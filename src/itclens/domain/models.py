"""Normalized records shared by the ingestion, quality, matching and reporting layers."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from itclens.domain.period import ReturnPeriod

ZERO = Decimal("0")


class Source(Enum):
    BOOKS = "books"
    GSTR2B = "gstr2b"


class DocType(Enum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


@dataclass(frozen=True, slots=True)
class TaxLine:
    rate: Decimal | None
    taxable_value: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class TaxAmounts:
    taxable_value: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal
    total_tax: Decimal
    computed_value: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class Document:
    """One invoice or note from either source, after normalization.

    row_ref is an input row number or a JSON path and is only used to point at
    the source in issue references. declared_value is the document total as
    stated in the input; computed_value is derived from the tax lines.
    """

    source: Source
    row_ref: str
    doc_type: DocType
    supplier_gstin: str
    supplier_name: str | None
    recipient_gstin: str | None
    doc_no_raw: str
    doc_no_norm: str
    doc_date: date | None
    place_of_supply: str | None
    declared_value: Decimal | None
    tax_lines: tuple[TaxLine, ...]
    reverse_charge: bool | None
    itc_available: bool | None
    itc_reason: str | None
    return_period: ReturnPeriod | None

    @property
    def taxable_value(self) -> Decimal:
        return sum((line.taxable_value for line in self.tax_lines), ZERO)

    @property
    def igst(self) -> Decimal:
        return sum((line.igst for line in self.tax_lines), ZERO)

    @property
    def cgst(self) -> Decimal:
        return sum((line.cgst for line in self.tax_lines), ZERO)

    @property
    def sgst(self) -> Decimal:
        return sum((line.sgst for line in self.tax_lines), ZERO)

    @property
    def cess(self) -> Decimal:
        return sum((line.cess for line in self.tax_lines), ZERO)

    @property
    def total_tax(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess

    @property
    def computed_value(self) -> Decimal:
        return self.taxable_value + self.total_tax

    def signed_amounts(self) -> TaxAmounts:
        """Return the totals with the sign used for matching.

        Each field is taken as an absolute value. Credit notes are then negated;
        invoices and debit notes stay positive. A credit note that arrives already
        negative therefore gives the same result as a positive one. Negative amounts
        on an invoice are a data quality issue reported by the quality layer, not
        corrected here.
        """
        negate = self.doc_type is DocType.CREDIT_NOTE

        def signed(value: Decimal) -> Decimal:
            # ZERO - x keeps a zero amount as 0 rather than -0
            return ZERO - abs(value) if negate else abs(value)

        return TaxAmounts(
            taxable_value=signed(self.taxable_value),
            igst=signed(self.igst),
            cgst=signed(self.cgst),
            sgst=signed(self.sgst),
            cess=signed(self.cess),
            total_tax=signed(self.total_tax),
            computed_value=signed(self.computed_value),
        )
