import dataclasses
from datetime import date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from itclens.domain.period import ReturnPeriod


def test_parse_and_format() -> None:
    period = ReturnPeriod.parse("042024")
    assert period == ReturnPeriod(year=2024, month=4)
    assert str(period) == "042024"
    assert ReturnPeriod.parse(" 122099 ") == ReturnPeriod(year=2099, month=12)


@given(year=st.integers(2000, 2100), month=st.integers(1, 12))
def test_round_trip(year: int, month: int) -> None:
    period = ReturnPeriod(year=year, month=month)
    assert ReturnPeriod.parse(str(period)) == period


@pytest.mark.parametrize(
    "text",
    [
        "",
        "132024",
        "002024",
        "041999",
        "042101",
        "42024",
        "0420245",
        "04-2024",
        "ab2024",
        "٠٤٢٠٢٤",
        None,
        42024,
        b"042024",
    ],
)
def test_parse_invalid(text: object) -> None:
    assert ReturnPeriod.parse(text) is None


def test_from_date() -> None:
    assert ReturnPeriod.from_date(date(2024, 4, 30)) == ReturnPeriod(2024, 4)
    assert ReturnPeriod.from_date(datetime(2025, 1, 1, 9, 0)) == ReturnPeriod(2025, 1)


def test_ordering() -> None:
    periods = [ReturnPeriod(2025, 3), ReturnPeriod(2024, 12), ReturnPeriod(2025, 1)]
    assert sorted(periods) == [ReturnPeriod(2024, 12), ReturnPeriod(2025, 1), ReturnPeriod(2025, 3)]
    assert ReturnPeriod(2024, 12) < ReturnPeriod(2025, 1)


def test_label() -> None:
    assert ReturnPeriod(2024, 4).label == "Apr 2024"
    assert ReturnPeriod(2099, 12).label == "Dec 2099"
    assert ReturnPeriod(2000, 1).label == "Jan 2000"


@pytest.mark.parametrize(
    ("year", "month", "bad"),
    [
        (2024, 13, "13"),
        (2024, 0, None),
        (1999, 4, "1999"),
        (2101, 4, "2101"),
        (2024, True, None),
        ("2024", 4, None),
        (2024, 4.0, None),
    ],
)
def test_invalid_values_raise_without_echo(year: object, month: object, bad: str | None) -> None:
    with pytest.raises(ValueError) as excinfo:
        ReturnPeriod(year=year, month=month)  # type: ignore[arg-type]
    if bad is not None:
        assert bad not in str(excinfo.value)


def test_frozen() -> None:
    period = ReturnPeriod(2024, 4)
    with pytest.raises(dataclasses.FrozenInstanceError):
        period.month = 5  # type: ignore[misc]
