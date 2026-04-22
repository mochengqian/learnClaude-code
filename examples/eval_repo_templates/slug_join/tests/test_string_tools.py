from __future__ import annotations

import unittest

from demo_app.string_tools import slugify_title


class StringToolsTest(unittest.TestCase):
    def test_slugify_title_uses_hyphens(self) -> None:
        self.assertEqual("hello-world", slugify_title("Hello World"))

    def test_slugify_title_trims_extra_spaces(self) -> None:
        self.assertEqual("ship-fast", slugify_title("  Ship   Fast  "))


if __name__ == "__main__":
    unittest.main()
