"""Pure value-normalisation helpers. No I/O, no database: easy to unit test."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def norm_header(h: str) -> str:
    """lower-case, trim, collapse every run of '_' / whitespace to one space."""
    return re.sub(r"[_\s]+", " ", h.strip().lower()).strip()


def normalize_phone(raw) -> str | None:
    """Indian mobile -> '+91XXXXXXXXXX', or None if invalid/missing."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "dmy"),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), "dmy"),
    (re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$"), "dMy"),
]


def parse_date(raw: str) -> str | None:
    """Day-first date parsing. Returns ISO 'YYYY-MM-DD' or None."""
    s = raw.strip()
    for pat, kind in _DATE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        a, b, c = m.groups()
        try:
            if kind == "ymd":
                y, mo, d = int(a), int(b), int(c)
            elif kind == "dmy":
                d, mo, y = int(a), int(b), int(c)
            else:
                d, y = int(a), int(c)
                mo = _MONTHS.get(b.lower())
                if mo is None:
                    return None
            return date(y, mo, d).isoformat()
        except ValueError:  # impossible date, e.g. 31/02 or month 13
            return None
    return None


_DEC_RE = re.compile(r"^(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)$", re.IGNORECASE)


def parse_decimal(raw: str) -> str | None:
    """Currency-ish amount -> string with 2 decimal places (>= 0), or None."""
    m = _DEC_RE.match(raw.strip())
    if not m:
        return None
    try:
        v = Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_enum(raw: str, values: list[str]) -> str | None:
    v = raw.strip().lower()
    allowed = {x.lower() for x in values}
    return v if v in allowed else None


def parse_timestamp_us(raw) -> int | None:
    """Event timestamp -> integer microseconds since the UTC epoch.

    Z / +hh:mm offsets are honoured; naive timestamps are IST (per spec).
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    delta = dt - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def us_to_ist(us: int) -> datetime:
    return (_EPOCH + timedelta(microseconds=us)).astimezone(IST)


def rate4(num: int, den: int) -> str:
    """num/den with exactly 4 decimals, round-half-up, using integer maths."""
    scaled = (num * 20000 + den) // (2 * den)  # round(num/den * 10000)
    return f"{scaled // 10000}.{scaled % 10000:04d}"
