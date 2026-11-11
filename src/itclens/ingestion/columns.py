"""Find the header row of a purchase register and map its columns to fields.

Scoring is deterministic. Each (field, column) pair gets a score from the header
text and, for some kinds, a check of the cells below the header:

- Header score: exact strong synonym 1.0, exact weak synonym 0.6. Headers with no
  exact match for any field are compared with difflib; a ratio of at least 0.85
  gives 0.9 * ratio for a strong synonym and 0.6 * ratio for a weak one. A header
  that matches some field exactly gets no fuzzy scores, so "SGST Amount" cannot
  become a weak IGST candidate.
- Content: for gstin, date, money and rate fields, a header score is halved when
  fewer than 30% of the sampled cells fit the kind. A column with no data below
  the header is not penalised.
- Content only: supplier_gstin and doc_date may be found from content alone when
  at least 90% of the sampled cells fit, with score 0.7. Amounts never are.
- Columns are assigned greedily by descending score, then field order, then
  column index. Each column goes to at most one field; the minimum score is 0.6.

The synonym lists in data/register_fields.json are invented starter lists.
"""

import functools
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal, TypeVar

from itclens.domain.config import ReconConfig, read_packaged_json
from itclens.domain.gstin import GSTIN_PATTERN, normalize_gstin
from itclens.domain.models import DocType
from itclens.domain.normalize import parse_money
from itclens.ingestion.cells import cell_date, cell_text, parse_rate
from itclens.ingestion.errors import BAD_MAPPING, HEADER_NOT_FOUND, IngestError
from itclens.ingestion.table_reader import RawTable

FieldKind = Literal["gstin", "text", "date", "money", "rate", "state", "doctype", "bool"]
Method = Literal["header_strong", "header_weak", "header_fuzzy", "content", "user"]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    required: bool
    kind: FieldKind


REGISTER_FIELDS = (
    FieldSpec("supplier_gstin", True, "gstin"),
    FieldSpec("supplier_name", False, "text"),
    FieldSpec("doc_no", True, "text"),
    FieldSpec("doc_date", True, "date"),
    FieldSpec("taxable_value", True, "money"),
    FieldSpec("igst", False, "money"),
    FieldSpec("cgst", False, "money"),
    FieldSpec("sgst", False, "money"),
    FieldSpec("cess", False, "money"),
    FieldSpec("rate", False, "rate"),
    FieldSpec("invoice_value", False, "money"),
    FieldSpec("place_of_supply", False, "state"),
    FieldSpec("doc_type", False, "doctype"),
    FieldSpec("reverse_charge", False, "bool"),
)
FIELD_NAMES = tuple(spec.name for spec in REGISTER_FIELDS)
_FIELD_ORDER = {name: i for i, name in enumerate(FIELD_NAMES)}

STRONG_SCORE = 1.0
WEAK_SCORE = 0.6
FUZZY_MIN_RATIO = 0.85
STRONG_FUZZY_WEIGHT = 0.9
WEAK_FUZZY_WEIGHT = 0.6
CONTRADICTION_FRACTION = 0.3
CONTENT_ONLY_FRACTION = 0.9
CONTENT_ONLY_SCORE = 0.7
CONTENT_ONLY_FIELDS = frozenset({"supplier_gstin", "doc_date"})
MIN_ASSIGN_SCORE = 0.6
CONFIDENT_SCORE = 0.8
RUNNER_UP_MARGIN = 0.1
SAMPLE_CELLS = 200
HEADER_SCAN_ROWS = 30
MIN_HEADER_FIELDS = 3
MAX_HEADER_LENGTH = 100
_EPSILON = 1e-9

_NON_ALNUM_RUN = re.compile(r"[^0-9a-z]+")


def normalize_header(value: object) -> str:
    """Lowercase, turn every run of other characters into one space, strip."""
    text = cell_text(value)
    if text is None:
        return ""
    return _NON_ALNUM_RUN.sub(" ", text.lower()).strip()


@dataclass(frozen=True, slots=True)
class _Synonym:
    field: str
    strong: bool
    spaced: str
    despaced: str


@dataclass(frozen=True)
class RegisterVocabulary:
    exact: Mapping[str, tuple[_Synonym, ...]]
    synonyms: tuple[_Synonym, ...]
    doc_types: Mapping[str, DocType]
    reverse_charge: Mapping[str, bool]

    def doc_type_of(self, value: object) -> DocType | None:
        return _lookup(self.doc_types, value)

    def reverse_charge_of(self, value: object) -> bool | None:
        return _lookup(self.reverse_charge, value)


def _lookup(table: Mapping[str, _T], value: object) -> _T | None:
    key = normalize_header(value)
    found = table.get(key)
    return found if found is not None else table.get(key.replace(" ", ""))


def _with_despaced(table: Mapping[str, _T]) -> dict[str, _T]:
    keys = {normalize_header(k): v for k, v in table.items()}
    result = {k.replace(" ", ""): v for k, v in keys.items()}
    result.update(keys)
    return result


@functools.cache
def load_register_vocabulary() -> RegisterVocabulary:
    data = read_packaged_json("register_fields.json")
    synonyms: list[_Synonym] = []
    for name in FIELD_NAMES:
        tiers = data["synonyms"][name]
        for tier in ("strong", "weak"):
            for text in tiers[tier]:
                spaced = normalize_header(text)
                synonyms.append(_Synonym(name, tier == "strong", spaced, spaced.replace(" ", "")))

    exact: dict[str, list[_Synonym]] = {}
    for synonym in synonyms:
        for form in {synonym.spaced, synonym.despaced}:
            exact.setdefault(form, []).append(synonym)

    return RegisterVocabulary(
        exact={k: tuple(v) for k, v in exact.items()},
        synonyms=tuple(synonyms),
        doc_types=_with_despaced({k: DocType[v] for k, v in data["doc_type_values"].items()}),
        reverse_charge=_with_despaced(dict(data["reverse_charge_values"])),
    )


@dataclass(frozen=True, slots=True)
class _Score:
    value: float
    method: Method


def _ratio(a: str, b: str) -> float:
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < FUZZY_MIN_RATIO or matcher.quick_ratio() < FUZZY_MIN_RATIO:
        return 0.0
    return matcher.ratio()


def _header_scores(header: str, vocab: RegisterVocabulary) -> dict[str, _Score]:
    """Best header score per field for a normalized header."""
    if not header or len(header) > MAX_HEADER_LENGTH:
        return {}
    despaced = header.replace(" ", "")

    exact: dict[str, _Score] = {}
    for form in (header, despaced):
        for synonym in vocab.exact.get(form, ()):
            score = (
                _Score(STRONG_SCORE, "header_strong")
                if synonym.strong
                else _Score(WEAK_SCORE, "header_weak")
            )
            current = exact.get(synonym.field)
            if current is None or score.value > current.value:
                exact[synonym.field] = score
    if exact:
        return exact

    fuzzy: dict[str, _Score] = {}
    for synonym in vocab.synonyms:
        ratio = max(_ratio(header, synonym.spaced), _ratio(despaced, synonym.despaced))
        if ratio < FUZZY_MIN_RATIO:
            continue
        weight = STRONG_FUZZY_WEIGHT if synonym.strong else WEAK_FUZZY_WEIGHT
        value = weight * ratio
        current = fuzzy.get(synonym.field)
        if current is None or value > current.value:
            fuzzy[synonym.field] = _Score(value, "header_fuzzy")
    return fuzzy


class _HeaderScorer:
    """Scores header texts, caching per call so repeated cells are scored once."""

    def __init__(self) -> None:
        self._vocab = load_register_vocabulary()
        self._cache: dict[str, dict[str, _Score]] = {}

    def __call__(self, value: object) -> dict[str, _Score]:
        header = normalize_header(value)
        scores = self._cache.get(header)
        if scores is None:
            scores = _header_scores(header, self._vocab)
            self._cache[header] = scores
        return scores


def _best_field(scores: Mapping[str, _Score]) -> tuple[str, _Score]:
    return min(scores.items(), key=lambda item: (-item[1].value, _FIELD_ORDER[item[0]]))


def detect_header_row(table: RawTable, scan_rows: int = HEADER_SCAN_ROWS) -> int | None:
    """Find the header row among the first scan_rows rows.

    The chosen row has the most cells scoring at least 0.6 and covers at least 3
    distinct fields. The earliest row wins ties.
    """
    scorer = _HeaderScorer()
    best_row, best_count = None, 0
    for index, row in enumerate(table.rows[:scan_rows]):
        count = 0
        fields: set[str] = set()
        for cell in row:
            if not isinstance(cell, str):
                continue
            scores = scorer(cell)
            if not scores:
                continue
            field, score = _best_field(scores)
            if score.value >= MIN_ASSIGN_SCORE:
                count += 1
                fields.add(field)
        if len(fields) >= MIN_HEADER_FIELDS and count > best_count:
            best_row, best_count = index, count
    return best_row


@dataclass(frozen=True, slots=True)
class ColumnAssignment:
    field: str
    column: int
    header: str | None
    confidence: float
    method: Method


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    header_row: int
    headers: tuple[str | None, ...]
    assignments: tuple[ColumnAssignment, ...]
    missing_required: tuple[str, ...]
    needs_confirmation: tuple[str, ...]
    alternatives: tuple[tuple[str, tuple[int, ...]], ...]

    def columns(self) -> dict[str, int]:
        return {a.field: a.column for a in self.assignments}

    def to_dict(self) -> dict[str, Any]:
        return {
            "header_row": self.header_row,
            "headers": list(self.headers),
            "assignments": [
                {
                    "field": a.field,
                    "column": a.column,
                    "header": a.header,
                    "confidence": a.confidence,
                    "method": a.method,
                }
                for a in self.assignments
            ],
            "missing_required": list(self.missing_required),
            "needs_confirmation": list(self.needs_confirmation),
            "alternatives": {field: list(cols) for field, cols in self.alternatives},
        }


def _is_gstin_cell(value: object) -> bool:
    return isinstance(value, str) and GSTIN_PATTERN.match(normalize_gstin(value)) is not None


def _content_checks(config: ReconConfig) -> dict[str, Callable[[object], bool]]:
    return {
        "gstin": _is_gstin_cell,
        "date": lambda v: cell_date(v, config.date_formats) is not None,
        "money": lambda v: parse_money(v) is not None,
        "rate": lambda v: parse_rate(v) is not None,
    }


def _column_sample(table: RawTable, header_row: int, column: int) -> list[object]:
    sample: list[object] = []
    for row in table.rows[header_row + 1 :]:
        value = row[column]
        if value is not None:
            sample.append(value)
            if len(sample) == SAMPLE_CELLS:
                break
    return sample


def _build_mapping(
    header_row: int,
    headers: tuple[str | None, ...],
    assigned: Mapping[str, ColumnAssignment],
    alternatives: Mapping[str, tuple[int, ...]],
) -> ColumnMapping:
    assignments = tuple(assigned[name] for name in FIELD_NAMES if name in assigned)
    needs_confirmation = tuple(
        a.field
        for a in assignments
        if a.method != "user"
        and (a.confidence < CONFIDENT_SCORE - _EPSILON or alternatives.get(a.field))
    )
    return ColumnMapping(
        header_row=header_row,
        headers=headers,
        assignments=assignments,
        missing_required=tuple(
            spec.name for spec in REGISTER_FIELDS if spec.required and spec.name not in assigned
        ),
        needs_confirmation=needs_confirmation,
        alternatives=tuple(
            (name, alternatives[name])
            for name in FIELD_NAMES
            if name in assigned and alternatives.get(name)
        ),
    )


def propose_mapping(
    table: RawTable, config: ReconConfig, header_row: int | None = None
) -> ColumnMapping:
    if header_row is None:
        header_row = detect_header_row(table)
        if header_row is None:
            raise IngestError(HEADER_NOT_FOUND)
    elif not 0 <= header_row < len(table.rows):
        raise IngestError(HEADER_NOT_FOUND)

    headers = tuple(cell_text(cell) for cell in table.rows[header_row])
    scorer = _HeaderScorer()
    checks = _content_checks(config)
    fractions: dict[tuple[int, str], float | None] = {}
    samples: dict[int, list[object]] = {}

    def fraction(column: int, kind: str) -> float | None:
        key = (column, kind)
        if key not in fractions:
            if column not in samples:
                samples[column] = _column_sample(table, header_row, column)
            sample = samples[column]
            check = checks[kind]
            fractions[key] = sum(map(check, sample)) / len(sample) if sample else None
        return fractions[key]

    scores: dict[tuple[str, int], _Score] = {}
    for column, header in enumerate(headers):
        header_scores = scorer(header) if header is not None else {}
        for spec in REGISTER_FIELDS:
            best = header_scores.get(spec.name)
            if best is not None and spec.kind in checks:
                share = fraction(column, spec.kind)
                if share is not None and share < CONTRADICTION_FRACTION:
                    best = _Score(best.value / 2, best.method)
            content_may_win = best is None or best.value < CONTENT_ONLY_SCORE
            if spec.name in CONTENT_ONLY_FIELDS and content_may_win:
                share = fraction(column, spec.kind)
                if share is not None and share >= CONTENT_ONLY_FRACTION:
                    best = _Score(CONTENT_ONLY_SCORE, "content")
            if best is not None:
                scores[(spec.name, column)] = best

    ranked = sorted(
        scores.items(), key=lambda item: (-item[1].value, _FIELD_ORDER[item[0][0]], item[0][1])
    )
    assigned: dict[str, ColumnAssignment] = {}
    used_columns: set[int] = set()
    for (name, column), score in ranked:
        if score.value < MIN_ASSIGN_SCORE - _EPSILON:
            break
        if name in assigned or column in used_columns:
            continue
        assigned[name] = ColumnAssignment(name, column, headers[column], score.value, score.method)
        used_columns.add(column)

    alternatives: dict[str, tuple[int, ...]] = {}
    for name, assignment in assigned.items():
        runners_up = sorted(
            column
            for (field, column), score in scores.items()
            if field == name
            and column != assignment.column
            and score.value >= MIN_ASSIGN_SCORE - _EPSILON
            and assignment.confidence - score.value <= RUNNER_UP_MARGIN + _EPSILON
        )
        if runners_up:
            alternatives[name] = tuple(runners_up)

    return _build_mapping(header_row, headers, assigned, alternatives)


def apply_overrides(mapping: ColumnMapping, overrides: Mapping[str, int | None]) -> ColumnMapping:
    """Apply user choices: None unassigns a field, an int assigns that 0-based column."""
    assigned = {a.field: a for a in mapping.assignments}
    alternatives = dict(mapping.alternatives)
    for name, column in overrides.items():
        if name not in _FIELD_ORDER:
            raise IngestError(BAD_MAPPING)
        alternatives.pop(name, None)
        if column is None:
            assigned.pop(name, None)
            continue
        if not _valid_column(column, len(mapping.headers)):
            raise IngestError(BAD_MAPPING)
        assigned[name] = ColumnAssignment(name, column, mapping.headers[column], 1.0, "user")

    columns = [a.column for a in assigned.values()]
    if len(columns) != len(set(columns)):
        raise IngestError(BAD_MAPPING)
    return _build_mapping(mapping.header_row, mapping.headers, assigned, alternatives)


def _valid_column(column: object, width: int) -> bool:
    return isinstance(column, int) and not isinstance(column, bool) and 0 <= column < width
