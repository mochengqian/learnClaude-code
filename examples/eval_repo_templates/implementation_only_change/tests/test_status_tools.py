from __future__ import annotations

import unittest

from demo_app.status_tools import format_status_label


class StatusToolsTest(unittest.TestCase):
    def test_format_status_label_uses_hyphen_separator(self) -> None:
        self.assertEqual("status:in-progress", format_status_label(" In Progress "))

    def test_format_status_label_preserves_single_word_status(self) -> None:
        self.assertEqual("status:done", format_status_label("done"))


if __name__ == "__main__":
    unittest.main()
