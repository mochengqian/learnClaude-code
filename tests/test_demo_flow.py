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


class DemoFlowTest(unittest.TestCase):
    def setUp(self):
        _ensure_fastapi()
        from fastapi.testclient import TestClient

        from repo_task_runtime.api import create_app

        self.client = TestClient(create_app())

    def test_demo_repo_bugfix_flow(self):
        response = self.client.post("/demo/setup")
        self.assertEqual(200, response.status_code)
        demo = response.json()["demo"]
        repo_path = Path(demo["repo_path"])
        self.assertTrue(repo_path.exists())

        response = self.client.post(
            "/sessions",
            json={"repo_path": demo["repo_path"], "task_input": demo["task_input"]},
        )
        self.assertEqual(200, response.status_code)
        session_id = response.json()["session"]["session_id"]

        self.client.post(
            f"/sessions/{session_id}/plan",
            json={
                "plan_markdown": (
                    "1. Read the failing module and tests.\n"
                    "2. Apply the smallest safe fix.\n"
                    "3. Run the local unittest suite."
                )
            },
        )
        self.client.post(f"/sessions/{session_id}/plan/approve")
        self.client.put(
            f"/sessions/{session_id}/todos",
            json={
                "todos": [
                    {"content": "Read the failing module", "status": "in_progress"},
                    {"content": "Fix the slug join", "status": "pending"},
                    {"content": "Run tests", "status": "pending"},
                ]
            },
        )

        response = self.client.post(
            f"/sessions/{session_id}/tools",
            json={"tool_type": "read_file", "relative_path": "demo_app/string_tools.py"},
        )
        self.assertEqual(200, response.status_code)
        original_content = response.json()["result"]["data"]["content"]
        self.assertIn('"_".join', original_content)

        response = self.client.post(
            f"/sessions/{session_id}/tools",
            json={
                "tool_type": "run_test",
                "command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json()["result"]["exit_code"])

        fixed_content = original_content.replace('"_".join', '"-".join')
        response = self.client.post(
            f"/sessions/{session_id}/tools",
            json={
                "tool_type": "write_file",
                "relative_path": "demo_app/string_tools.py",
                "content": fixed_content,
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("approval_required", response.json()["result"]["status"])
        approval_id = response.json()["result"]["approval_id"]

        response = self.client.post(
            f"/sessions/{session_id}/approvals/{approval_id}/resolve",
            json={"approve": True},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("executed", response.json()["result"]["status"])

        response = self.client.post(
            f"/sessions/{session_id}/tools",
            json={
                "tool_type": "run_test",
                "command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, response.json()["result"]["exit_code"])

        response = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(200, response.status_code)
        session = response.json()["session"]
        self.assertIn("demo_app/string_tools.py", session["latest_diff"])
        event_types = [event["event_type"] for event in session["timeline"]]
        self.assertIn("approval_requested", event_types)
        self.assertIn("approval_granted", event_types)
        self.assertIn("local_test_completed", event_types)
