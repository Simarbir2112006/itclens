"""Convert purchase register rows into Documents.

Rows after the header row are read in order. row_ref is the 1-based source row
number (for CSV, the record number), so it matches what the user sees in Excel.
A row with every cell empty is skipped as BLANK. A row is skipped as TOTAL when
its first text cell (a string that is not an amount) starts with "total",
"sub total" or "grand total" and its supplier_gstin and doc_no cells are empty
apart from that label. An unlabelled totals row is kept and gets MISSING flags.

Field defaults (flags in brackets):
- doc_type: blank or unmapped -> INVOICE; unknown text -> INVOICE [UNPARSEABLE].
- supplier_gstin: normalized text; blank -> "" [MISSING]; a non-text cell -> ""
  [UNPARSEABLE]. The GSTIN itself is not validated here.
- supplier_name: stripped text or None.
- doc_no: doc_no_raw is the cell text (1001.0 gives "1001"); blank -> "" [MISSING].
- doc_date: date cells, Excel serials from 36526 to 73415, or text in one of
  config.date_formats; blank -> None [MISSING]; otherwise None [UNPARSEABLE].
- taxable_value: blank -> 0 [MISSING]; unparseable -> 0 [UNPARSEABLE].
- igst, cgst, sgst, cess: blank or unmapped -> 0; unparseable -> 0 [UNPARSEABLE].
- invoice_value (declared_value): blank or unmapped -> None; unparseable -> None
  [UNPARSEABLE].
- rate: text may end in "%"; numbers are used as they are, so a fraction such as
  0.18 is not turned into 18. That ambiguity is a known gap left to the rate-slab
  control. Blank or unmapped -> None; unparseable -> None [UNPARSEABLE].
- place_of_supply: a leading two-digit code in config.valid_state_codes (a
  number or single digit such as 7 is read as "07"), or a state name; blank ->
  None; otherwise None [UNPARSEABLE].
- reverse_charge: yes/no style values; blank -> None; unknown -> None
  [UNPARSEABLE].
- recipient_gstin, itc_available, itc_reason and return_period are None.

Unparseable money becomes 0 with a flag, so later layers must not trust the
amounts of a flagged row. Each row yields exactly one TaxLine.
"""

import re
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import TypeVar

from itclens.domain.config import ReconConfig, load_state_names
from itclens.domain.gstin import normalize_gstin
from itclens.domain.models import DocType, Document, Source, TaxLine
from itclens.domain.normalize import normalize_invoice_no, parse_money
from itclens.ingestion.cells import cell_date, cell_text, parse_rate
from itclens.ingestion.columns import (
    ColumnMapping,
    RegisterVocabulary,
    load_register_vocabulary,
    normalize_header,
)
from itclens.ingestion.errors import BAD_MAPPING, MAPPING_INCOMPLETE, IngestError
from itclens.ingestion.table_reader import RawTable
from itclens.ingestion.types import FieldFlag, FieldFlagKind, ParsedRow, ParseResult, SkippedRow

ZERO = Decimal("0")
TOTAL_LABEL = re.compile(r"^(grand\s+)?(sub\s*)?total\b", re.IGNORECASE)
_LEADING_CODE = re.compile(r"^([0-9]{2})(?![0-9])")
_SINGLE_DIGIT = re.compile(r"^[0-9]$")

MISSING = FieldFlagKind.MISSING
UNPARSEABLE = FieldFlagKind.UNPARSEABLE

_T = TypeVar("_T")


class _StateResolver:
    def __init__(self, valid_codes: frozenset[str], names: Mapping[str, str]) -> None:
        self._valid_codes = valid_codes
        self._by_name = {
            normalize_header(name): code for code, name in names.items() if code in valid_codes
        }

    def __call__(self, value: object) -> str | None:
        text = cell_text(value)
        if text is None:
            return None
        if _SINGLE_DIGIT.match(text):
            text = "0" + text
        match = _LEADING_CODE.match(text)
        if match and match.group(1) in self._valid_codes:
            return match.group(1)
        return self._by_name.get(normalize_header(text))


class _RowParser:
    def __init__(
        self,
        row: tuple[object, ...],
        columns: Mapping[str, int],
        config: ReconConfig,
        vocab: RegisterVocabulary,
        states: _StateResolver,
    ) -> None:
        self._row = row
        self._columns = columns
        self._config = config
        self._vocab = vocab
        self._states = states
        self.flags: list[FieldFlag] = []

    def cell(self, field: str) -> object:
        column = self._columns.get(field)
        return None if column is None else self._row[column]

    def flag(self, field: str, kind: FieldFlagKind) -> None:
        self.flags.append(FieldFlag(field, kind))

    def doc_type(self) -> DocType:
        value = self.cell("doc_type")
        if value is None:
            return DocType.INVOICE
        found = self._vocab.doc_type_of(value)
        if found is None:
            self.flag("doc_type", UNPARSEABLE)
            return DocType.INVOICE
        return found

    def supplier_gstin(self) -> str:
        value = self.cell("supplier_gstin")
        if value is None:
            self.flag("supplier_gstin", MISSING)
            return ""
        if not isinstance(value, str):
            self.flag("supplier_gstin", UNPARSEABLE)
            return ""
        return normalize_gstin(value)

    def doc_no(self) -> tuple[str, str]:
        raw = cell_text(self.cell("doc_no"))
        if raw is None:
            self.flag("doc_no", MISSING)
            return "", ""
        return raw, normalize_invoice_no(raw, strip_fy_affixes=self._config.strip_fy_affixes)

    def doc_date(self) -> date | None:
        value = self.cell("doc_date")
        if value is None:
            self.flag("doc_date", MISSING)
            return None
        parsed = cell_date(value, self._config.date_formats)
        if parsed is None:
            self.flag("doc_date", UNPARSEABLE)
        return parsed

    def money(self, field: str, *, required: bool) -> Decimal:
        value = self.cell(field)
        if value is None:
            if required:
                self.flag(field, MISSING)
            return ZERO
        parsed = parse_money(value)
        if parsed is None:
            self.flag(field, UNPARSEABLE)
            return ZERO
        return parsed

    def optional(self, field: str, parse: Callable[[object], _T | None]) -> _T | None:
        value = self.cell(field)
        if value is None:
            return None
        parsed = parse(value)
        if parsed is None:
            self.flag(field, UNPARSEABLE)
        return parsed

    def reverse_charge(self, value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        return self._vocab.reverse_charge_of(value)

    def document(self, row_ref: str) -> Document:
        doc_type = self.doc_type()
        supplier_gstin = self.supplier_gstin()
        doc_no_raw, doc_no_norm = self.doc_no()
        doc_date = self.doc_date()
        line = TaxLine(
            rate=self.optional("rate", parse_rate),
            taxable_value=self.money("taxable_value", required=True),
            igst=self.money("igst", required=False),
            cgst=self.money("cgst", required=False),
            sgst=self.money("sgst", required=False),
            cess=self.money("cess", required=False),
        )
        return Document(
            source=Source.BOOKS,
            row_ref=row_ref,
            doc_type=doc_type,
            supplier_gstin=supplier_gstin,
            supplier_name=cell_text(self.cell("supplier_name")),
            recipient_gstin=None,
            doc_no_raw=doc_no_raw,
            doc_no_norm=doc_no_norm,
            doc_date=doc_date,
            place_of_supply=self.optional("place_of_supply", self._states),
            declared_value=self.optional("invoice_value", parse_money),
            tax_lines=(line,),
            reverse_charge=self.optional("reverse_charge", self.reverse_charge),
            itc_available=None,
            itc_reason=None,
            return_period=None,
        )


def _is_label(value: object) -> bool:
    # CSV cells are always strings, so amounts are skipped to treat CSV and XLSX alike.
    return isinstance(value, str) and parse_money(value) is None


def _is_total_row(row: tuple[object, ...], columns: Mapping[str, int]) -> bool:
    label_column = next((i for i, value in enumerate(row) if _is_label(value)), None)
    if label_column is None:
        return False
    label = row[label_column]
    if not isinstance(label, str) or not TOTAL_LABEL.match(label.strip()):
        return False
    # The label itself may sit in the GSTIN or invoice number column.
    return all(
        row[columns[field]] is None or columns[field] == label_column
        for field in ("supplier_gstin", "doc_no")
    )


def parse_register(table: RawTable, mapping: ColumnMapping, config: ReconConfig) -> ParseResult:
    if mapping.missing_required:
        raise IngestError(MAPPING_INCOMPLETE)
    columns = mapping.columns()
    if mapping.header_row >= len(table.rows) or any(c >= table.width for c in columns.values()):
        raise IngestError(BAD_MAPPING)

    vocab = load_register_vocabulary()
    states = _StateResolver(config.valid_state_codes, load_state_names())
    parsed: list[ParsedRow] = []
    skipped: list[SkippedRow] = []
    first = mapping.header_row + 1
    for index in range(first, len(table.rows)):
        row = table.rows[index]
        row_ref = str(index + 1)
        if all(value is None for value in row):
            skipped.append(SkippedRow(row_ref, "BLANK"))
        elif _is_total_row(row, columns):
            skipped.append(SkippedRow(row_ref, "TOTAL"))
        else:
            parser = _RowParser(row, columns, config, vocab, states)
            document = parser.document(row_ref)
            parsed.append(ParsedRow(document, tuple(parser.flags)))
    return ParseResult(tuple(parsed), tuple(skipped), len(table.rows) - first)
