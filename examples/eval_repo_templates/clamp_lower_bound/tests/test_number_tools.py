from __future__ import annotations

import unittest

from demo_app.number_tools import clamp


class NumberToolsTest(unittest.TestCase):
    def test_clamp_returns_lower_bound(self) -> None:
        self.assertEqual(3, clamp(1, 3, 8))

    def test_clamp_returns_upper_bound(self) -> None:
        self.assertEqual(8, clamp(10, 3, 8))

    def test_clamp_preserves_in_range_value(self) -> None:
        self.assertEqual(5, clamp(5, 3, 8))


if __name__ == "__main__":
    unittest.main()
