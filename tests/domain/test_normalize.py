import locale
from datetime import date, datetime
from decimal import Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from itclens.domain.config import load_config
from itclens.domain.normalize import (
    MAX_MONEY,
    normalize_invoice_no,
    parse_date,
    parse_money,
    plain_number_text,
    round_money,
    within_tolerance,
)

DEFAULT_FORMATS = load_config().date_formats


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("inv/0042", "INV0042"),
        ("Inv-42", "INV42"),
        ("  a b  c ", "ABC"),
        ("INV/007/A", "INV007A"),
        ("000123", "123"),
        ("0A1", "A1"),
        ("0000", "0"),
        ("00-00", "0"),
        ("0", "0"),
        ("--/", ""),
        ("", ""),
        (None, ""),
        (123, "123"),
        (Decimal("45"), "45"),
    ],
)
def test_invoice_no(raw: object, expected: str) -> None:
    assert normalize_invoice_no(raw) == expected


@pytest.mark.parametrize(
    ("raw", "stripped", "kept"),
    [
        ("INV-001/2024-25", "INV001", "INV001202425"),
        ("2024-25/INV-001", "INV001", "202425INV001"),
        ("INV 1 2024-2025", "INV1", "INV120242025"),
        ("INV_1_24-25", "INV1", "INV12425"),
        ("inv/1/24/25", "INV1", "INV12425"),
        ("INV/1/99-00", "INV1", "INV19900"),
        # suffix is tried first, and only one token is removed
        ("2023-24/1/2024-25", "2023241", "2023241202425"),
        # not consecutive years
        ("INV/1/2023-25", "INV1202325", "INV1202325"),
        ("INV/1/2025-24", "INV1202524", "INV1202524"),
        # YY-YYYY is not a recognised shape
        ("INV/1/24-2025", "INV1242025", "INV1242025"),
        # token not separated from the rest
        ("INV/12024-25", "INV1202425", "INV1202425"),
        # stripping would leave nothing
        ("2024-25", "202425", "202425"),
        ("2024-25/--", "202425", "202425"),
        # token in the middle is kept
        ("A/2024-25/7", "A2024257", "A2024257"),
    ],
)
def test_invoice_no_fy_affixes(raw: str, stripped: str, kept: str) -> None:
    assert normalize_invoice_no(raw, strip_fy_affixes=True) == stripped
    assert normalize_invoice_no(raw, strip_fy_affixes=False) == kept


invoice_inputs = st.one_of(
    st.none(),
    st.integers(),
    st.text(),
    st.text(alphabet="0123456789-/_ ABCinv."),
)


@given(raw=invoice_inputs, strip=st.booleans())
def test_invoice_no_idempotent(raw: object, strip: bool) -> None:
    once = normalize_invoice_no(raw, strip_fy_affixes=strip)
    assert normalize_invoice_no(once, strip_fy_affixes=strip) == once


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1,234.50", "1234.50"),
        ("1,23,456.78", "123456.78"),
        ("12,34,567", "1234567"),
        ("1,234,567", "1234567"),
        ("1,00,000", "100000"),
        ("₹1,000", "1000"),
        ("₹ 1,000", "1000"),
        ("Rs. 500", "500"),
        ("Rs 500", "500"),
        ("rs500", "500"),
        ("INR 2,500.00", "2500.00"),
        ("inr 7", "7"),
        ("(250.00)", "-250.00"),
        ("(₹1,234)", "-1234"),
        ("-500", "-500"),
        ("- 500", "-500"),
        ("+500", "500"),
        ("-₹10", "-10"),
        ("₹-10", "-10"),
        ("  42  ", "42"),
        (".5", "0.5"),
        ("7.", "7"),
        ("0.001", "0.001"),
        (10, "10"),
        (-3, "-3"),
        (Decimal("1.005"), "1.005"),
        (0.1, "0.1"),
        (2.675, "2.675"),
    ],
)
def test_parse_money(value: object, expected: str) -> None:
    result = parse_money(value)
    assert result == Decimal(expected)
    assert str(result) == str(Decimal(expected))


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        Decimal("NaN"),
        Decimal("Infinity"),
        "",
        "   ",
        "-",
        "₹",
        "Rs",
        "abc",
        "1,2,3",
        "12,34",
        "1,234,56",
        "1.2.3",
        "1.2,3",
        "(-5)",
        "--5",
        "+-5",
        "5-",
        "1e5",
        "NaN",
        "Infinity",
        "12 34",
        None,
        [1],
    ],
)
def test_parse_money_rejects(value: object) -> None:
    assert parse_money(value) is None


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("2.675", "2.68"),
        ("2.665", "2.67"),
        ("2.664", "2.66"),
        ("-2.675", "-2.68"),
        ("0.005", "0.01"),
        ("0.004", "0.00"),
        ("1", "1.00"),
    ],
)
def test_round_money_half_up(amount: str, expected: str) -> None:
    assert str(round_money(Decimal(amount))) == expected


@pytest.mark.parametrize(
    ("a", "b", "tolerance", "expected"),
    [
        ("100", "101", "1", True),
        ("100", "101.01", "1", False),
        ("101.01", "100", "1", False),
        ("100.004", "101.004", "1", True),
        ("100.00", "101.005", "1", False),
        ("100", "100", "0", True),
        ("100", "100.01", "0", False),
        ("100", "100.004", "0", True),
    ],
)
def test_within_tolerance(a: str, b: str, tolerance: str, expected: bool) -> None:
    assert within_tolerance(Decimal(a), Decimal(b), Decimal(tolerance)) is expected


DATE_CASES = [
    ("%d-%m-%Y", "15-04-2024"),
    ("%d/%m/%Y", "15/04/2024"),
    ("%d.%m.%Y", "15.04.2024"),
    ("%Y-%m-%d", "2024-04-15"),
    ("%Y/%m/%d", "2024/04/15"),
    ("%d-%b-%Y", "15-Apr-2024"),
    ("%d %b %Y", "15 apr 2024"),
    ("%d-%B-%Y", "15-April-2024"),
    ("%d %B %Y", "15 APRIL 2024"),
]


def test_date_cases_cover_default_formats() -> None:
    assert {fmt for fmt, _ in DATE_CASES} == set(DEFAULT_FORMATS)


@pytest.mark.parametrize(("fmt", "text"), DATE_CASES)
def test_parse_date_each_default_format(fmt: str, text: str) -> None:
    assert parse_date(text, [fmt]) == date(2024, 4, 15)
    assert parse_date(f"  {text} ", DEFAULT_FORMATS) == date(2024, 4, 15)


def test_parse_date_first_matching_format_wins() -> None:
    assert parse_date("01/02/2024", ["%d/%m/%Y", "%m/%d/%Y"]) == date(2024, 2, 1)
    assert parse_date("01/02/2024", ["%m/%d/%Y", "%d/%m/%Y"]) == date(2024, 1, 2)


def test_parse_date_objects() -> None:
    assert parse_date(date(2024, 4, 15), DEFAULT_FORMATS) == date(2024, 4, 15)
    result = parse_date(datetime(2024, 4, 15, 13, 30), DEFAULT_FORMATS)
    assert result == date(2024, 4, 15)
    assert type(result) is date


@pytest.mark.parametrize(
    "value",
    [
        "31-02-2024",
        "29-02-2023",
        "2024-13-01",
        "15-Foo-2024",
        "15 Sept 2024",
        "15-Apr-April-2024",
        "15-January-2024 x",
        "",
        "   ",
        None,
        20240415,
        45397.0,
    ],
)
def test_parse_date_rejects(value: object) -> None:
    assert parse_date(value, DEFAULT_FORMATS) is None


def test_parse_date_month_name_must_match_directive() -> None:
    assert parse_date("15-April-2024", ["%d-%b-%Y"]) is None
    assert parse_date("15-Apr-2024", ["%d-%B-%Y"]) is None


def test_parse_date_skips_locale_dependent_formats() -> None:
    assert parse_date("Mon 15-04-2024", ["%a %d-%m-%Y"]) is None
    assert parse_date("15 Apr Apr 2024", ["%d %b %b %Y"]) is None


def test_parse_date_handles_literal_percent() -> None:
    assert parse_date("15%b04%2024", ["%d%%b%m%%%Y"]) == date(2024, 4, 15)


NON_ENGLISH_LOCALES = ["de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8", "hi_IN.UTF-8", "ru_RU.UTF-8"]
MONTH_TEXTS = ["15 Mar 2024", "15 March 2024", "01-Dec-2023", "31-May-2024", "1 Oct 2024"]


def test_month_names_do_not_depend_on_locale() -> None:
    baseline = [parse_date(text, DEFAULT_FORMATS) for text in MONTH_TEXTS]
    assert None not in baseline

    previous = locale.setlocale(locale.LC_ALL)
    tried = 0
    try:
        for name in NON_ENGLISH_LOCALES:
            try:
                locale.setlocale(locale.LC_ALL, name)
            except locale.Error:
                continue
            tried += 1
            assert [parse_date(text, DEFAULT_FORMATS) for text in MONTH_TEXTS] == baseline
    finally:
        locale.setlocale(locale.LC_ALL, previous)
    if not tried:
        pytest.skip("no non-English locale installed")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1.0, "1"),
        (1001.0, "1001"),
        (-0.0, "0"),
        (Decimal("1001.00"), "1001"),
        (Decimal("1E+3"), "1000"),
        (Decimal("12.50"), "1250"),
        (1.5, "15"),
        (1e20, "100000000000000000000"),
        (1e-7, "1"),
        (7, "7"),
        (True, "TRUE"),
    ],
)
def test_invoice_no_numeric_input(raw: object, expected: str) -> None:
    assert normalize_invoice_no(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (1001, "1001"),
        (Decimal("1001.00"), "1001"),
        (Decimal("2.5E+2"), "250"),
        (1.25, "1.25"),
        (1e-7, "0.0000001"),
        (Decimal("0.10"), "0.10"),
        (float("nan"), "NaN"),
    ],
)
def test_plain_number_text(value: int | float | Decimal, expected: str) -> None:
    assert plain_number_text(value) == expected


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (Decimal("9999999999999.99"), True),
        (Decimal("-9999999999999.99"), True),
        ("99,99,99,99,99,999.99", True),
        (Decimal("1e13"), False),
        (Decimal("-1e13"), False),
        (10**13, False),
        (1e13, False),
        ("10000000000000", False),
        ("1." + "0" * 38, True),
        ("1." + "0" * 39, False),
        ("  " + "1." + "0" * 38 + "  ", True),
    ],
)
def test_parse_money_cap(value: object, accepted: bool) -> None:
    assert (parse_money(value) is not None) is accepted


def test_max_money_constant() -> None:
    assert Decimal("1e13") == MAX_MONEY


money_inputs = st.one_of(
    st.decimals(allow_nan=True, allow_infinity=True),
    st.floats(),
    st.integers(),
    st.text(),
    st.text(alphabet="0123456789.,-+() Rs₹INR", max_size=45),
    st.from_regex(r"-?[0-9]{1,30}(\.[0-9]{1,30})?E[+-]?[0-9]{1,7}", fullmatch=True).map(Decimal),
)


@given(value=money_inputs)
def test_round_money_never_raises_on_parsed_values(value: object) -> None:
    parsed = parse_money(value)
    if parsed is not None:
        rounded = round_money(parsed)
        assert rounded.copy_abs() <= MAX_MONEY


def test_round_money_ignores_caller_context() -> None:
    with localcontext() as context:
        context.prec = 3
        assert str(round_money(Decimal("123456.785"))) == "123456.79"
