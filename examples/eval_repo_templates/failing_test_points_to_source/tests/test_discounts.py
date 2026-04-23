from __future__ import annotations

import unittest

from demo_app.discounts import apply_discount_cents


class DiscountsTest(unittest.TestCase):
    def test_apply_discount_uses_percent_not_whole_number(self) -> None:
        self.assertEqual(800, apply_discount_cents(1000, 20))

    def test_apply_discount_allows_zero_percent(self) -> None:
        self.assertEqual(1250, apply_discount_cents(1250, 0))


if __name__ == "__main__":
    unittest.main()
