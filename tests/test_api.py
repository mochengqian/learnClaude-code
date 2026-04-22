import subprocess
import sys
import tempfile
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


def init_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Runtime Tests"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )


class FakeModelClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, *, system_prompt: str, user_prompt: str):
        from repo_task_runtime import ModelResponse

        if not self.responses:
            raise AssertionError("No fake model responses remaining.")
        return ModelResponse(
            text=self.responses.pop(0),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 123},
        )


class ApiRuntimeTest(unittest.TestCase):
    def setUp(self):
        _ensure_fastapi()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name)
        init_git_repo(self.repo_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_client(self, responses=None):
        from fastapi.testclient import TestClient

        from repo_task_runtime import AgentRunner
        from repo_task_runtime.api import create_app

        agent_runner = None
        if responses is not None:
            agent_runner = AgentRunner(FakeModelClient(responses))
        return TestClient(create_app(agent_runner=agent_runner))

    def seed_todos(self, client, session_id):
        response = client.put(
            f"/sessions/{session_id}/todos",
            json={
                "todos": [
                    {"content": "Inspect", "status": "in_progress"},
                    {"content": "Fix", "status": "pending"},
                    {"content": "Test", "status": "pending"},
                ]
            },
        )
        self.assertEqual(200, response.status_code)

    def test_session_lifecycle_over_http(self):
        client = self.make_client()
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Fix a bug"},
        )
        self.assertEqual(200, response.status_code)
        session = response.json()["session"]
        session_id = session["session_id"]
        self.assertEqual("plan", session["permission_mode"])

        response = client.post(
            f"/sessions/{session_id}/plan",
            json={"plan_markdown": "1. Inspect\n2. Fix\n3. Test"},
        )
        self.assertEqual(200, response.status_code)

        response = client.post(f"/sessions/{session_id}/plan/approve")
        self.assertEqual(200, response.status_code)
        self.assertEqual("default", response.json()["session"]["permission_mode"])

        response = client.put(
            f"/sessions/{session_id}/todos",
            json={
                "todos": [
                    {"content": "Inspect", "status": "in_progress"},
                    {"content": "Fix", "status": "pending"},
                ]
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(response.json()["session"]["todos"]))

    def test_write_file_approval_flow_over_http(self):
        client = self.make_client()
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Fix a bug"},
        )
        session_id = response.json()["session"]["session_id"]
        client.post(
            f"/sessions/{session_id}/plan",
            json={"plan_markdown": "1. Inspect\n2. Fix\n3. Test"},
        )
        client.post(f"/sessions/{session_id}/plan/approve")

        response = client.post(
            f"/sessions/{session_id}/tools",
            json={
                "tool_type": "write_file",
                "relative_path": "notes.txt",
                "content": "approved\n",
            },
        )
        self.assertEqual(200, response.status_code)
        result = response.json()["result"]
        self.assertEqual("approval_required", result["status"])
        approval_id = result["approval_id"]

        response = client.post(
            f"/sessions/{session_id}/approvals/{approval_id}/resolve",
            json={"approve": True},
        )
        self.assertEqual(200, response.status_code)
        result = response.json()["result"]
        self.assertEqual("executed", result["status"])
        self.assertTrue((self.repo_path / "notes.txt").exists())
        self.assertIn("notes.txt", response.json()["session"]["latest_diff"])

    def test_file_patch_approval_flow_over_http(self):
        client = self.make_client()
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Fix a bug"},
        )
        session_id = response.json()["session"]["session_id"]
        client.post(
            f"/sessions/{session_id}/plan",
            json={"plan_markdown": "1. Inspect\n2. Fix\n3. Test"},
        )
        client.post(f"/sessions/{session_id}/plan/approve")

        response = client.post(
            f"/sessions/{session_id}/tools",
            json={
                "tool_type": "file_patch",
                "relative_path": "README.md",
                "expected_old_snippet": "hello\n",
                "new_snippet": "hello\npatched\n",
            },
        )
        self.assertEqual(200, response.status_code)
        result = response.json()["result"]
        self.assertEqual("approval_required", result["status"])
        approval_id = result["approval_id"]

        response = client.post(
            f"/sessions/{session_id}/approvals/{approval_id}/resolve",
            json={"approve": True},
        )
        self.assertEqual(200, response.status_code)
        result = response.json()["result"]
        self.assertEqual("executed", result["status"])
        self.assertEqual("hello\npatched\n", (self.repo_path / "README.md").read_text(encoding="utf-8"))
        self.assertIn("README.md", response.json()["session"]["latest_diff"])

    def test_begin_task_endpoint_resets_previous_task_state(self):
        client = self.make_client()
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Fix a bug"},
        )
        session_id = response.json()["session"]["session_id"]
        client.post(
            f"/sessions/{session_id}/plan",
            json={"plan_markdown": "1. Inspect\n2. Fix\n3. Test"},
        )
        client.post(f"/sessions/{session_id}/plan/approve")
        client.put(
            f"/sessions/{session_id}/todos",
            json={"todos": [{"content": "Inspect", "status": "in_progress"}]},
        )
        client.post(
            f"/sessions/{session_id}/tools",
            json={"tool_type": "read_file", "relative_path": "README.md"},
        )
        response = client.post(
            f"/sessions/{session_id}/tools",
            json={
                "tool_type": "write_file",
                "relative_path": "notes.txt",
                "content": "pending\n",
            },
        )
        self.assertEqual("approval_required", response.json()["result"]["status"])

        response = client.post(
            f"/sessions/{session_id}/task",
            json={"task_input": "Start a new task"},
        )

        self.assertEqual(200, response.status_code)
        session = response.json()["session"]
        self.assertEqual("Start a new task", session["task_input"])
        self.assertEqual("plan", session["permission_mode"])
        self.assertIsNone(session["plan"])
        self.assertEqual([], session["todos"])
        self.assertEqual("", session["latest_diff"])
        self.assertIsNone(session["latest_tool_result"])
        self.assertEqual([], session["pending_approvals"])
        self.assertEqual(
            ["task_received", "plan_mode_entered"],
            [event["event_type"] for event in session["timeline"]],
        )

    def test_tool_endpoint_accepts_string_command_payload(self):
        client = self.make_client()
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Inspect the repo"},
        )
        session_id = response.json()["session"]["session_id"]
        client.post(
            f"/sessions/{session_id}/plan",
            json={"plan_markdown": "1. Inspect\n2. Verify"},
        )
        client.post(f"/sessions/{session_id}/plan/approve")
        (self.repo_path / "hello world.txt").write_text("ok\n", encoding="utf-8")

        response = client.post(
            f"/sessions/{session_id}/tools",
            json={
                "tool_type": "shell",
                "command": 'find . -name "hello world.txt"',
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("executed", payload["result"]["status"])
        self.assertEqual(
            ["find", ".", "-name", "hello world.txt"],
            payload["result"]["data"]["command"],
        )
        self.assertIn("hello world.txt", payload["result"]["stdout"])

    def test_agent_plan_endpoint_populates_plan_and_todos(self):
        client = self.make_client(
            [
                (
                    '{"plan_markdown":"1. Inspect\\n2. Fix\\n3. Test",'
                    '"todos":['
                    '{"content":"Inspect code","status":"in_progress"},'
                    '{"content":"Fix bug","status":"pending"}'
                    "]}"
                )
            ]
        )
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Fix a bug"},
        )
        session_id = response.json()["session"]["session_id"]

        response = client.post(f"/sessions/{session_id}/agent/plan")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("gpt-5.4-mini-test", payload["agent"]["model"])
        self.assertEqual("1. Inspect\n2. Fix\n3. Test", payload["session"]["plan"])
        self.assertEqual(2, len(payload["session"]["todos"]))

    def test_agent_step_endpoint_executes_selected_tool(self):
        client = self.make_client(
            [
                (
                    '{"summary":"Read the README first.",'
                    '"action":"request_tool",'
                    '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                )
            ]
        )
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Fix a bug"},
        )
        session_id = response.json()["session"]["session_id"]
        client.post(
            f"/sessions/{session_id}/plan",
            json={"plan_markdown": "1. Inspect\n2. Fix\n3. Test"},
        )
        client.post(f"/sessions/{session_id}/plan/approve")
        self.seed_todos(client, session_id)

        response = client.post(f"/sessions/{session_id}/agent/step")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("request_tool", payload["agent"]["decision"]["action"])
        self.assertEqual("executed", payload["agent"]["tool_result"]["status"])
        self.assertEqual("hello\n", payload["agent"]["tool_result"]["data"]["content"])
        self.assertEqual(
            "read_file",
            payload["session"]["latest_tool_result"]["tool_name"],
        )
        self.assertEqual("completed", payload["session"]["todos"][0]["status"])
        self.assertEqual("in_progress", payload["session"]["todos"][1]["status"])
        self.assertEqual("pending", payload["session"]["todos"][2]["status"])

    def test_agent_loop_endpoint_stops_on_approval(self):
        client = self.make_client(
            [
                (
                    '{"summary":"Read the README first.",'
                    '"action":"request_tool",'
                    '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                ),
                (
                    '{"summary":"Patch the README with the fix note.",'
                    '"action":"request_tool",'
                    '"tool_request":{'
                    '"tool_type":"file_patch",'
                    '"relative_path":"README.md",'
                    '"expected_old_snippet":"hello\\n",'
                    '"new_snippet":"hello\\nfix applied\\n"'
                    "}}"
                ),
            ]
        )
        response = client.post(
            "/sessions",
            json={"repo_path": str(self.repo_path), "task_input": "Fix a bug"},
        )
        session_id = response.json()["session"]["session_id"]
        client.post(
            f"/sessions/{session_id}/plan",
            json={"plan_markdown": "1. Inspect\n2. Fix\n3. Test"},
        )
        client.post(f"/sessions/{session_id}/plan/approve")
        self.seed_todos(client, session_id)

        response = client.post(
            f"/sessions/{session_id}/agent/loop",
            json={"max_steps": 4},
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("approval_required", payload["agent"]["stop_reason"])
        self.assertEqual(2, payload["agent"]["steps_completed"])
        self.assertEqual(
            "approval_required",
            payload["agent"]["steps"][1]["tool_result"]["status"],
        )
        self.assertEqual("completed", payload["session"]["todos"][0]["status"])
        self.assertEqual("in_progress", payload["session"]["todos"][1]["status"])
        self.assertEqual("pending", payload["session"]["todos"][2]["status"])


if __name__ == "__main__":
    unittest.main()
