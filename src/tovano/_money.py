"""Money helpers. The API sends integer micro-dollars: 1 USD = 1_000_000."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

__all__ = ["MICRO_PER_USD", "format_usd", "usd_to_micro", "micro_to_usd"]

MICRO_PER_USD = 1_000_000


def format_usd(micro: int) -> str:
    """Formats micro-dollars, keeping sub-cent precision when there is some.

    >>> format_usd(84_120_000), format_usd(2_500), format_usd(-1_000_000)
    ('$84.12', '$0.0025', '-$1.00')
    """
    micro = int(micro)
    sign = "-" if micro < 0 else ""
    whole, frac = divmod(abs(micro), MICRO_PER_USD)
    digits = f"{frac:06d}".rstrip("0").ljust(2, "0")
    return f"{sign}${whole:,}.{digits}"


def usd_to_micro(usd: Union[int, float, str, Decimal]) -> int:
    """Dollars to micro-dollars, rounded half up: ``usd_to_micro("12.50") == 12_500_000``."""
    return int((Decimal(str(usd)) * MICRO_PER_USD).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def micro_to_usd(micro: int) -> Decimal:
    """Micro-dollars to an exact ``Decimal`` amount of dollars."""
    return Decimal(int(micro)) / MICRO_PER_USD
