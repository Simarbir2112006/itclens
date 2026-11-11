import json
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from itclens.domain.config import ReconConfig, read_packaged_json
from itclens.domain.gstin import with_check_char
from itclens.domain.models import DocType
from itclens.ingestion import errors
from itclens.ingestion.columns import (
    FIELD_NAMES,
    REGISTER_FIELDS,
    ColumnMapping,
    apply_overrides,
    detect_header_row,
    load_register_vocabulary,
    normalize_header,
    propose_mapping,
)
from itclens.ingestion.errors import IngestError
from itclens.ingestion.register import parse_register
from itclens.ingestion.table_reader import RawTable, read_table

CsvBuilder = Callable[..., bytes]
GSTIN = with_check_char("27ABCDE1234F1Z")


def table_of(csv_bytes: CsvBuilder, rows: list[list[object]]) -> RawTable:
    return read_table(csv_bytes(rows))


def by_field(mapping: ColumnMapping) -> dict[str, Any]:
    return {a.field: a for a in mapping.assignments}


def test_normalize_header() -> None:
    assert normalize_header("  GSTIN/UIN of Supplier ") == "gstin uin of supplier"
    assert normalize_header("Invoice No.") == "invoice no"
    assert normalize_header("Taxable_Value (Rs.)") == "taxable value rs"
    assert normalize_header(None) == ""
    assert normalize_header(2024.0) == "2024"


def test_vocabulary_file_is_consistent() -> None:
    data = read_packaged_json("register_fields.json")
    assert "unverified" in data["_note"].lower()
    assert set(data["synonyms"]) == set(FIELD_NAMES)
    for tiers in data["synonyms"].values():
        for synonym in tiers["strong"] + tiers["weak"]:
            assert normalize_header(synonym) == synonym
    assert "voucher no" in data["synonyms"]["doc_no"]["weak"]
    assert "particulars" in data["synonyms"]["supplier_name"]["weak"]
    assert {DocType[v] for v in data["doc_type_values"].values()} == set(DocType)


def test_value_lookups() -> None:
    vocab = load_register_vocabulary()
    assert vocab.doc_type_of("Credit Note") is DocType.CREDIT_NOTE
    assert vocab.doc_type_of("CreditNote") is DocType.CREDIT_NOTE
    assert vocab.doc_type_of("Dr. Note") is DocType.DEBIT_NOTE
    assert vocab.doc_type_of("Journal") is None
    assert vocab.reverse_charge_of(" YES ") is True
    assert vocab.reverse_charge_of(0) is False
    assert vocab.reverse_charge_of("maybe") is None


def test_header_on_first_row(csv_bytes: CsvBuilder, register_rows: list[list[object]]) -> None:
    assert detect_header_row(table_of(csv_bytes, register_rows)) == 0


def test_header_below_title_rows(
    csv_bytes: CsvBuilder, register_rows: list[list[object]], config: ReconConfig
) -> None:
    rows = [["Purchase Register"], ["Period: April 2024"], []] + register_rows
    table = table_of(csv_bytes, rows)
    assert detect_header_row(table) == 3
    mapping = propose_mapping(table, config)
    assert mapping.header_row == 3
    assert mapping.missing_required == ()
    assert mapping.needs_confirmation == ()
    assert [a.field for a in mapping.assignments] == list(FIELD_NAMES)
    assert all(a.method == "header_strong" and a.confidence == 1.0 for a in mapping.assignments)


def test_no_header_found(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    table = table_of(csv_bytes, [["foo", "bar"], ["1", "2"]])
    assert detect_header_row(table) is None
    with pytest.raises(IngestError) as excinfo:
        propose_mapping(table, config)
    assert excinfo.value.code == errors.HEADER_NOT_FOUND


def test_explicit_header_row(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    table = table_of(csv_bytes, [["x"], ["GSTIN", "Invoice No", "Date"], [GSTIN, "1", "x"]])
    assert propose_mapping(table, config, header_row=1).header_row == 1
    with pytest.raises(IngestError) as excinfo:
        propose_mapping(table, config, header_row=5)
    assert excinfo.value.code == errors.HEADER_NOT_FOUND


def test_strong_weak_and_fuzzy(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [
        ["Supplier's GSTIN", "Particulars", "Voucher No", "Invoice Dt", "Taxable Value"],
        [GSTIN, "Alpha", "V1", "15-04-2024", "100"],
    ]
    mapping = propose_mapping(table_of(csv_bytes, rows), config)
    fields = by_field(mapping)
    assert fields["supplier_gstin"].method == "header_fuzzy"
    assert 0.8 < fields["supplier_gstin"].confidence < 0.9
    assert fields["supplier_name"].method == "header_weak"
    assert fields["supplier_name"].confidence == pytest.approx(0.6)
    assert fields["doc_no"].method == "header_weak"
    assert fields["doc_date"].method == "header_fuzzy"
    assert fields["taxable_value"].method == "header_strong"
    assert {"supplier_name", "doc_no"} <= set(mapping.needs_confirmation)
    assert "taxable_value" not in mapping.needs_confirmation


def test_exact_match_blocks_fuzzy_for_other_fields(
    csv_bytes: CsvBuilder, config: ReconConfig
) -> None:
    rows = [
        ["GSTIN", "Invoice No", "Invoice Date", "Taxable Value", "CGST Amount", "SGST Amount"],
        [GSTIN, "1", "15-04-2024", "100", "9", "9"],
    ]
    fields = by_field(propose_mapping(table_of(csv_bytes, rows), config))
    assert "igst" not in fields
    assert fields["cgst"].column == 4
    assert fields["sgst"].column == 5


def test_content_only_gstin_and_date(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [
        ["Col A", "Invoice No", "Col C", "Taxable Value", "Col E"],
        [GSTIN, "1", "15-04-2024", "100", "250.00"],
        [with_check_char("29PQRST5678K2Z"), "2", "16-04-2024", "200", "300.00"],
    ]
    mapping = propose_mapping(table_of(csv_bytes, rows), config, header_row=0)
    fields = by_field(mapping)
    assert fields["supplier_gstin"].method == "content"
    assert fields["supplier_gstin"].column == 0
    assert fields["supplier_gstin"].confidence == pytest.approx(0.7)
    assert fields["doc_date"].method == "content"
    assert fields["doc_date"].column == 2
    assert {"supplier_gstin", "doc_date"} <= set(mapping.needs_confirmation)
    assert 4 not in {a.column for a in mapping.assignments}


def test_amount_not_assigned_by_content(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [["GSTIN", "Invoice No", "Invoice Date", "Col D"], [GSTIN, "1", "15-04-2024", "100"]]
    mapping = propose_mapping(table_of(csv_bytes, rows), config)
    assert mapping.missing_required == ("taxable_value",)
    assert 3 not in {a.column for a in mapping.assignments}


def test_content_only_needs_most_cells(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    gstins = [[GSTIN]] * 8 + [["x"]] * 2
    rows = [["Col A", "Invoice No", "Invoice Date", "Taxable Value"]] + [
        g + ["1", "15-04-2024", "1"] for g in gstins
    ]
    mapping = propose_mapping(table_of(csv_bytes, rows), config)
    assert "supplier_gstin" in mapping.missing_required


def test_contradicting_content_halves_score(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [
        ["GSTIN", "Invoice No", "Invoice Date", "Taxable Value", "IGST"],
        [GSTIN, "1", "Alpha", "100", "n/a"],
        [GSTIN, "2", "Beta", "200", "n/a"],
    ]
    mapping = propose_mapping(table_of(csv_bytes, rows), config)
    fields = by_field(mapping)
    assert "doc_date" not in fields
    assert "igst" not in fields
    assert mapping.missing_required == ("doc_date",)


def test_empty_column_is_not_penalised(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [
        ["GSTIN", "Invoice No", "Invoice Date", "Taxable Value", "IGST"],
        [GSTIN, "1", "15-04-2024", "100", ""],
    ]
    fields = by_field(propose_mapping(table_of(csv_bytes, rows), config))
    assert fields["igst"].confidence == 1.0


def test_two_date_columns(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [
        ["GSTIN", "Invoice No", "Entry Date", "Invoice Date", "Taxable Value"],
        [GSTIN, "1", "20-04-2024", "15-04-2024", "100"],
        [GSTIN, "2", "21-04-2024", "16-04-2024", "200"],
    ]
    mapping = propose_mapping(table_of(csv_bytes, rows), config)
    fields = by_field(mapping)
    assert fields["doc_date"].column == 3
    assert 2 not in {a.column for a in mapping.assignments}
    assert "doc_date" not in mapping.needs_confirmation


def test_close_runner_up_needs_confirmation(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [
        ["GSTIN", "Invoice No", "Bill No", "Invoice Date", "Taxable Value"],
        [GSTIN, "1", "B1", "15-04-2024", "100"],
    ]
    mapping = propose_mapping(table_of(csv_bytes, rows), config)
    assert by_field(mapping)["doc_no"].column == 1
    assert mapping.needs_confirmation == ("doc_no",)
    assert dict(mapping.alternatives) == {"doc_no": (2,)}


def test_missing_required(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    rows = [["Supplier Name", "Invoice No", "Taxable Value"], ["A", "1", "100"]]
    mapping = propose_mapping(table_of(csv_bytes, rows), config)
    assert mapping.missing_required == ("supplier_gstin", "doc_date")


def bills_mapping(csv_bytes: CsvBuilder, config: ReconConfig) -> ColumnMapping:
    rows = [
        ["GSTIN", "Invoice No", "Bill No", "Invoice Date", "Taxable Value", "Notes"],
        [GSTIN, "1", "B1", "15-04-2024", "100", "x"],
    ]
    return propose_mapping(table_of(csv_bytes, rows), config)


def test_apply_overrides(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    mapping = bills_mapping(csv_bytes, config)
    updated = apply_overrides(mapping, {"doc_no": None})
    assert "doc_no" not in by_field(updated)
    assert updated.missing_required == ("doc_no",)
    assert updated.needs_confirmation == ()

    updated = apply_overrides(updated, {"doc_no": 2, "supplier_name": 5})
    fields = by_field(updated)
    assert fields["doc_no"].column == 2
    assert fields["doc_no"].method == "user"
    assert fields["doc_no"].confidence == 1.0
    assert fields["doc_no"].header == "Bill No"
    assert fields["supplier_name"].column == 5
    assert updated.missing_required == ()
    assert updated.needs_confirmation == ()
    assert updated.alternatives == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown_field": 1},
        {"doc_no": 6},
        {"doc_no": -1},
        {"doc_no": True},
        {"doc_no": "2"},
        {"doc_no": 3},
        {"doc_no": 2, "supplier_name": 2},
    ],
)
def test_apply_overrides_rejects(
    csv_bytes: CsvBuilder, config: ReconConfig, overrides: dict[str, Any]
) -> None:
    mapping = bills_mapping(csv_bytes, config)
    with pytest.raises(IngestError) as excinfo:
        apply_overrides(mapping, overrides)
    assert excinfo.value.code == errors.BAD_MAPPING


def test_to_dict_is_json(csv_bytes: CsvBuilder, config: ReconConfig) -> None:
    mapping = bills_mapping(csv_bytes, config)
    data = json.loads(json.dumps(mapping.to_dict()))
    assert data["header_row"] == 0
    assert data["headers"][2] == "Bill No"
    assert data["alternatives"] == {"doc_no": [2]}
    assert data["assignments"][0] == {
        "field": "supplier_gstin",
        "column": 0,
        "header": "GSTIN",
        "confidence": 1.0,
        "method": "header_strong",
    }


def test_register_fields_order() -> None:
    required = [spec.name for spec in REGISTER_FIELDS if spec.required]
    assert required == ["supplier_gstin", "doc_no", "doc_date", "taxable_value"]


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(order=st.permutations(range(14)))
def test_column_order_does_not_matter(
    order: list[int],
    csv_bytes: CsvBuilder,
    config: ReconConfig,
    register_rows: list[list[object]],
) -> None:
    def run(rows: list[list[object]]) -> tuple[dict[str, str | None], list[Any]]:
        table = table_of(csv_bytes, rows)
        mapping = propose_mapping(table, config)
        result = parse_register(table, mapping, config)
        headers = {a.field: a.header for a in mapping.assignments}
        return headers, [(r.document, r.flags) for r in result.rows]

    permuted = [[row[i] for i in order] for row in register_rows]
    assert run(permuted) == run(register_rows)
