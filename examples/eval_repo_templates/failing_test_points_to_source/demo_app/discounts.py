from __future__ import annotations


def apply_discount_cents(price_cents: int, discount_percent: int) -> int:
    discount_cents = price_cents * discount_percent
    return price_cents - discount_cents
