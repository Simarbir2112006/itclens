import pytest
from hypothesis import given
from hypothesis import strategies as st

from itclens.domain.config import load_config
from itclens.domain.gstin import (
    BASE36_ALPHABET,
    GstinFailure,
    compute_check_char,
    normalize_gstin,
    pan_of,
    state_code_of,
    validate_gstin,
    with_check_char,
)

STATE_CODES = load_config().valid_state_codes

# Widely circulated sample GSTIN; checked by hand against the checksum algorithm.
SAMPLE = "27AAPFU0939F1ZV"

SYNTHETIC_BODIES = [
    "01ABCDE1234F1Z",
    "07PQRST5678K2Z",
    "19LMNOP0001Q9Z",
    "27ZZZZZ9999ZZZ",
    "29AAAAA0000A1Z",
    "33BCDEF4321GAZ",
    "97XYZAB1111C1Z",
    "99CENTR0001J1Z",
]


def test_sample_gstin_passes_checksum() -> None:
    assert compute_check_char(SAMPLE[:14]) == SAMPLE[14]
    result = validate_gstin(SAMPLE, STATE_CODES)
    assert result.is_valid
    assert result.failure is None
    assert result.state_code == "27"
    assert result.pan == "AAPFU0939F"


@pytest.mark.parametrize("body", SYNTHETIC_BODIES)
def test_synthetic_gstins_are_valid(body: str) -> None:
    gstin = with_check_char(body)
    assert len(gstin) == 15
    result = validate_gstin(gstin, STATE_CODES)
    assert result.is_valid, result.failure
    assert result.state_code == body[:2]


@pytest.mark.parametrize(
    ("value", "failure"),
    [
        (None, GstinFailure.EMPTY),
        ("", GstinFailure.EMPTY),
        ("   ", GstinFailure.EMPTY),
        (27, GstinFailure.EMPTY),
        ("27AAPFU0939F1Z", GstinFailure.LENGTH),
        ("27AAPFU0939F1ZVX", GstinFailure.LENGTH),
        ("27AAPFU0939F1XV", GstinFailure.STRUCTURE),
        ("27AAPFU0939F0ZV", GstinFailure.STRUCTURE),
        ("2AAAPFU0939F1ZV", GstinFailure.STRUCTURE),
        ("27AAPF10939F1ZV", GstinFailure.STRUCTURE),
        ("27AAPFU0939F1Z-", GstinFailure.STRUCTURE),
        ("27AAPFU0939F1ZA", GstinFailure.CHECKSUM),
    ],
)
def test_failure_paths(value: object, failure: GstinFailure) -> None:
    result = validate_gstin(value, STATE_CODES)
    assert not result.is_valid
    assert result.failure is failure


def test_unknown_state_code_when_set_given() -> None:
    gstin = with_check_char("00AAPFU0939F1Z")
    result = validate_gstin(gstin, STATE_CODES)
    assert result.failure is GstinFailure.STATE_CODE
    assert result.state_code == "00"
    assert result.pan == "AAPFU0939F"
    assert validate_gstin(gstin).is_valid


def test_checksum_failure_still_reports_parts() -> None:
    result = validate_gstin("27AAPFU0939F1ZA")
    assert result.state_code == "27"
    assert result.pan == "AAPFU0939F"


def test_structure_failure_has_no_parts() -> None:
    result = validate_gstin("27AAPFU0939F1XV")
    assert result.state_code is None
    assert result.pan is None


def test_lowercase_and_spaces_are_normalized() -> None:
    result = validate_gstin("  27aapfu0939f1zv\n", STATE_CODES)
    assert result.is_valid
    assert result.normalized == SAMPLE


def test_normalize_keeps_inner_characters() -> None:
    assert normalize_gstin(" 27 aapfu ") == "27 AAPFU"
    assert normalize_gstin(None) == ""
    assert normalize_gstin(123) == ""


def test_slices() -> None:
    assert state_code_of(SAMPLE) == "27"
    assert pan_of(SAMPLE) == "AAPFU0939F"


def _fixed(alphabet: str, size: int) -> st.SearchStrategy[str]:
    return st.text(alphabet=alphabet, min_size=size, max_size=size)


DIGITS = "0123456789"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

gstin_bodies = st.tuples(
    _fixed(DIGITS, 2),
    _fixed(LETTERS, 5),
    _fixed(DIGITS, 4),
    _fixed(LETTERS, 1),
    _fixed("123456789" + LETTERS, 1),
).map(lambda parts: "".join(parts) + "Z")


@given(body=gstin_bodies, position=st.integers(0, 14), data=st.data())
def test_single_substitution_is_caught(body: str, position: int, data: st.DataObject) -> None:
    gstin = with_check_char(body)
    assert validate_gstin(gstin).is_valid
    replacement = data.draw(st.sampled_from([c for c in BASE36_ALPHABET if c != gstin[position]]))
    mutated = gstin[:position] + replacement + gstin[position + 1 :]
    assert not validate_gstin(mutated).is_valid


def test_every_single_substitution_of_sample_is_caught() -> None:
    for position in range(15):
        for char in BASE36_ALPHABET:
            if char != SAMPLE[position]:
                mutated = SAMPLE[:position] + char + SAMPLE[position + 1 :]
                assert not validate_gstin(mutated).is_valid


@pytest.mark.parametrize(
    "bad",
    [
        "27AAPFU0939F1",
        "27AAPFU0939F1ZV",
        "27aapfu0939f1z",
        "27AAPFU0939F1#",
        "27AAPFU 0939F1",
    ],
)
def test_compute_check_char_rejects_bad_input_without_echo(bad: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        compute_check_char(bad)
    message = str(excinfo.value)
    assert bad not in message
    assert bad.upper()[2:7] not in message.upper()


def test_compute_check_char_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        compute_check_char(None)  # type: ignore[arg-type]
