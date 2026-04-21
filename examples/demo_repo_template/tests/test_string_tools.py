import unittest

from demo_app.string_tools import slugify_title


class StringToolsTest(unittest.TestCase):
    def test_slugify_title_normalizes_whitespace(self) -> None:
        self.assertEqual(
            slugify_title("  Hello    Repo Task  "),
            "hello-repo-task",
        )

    def test_slugify_title_handles_single_word(self) -> None:
        self.assertEqual(slugify_title("Runtime"), "runtime")


if __name__ == "__main__":
    unittest.main()
