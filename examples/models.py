"""python examples/models.py — the catalogue with prices."""

from _client import client

from tovano import format_usd

for m in client.models.list():
    prices = ", ".join(f"{format_usd(p.price_micro)} / {p.unit}" for p in m.prices)
    print(f"{m.id:<30} {m.type:<6} {prices}")
