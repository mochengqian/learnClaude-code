from __future__ import annotations

import unittest

from demo_app.message_tools import render_message


class MessageToolsTest(unittest.TestCase):
    def test_render_message_uses_shared_suffix(self) -> None:
        self.assertEqual("Hello, Ada!", render_message(" ada "))

    def test_render_message_title_cases_name(self) -> None:
        self.assertEqual("Hello, Grace Hopper!", render_message("grace hopper"))


if __name__ == "__main__":
    unittest.main()
