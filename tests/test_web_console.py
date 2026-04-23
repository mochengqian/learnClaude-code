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
        self.assertIn('id="snapshot-summary"', response.text)
        self.assertIn('id="latest-result-summary"', response.text)
        self.assertIn("Plan mode, todo progress", response.text)
        self.assertIn("latest successful test status", response.text)
        self.assertIn("current test evidence", response.text)
        self.assertIn("Key event summaries appear above raw payloads", response.text)

    def test_assets_are_served(self):
        js_response = self.client.get("/assets/app.js")
        css_response = self.client.get("/assets/styles.css")
        self.assertEqual(200, js_response.status_code)
        self.assertEqual(200, css_response.status_code)
        self.assertIn("const state", js_response.text)
        self.assertIn("agent-plan-button", js_response.text)
        self.assertIn("agent-step-button", js_response.text)
        self.assertIn("agent-loop-button", js_response.text)
        self.assertIn("createApprovalKindBadge", js_response.text)
        self.assertIn("createStateBadge", js_response.text)
        self.assertIn("appendPlanTodoSummary", js_response.text)
        self.assertIn("summarizePlanMode", js_response.text)
        self.assertIn("todo_counts", js_response.text)
        self.assertIn("active_todo", js_response.text)
        self.assertIn("next_pending_todo", js_response.text)
        self.assertIn("buildTimelineSummary", js_response.text)
        self.assertIn("timelineTitle", js_response.text)
        self.assertIn("diff_updated", js_response.text)
        self.assertIn("local_test_completed", js_response.text)
        self.assertIn('createMetaPill("event"', js_response.text)
        self.assertIn("approval_kind", js_response.text)
        self.assertIn("latestResultSummary", js_response.text)
        self.assertIn("latest_successful_test", js_response.text)
        self.assertIn("latest_diff", js_response.text)
        self.assertNotIn('.split(" ")', js_response.text)
        self.assertIn("--accent", css_response.text)
        self.assertIn(".kind-badge", css_response.text)
        self.assertIn(".state-badge", css_response.text)
        self.assertIn(".state-good", css_response.text)
        self.assertIn(".todo-pill", css_response.text)
        self.assertIn(".summary-stack", css_response.text)
        self.assertIn(".timeline-summary", css_response.text)
