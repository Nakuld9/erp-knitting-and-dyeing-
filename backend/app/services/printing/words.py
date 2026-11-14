"""Indian-system amount-in-words (rupees + paise)."""
from __future__ import annotations

from decimal import Decimal

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")).strip()


def _three(n: int) -> str:
    h, rest = divmod(n, 100)
    out = []
    if h:
        out.append(_ONES[h] + " hundred")
    if rest:
        out.append(_two(rest))
    return " ".join(out)


def _int_words(n: int) -> str:
    """Indian grouping: crore, lakh, thousand, hundred."""
    if n == 0:
        return "zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(_int_words(crore) + " crore")
    if lakh:
        parts.append(_two(lakh) + " lakh")
    if thousand:
        parts.append(_two(thousand) + " thousand")
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def amount_in_words(value, currency: str = "INR") -> str:
    """e.g. 47200.00 -> 'Forty-seven thousand two hundred rupees only'."""
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return ""
    sign = "minus " if d < 0 else ""
    d = abs(d)
    rupees = int(d)
    paise = int((d - rupees) * 100)
    unit = "rupees" if currency == "INR" else currency.lower()
    words = _int_words(rupees) + f" {unit}"
    if paise:
        words += " and " + _two(paise) + " paise"
    words += " only"
    out = (sign + words).strip()
    return out[:1].upper() + out[1:]
