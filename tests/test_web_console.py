import sys
import unittest
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / ".vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))


def _ensure_fastapi():
    try:
        from fastapi.testclient import TestClient  # noqa: F401
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest("fastapi is not installed") from exc


class WebConsoleTest(unittest.TestCase):
    def setUp(self):
        _ensure_fastapi()
        from fastapi.testclient import TestClient

        from repo_task_runtime.api import create_app

        self.client = TestClient(create_app())

    def test_root_serves_console_html(self):
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn("Minimal Control Console", response.text)
        self.assertIn("/assets/app.js", response.text)
        self.assertIn("Draft Plan With Model", response.text)
        self.assertIn("Run Agent Step", response.text)
        self.assertIn("Run Agent Loop", response.text)

    def test_assets_are_served(self):
        js_response = self.client.get("/assets/app.js")
        css_response = self.client.get("/assets/styles.css")
        self.assertEqual(200, js_response.status_code)
        self.assertEqual(200, css_response.status_code)
        self.assertIn("const state", js_response.text)
        self.assertIn("agent-plan-button", js_response.text)
        self.assertIn("agent-step-button", js_response.text)
        self.assertIn("agent-loop-button", js_response.text)
        self.assertNotIn('.split(" ")', js_response.text)
        self.assertIn("--accent", css_response.text)
