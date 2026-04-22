from __future__ import annotations

import unittest

from demo_app.text_tools import compact_whitespace


class TextToolsTest(unittest.TestCase):
    def test_compact_whitespace_trims_edges(self) -> None:
        self.assertEqual("hello world", compact_whitespace("  hello   world  "))

    def test_compact_whitespace_collapses_all_whitespace(self) -> None:
        self.assertEqual(
            "hello world again",
            compact_whitespace("hello\nworld\tagain"),
        )


if __name__ == "__main__":
    unittest.main()
