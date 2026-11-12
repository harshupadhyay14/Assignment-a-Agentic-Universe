import pytest

from solution.ledger.normalize import (norm_header, normalize_phone, parse_date, parse_decimal,
                                       parse_enum, parse_timestamp_us, rate4, us_to_ist)


@pytest.mark.parametrize("raw,expected", [
    ("9876543210", "+919876543210"), ("+91 98765 43210", "+919876543210"),
    ("(+91) 9876543210", "+919876543210"), ("91-98765-43210", "+919876543210"),
    ("09876543210", "+919876543210"), ("919876543210", "+919876543210"),
    ("5876543210", None),          # must start with 6-9
    ("987654321", None), ("98765432101", None), ("N/A", None), ("", None), (None, None),
    ("0919876543210", None),       # 13 digits
])
def test_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2026-12-01", "2026-12-01"), ("01/12/2026", "2026-12-01"), ("01-12-2026", "2026-12-01"),
    ("01-Dec-2026", "2026-12-01"), ("01-dEc-2026", "2026-12-01"),
    ("31/02/2026", None), ("2026-13-01", None), ("13/13/2026", None), ("garbage", None),
    ("03/04/2026", "2026-04-03"),   # day-first
])
def test_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("₹27,554", "27554.00"), ("Rs. 26505", "26505.00"), ("rs 100", "100.00"), ("INR 25301.4", "25301.40"),
    ("inr25301.456", "25301.46"), ("4737.14", "4737.14"), ("1,23,456", "123456.00"),
    ("N/A", None), ("-", None), ("twelve thousand", None), ("-5", None),
])
def test_decimal(raw, expected):
    assert parse_decimal(raw) == expected


def test_enum_and_header():
    assert parse_enum(" TA ", ["en", "ta"]) == "ta"
    assert parse_enum("Hindi", ["en", "hi"]) is None
    assert norm_header("  Mobile__No \t") == "mobile no"
    assert norm_header("policy_number") == "policy number"


def test_timestamps_and_naive_is_ist():
    z = parse_timestamp_us("2026-10-03T04:31:00Z")
    off = parse_timestamp_us("2026-10-03T10:01:00+05:30")
    naive = parse_timestamp_us("2026-10-03 10:01:00")
    assert z == off == naive
    assert us_to_ist(naive).isoformat() == "2026-10-03T10:01:00+05:30"
    assert parse_timestamp_us("nonsense") is None


def test_rate4_rounds_half_up():
    assert rate4(9, 16) == "0.5625" and rate4(1, 3) == "0.3333" and rate4(2, 3) == "0.6667"
    assert rate4(1, 1) == "1.0000" and rate4(0, 5) == "0.0000"
    assert rate4(1, 8) == "0.1250"
