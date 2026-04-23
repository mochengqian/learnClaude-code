import subprocess
import tempfile
import unittest
from pathlib import Path

from repo_task_runtime import (
    AgentRunner,
    FileReadRequest,
    ModelResponse,
    TaskWorkbench,
    TestCommandRequest,
    TodoItem,
    TodoStatus,
)


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
    tests_dir = repo_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text(
        "import unittest\n\n"
        "class SmokeTest(unittest.TestCase):\n"
        "    def test_smoke(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )


class FakeModelClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if not self.responses:
            raise AssertionError("No fake model responses remaining.")
        return ModelResponse(
            text=self.responses.pop(0),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 123},
        )


class AgentRunnerTest(unittest.TestCase):
    def make_session(self):
        temp_dir = tempfile.TemporaryDirectory()
        repo_path = Path(temp_dir.name)
        init_git_repo(repo_path)
        session = TaskWorkbench().create_session(repo_path)
        session.begin_task("Fix the local slug bug.")
        return temp_dir, session

    def seed_todos(self, session):
        session.replace_todos(
            [
                TodoItem(content="Inspect the failing code", status=TodoStatus.IN_PROGRESS),
                TodoItem(content="Fix the slug join", status=TodoStatus.PENDING),
                TodoItem(content="Run tests", status=TodoStatus.PENDING),
            ]
        )

    def test_draft_plan_updates_session_plan_and_todos(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"plan_markdown":"1. Inspect\\n2. Fix\\n3. Test",'
                        '"todos":['
                        '{"content":"Inspect the failing code","status":"in_progress"},'
                        '{"content":"Fix the slug join","status":"pending"},'
                        '{"content":"Run tests","status":"pending"}'
                        "]}"
                    )
                ]
            )
        )

        draft = runner.draft_plan(session)

        self.assertEqual("1. Inspect\n2. Fix\n3. Test", draft.plan_markdown)
        self.assertEqual("1. Inspect\n2. Fix\n3. Test", session.plan)
        self.assertEqual(3, len(session.todos))
        self.assertEqual("in_progress", session.todos[0].status.value)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn("agent_plan_drafted", event_types)

    def test_run_next_step_keeps_file_patch_requests_inside_approval_flow(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        read_result = session.request_tool(FileReadRequest(relative_path="README.md"))
        self.assertEqual("executed", read_result.status)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Patch the README file with the fix note.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"README.md",'
                        '"expected_old_snippet":"hello\\n",'
                        '"new_snippet":"hello\\nfix applied\\n"'
                        "}}"
                    )
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertIsNotNone(outcome.tool_result)
        self.assertEqual("approval_required", outcome.tool_result.status)
        self.assertEqual(1, len(session.pending_approvals))
        self.assertEqual("in_progress", session.todos[0].status.value)
        self.assertEqual("pending", session.todos[1].status.value)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn("agent_step_decided", event_types)
        self.assertIn("approval_requested", event_types)

    def test_run_next_step_advances_todos_after_successful_execution(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the README first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                    )
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("completed", session.todos[0].status.value)
        self.assertEqual("in_progress", session.todos[1].status.value)
        self.assertEqual("pending", session.todos[2].status.value)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn("agent_todos_synced", event_types)

    def test_finish_only_completes_current_todo_without_promoting_next_one(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        test_result = session.request_tool(
            TestCommandRequest(
                command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v")
            )
        )
        self.assertEqual(0, test_result.exit_code)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"The task is done.",'
                        '"action":"finish"}'
                    )
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("finish", outcome.decision.action)
        self.assertIsNone(outcome.tool_result)
        self.assertEqual("completed", session.todos[0].status.value)
        self.assertEqual("pending", session.todos[1].status.value)
        self.assertEqual("pending", session.todos[2].status.value)

    def test_run_next_step_retries_invalid_tool_request_once(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the file first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file"}}'
                    ),
                    (
                        '{"summary":"Read the README first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn("agent_step_output_invalid", event_types)
        self.assertIn("agent_step_output_retry_requested", event_types)
        self.assertIn("agent_step_output_repaired", event_types)

    def test_finish_without_successful_test_is_repaired_into_run_test(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    '{"summary":"The task is done.","action":"finish"}',
                    (
                        '{"summary":"Run local tests before finishing.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"run_test",'
                        '"command":["python3","-m","unittest","discover","-s","tests","-v"]'
                        "}}"
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("run_test", outcome.tool_result.tool_name)
        self.assertEqual(0, outcome.tool_result.exit_code)
        self.assertTrue(session.has_successful_test_for_current_state())
        event_types = [event.event_type for event in session.timeline]
        self.assertIn("agent_step_output_invalid", event_types)
        self.assertIn("agent_step_output_repaired", event_types)

    def test_run_next_step_retries_directory_path_once(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Inspect the tests directory.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"tests"}}'
                    ),
                    (
                        '{"summary":"Read the README first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        event_payloads = [
            event.payload for event in session.timeline if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any("directory path for read_file" in payload.get("error", "") for payload in event_payloads)
        )

    def test_run_next_step_retries_missing_repo_file_once(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "number_tools.py").write_text(
            "def clamp(value: int, lower: int, upper: int) -> int:\n    return value\n",
            encoding="utf-8",
        )
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the clamp helper.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"clamp.py"}}'
                    ),
                    (
                        '{"summary":"Read the number helper instead.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/number_tools.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        self.assertEqual("demo_app/number_tools.py", outcome.tool_result.data["relative_path"])
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any("missing repo file for read_file" in payload.get("error", "") for payload in event_payloads)
        )

    def test_run_next_step_retries_missing_relative_path_once(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            "def slugify_title(value: str) -> str:\n    return value\n",
            encoding="utf-8",
        )
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the implementation first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file"}}'
                    ),
                    (
                        '{"summary":"Read the slug helper.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/string_tools.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        self.assertEqual(
            "demo_app/string_tools.py",
            outcome.tool_result.data["relative_path"],
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any(
                "relative_path is required for read_file" in payload.get("error", "")
                for payload in event_payloads
            )
        )

    def test_run_next_step_grants_directory_path_second_chance_repair(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Inspect the tests directory.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"tests"}}'
                    ),
                    (
                        '{"summary":"Inspect the repo root.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"."}}'
                    ),
                    (
                        '{"summary":"Read the test file directly.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"tests/test_smoke.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        self.assertEqual("tests/test_smoke.py", outcome.tool_result.data["relative_path"])
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_directory_path_second_chance_requested",
            event_types,
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_retry_requested"
        ]
        self.assertEqual(2, len(event_payloads))

    def test_run_next_step_grants_missing_relative_path_second_chance_repair(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            "def slugify_title(value: str) -> str:\n    return value\n",
            encoding="utf-8",
        )
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the implementation first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file"}}'
                    ),
                    (
                        '{"summary":"Still inspect the implementation.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file"}}'
                    ),
                    (
                        '{"summary":"Read the slug helper directly.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/string_tools.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        self.assertEqual(
            "demo_app/string_tools.py",
            outcome.tool_result.data["relative_path"],
        )
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_missing_relative_path_second_chance_requested",
            event_types,
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_retry_requested"
        ]
        self.assertEqual(2, len(event_payloads))

    def test_run_next_step_grants_missing_repo_file_second_chance_repair(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "number_tools.py").write_text(
            "def clamp(value: int, lower: int, upper: int) -> int:\n    return value\n",
            encoding="utf-8",
        )
        tests_dir = session.repo_path / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_number_tools.py").write_text(
            "def test_placeholder() -> None:\n    assert True\n",
            encoding="utf-8",
        )
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the missing clamp helper.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"clamp.py"}}'
                    ),
                    (
                        '{"summary":"Read another missing helper.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"number_helper.py"}}'
                    ),
                    (
                        '{"summary":"Read the existing number helper.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/number_tools.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        self.assertEqual("demo_app/number_tools.py", outcome.tool_result.data["relative_path"])
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_missing_repo_file_second_chance_requested",
            event_types,
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_retry_requested"
        ]
        self.assertEqual(2, len(event_payloads))

    def test_run_next_step_repairs_shell_test_into_run_test(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Run the local tests from shell.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"shell",'
                        '"command":["python3","-m","unittest","discover","-s","tests","-v"]'
                        "}}"
                    ),
                    (
                        '{"summary":"Still run the local tests from shell.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"shell",'
                        '"command":["python3","-m","unittest","discover","-s","tests","-v"]'
                        "}}"
                    ),
                    (
                        '{"summary":"Run the local tests with run_test.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"run_test",'
                        '"command":["python3","-m","unittest","discover","-s","tests","-v"]'
                        "}}"
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("run_test", outcome.tool_result.tool_name)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_approval_path_second_chance_requested",
            event_types,
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any(
                "Use run_test instead of shell for local tests" in payload.get("error", "")
                for payload in event_payloads
            )
        )

    def test_run_next_step_repairs_shell_file_read_into_read_file(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            'def slugify_title(value: str) -> str:\n    return value.replace(" ", "_")\n',
            encoding="utf-8",
        )
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Inspect the file through shell.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"shell",'
                        '"command":["cat","demo_app/string_tools.py"]'
                        "}}"
                    ),
                    (
                        '{"summary":"Still inspect the file through shell.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"shell",'
                        '"command":["cat","demo_app/string_tools.py"]'
                        "}}"
                    ),
                    (
                        '{"summary":"Read the slug helper directly.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/string_tools.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        self.assertEqual(
            "demo_app/string_tools.py",
            outcome.tool_result.data["relative_path"],
        )
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_approval_path_second_chance_requested",
            event_types,
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any(
                "Use read_file for that file instead of shell" in payload.get("error", "")
                for payload in event_payloads
            )
        )

    def test_run_next_step_retries_file_patch_without_recent_read(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Patch the README directly.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"README.md",'
                        '"expected_old_snippet":"hello\\n",'
                        '"new_snippet":"hello\\nfixed\\n"'
                        "}}"
                    ),
                    (
                        '{"summary":"Read the README first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        event_payloads = [
            event.payload for event in session.timeline if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any(
                "edit without recent file context for file_patch" in payload.get("error", "")
                for payload in event_payloads
            )
        )

    def test_run_next_step_grants_edit_without_read_second_chance_repair(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            'def slugify_title(value: str) -> str:\n    return value.replace(" ", "_")\n',
            encoding="utf-8",
        )
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Patch the slug helper immediately.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"demo_app/string_tools.py",'
                        '"expected_old_snippet":"return value.replace(\\" \\", \\"_\\")",'
                        '"new_snippet":"return value.replace(\\" \\", \\"-\\")"'
                        "}}"
                    ),
                    (
                        '{"summary":"Still patch it directly.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"demo_app/string_tools.py",'
                        '"expected_old_snippet":"return value.replace(\\" \\", \\"_\\")",'
                        '"new_snippet":"return value.replace(\\" \\", \\"-\\")"'
                        "}}"
                    ),
                    (
                        '{"summary":"Read the slug helper first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/string_tools.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_edit_target_second_chance_requested",
            event_types,
        )

    def test_run_next_step_grants_off_target_edit_second_chance_repair(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "number_tools.py").write_text(
            "def clamp(value: int, lower: int, upper: int) -> int:\n    return value\n",
            encoding="utf-8",
        )
        readme_result = session.request_tool(FileReadRequest(relative_path="README.md"))
        source_result = session.request_tool(
            FileReadRequest(relative_path="demo_app/number_tools.py")
        )
        self.assertEqual("executed", readme_result.status)
        self.assertEqual("executed", source_result.status)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Patch the README note.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"README.md",'
                        '"expected_old_snippet":"hello\\n",'
                        '"new_snippet":"hello\\nfixed\\n"'
                        "}}"
                    ),
                    (
                        '{"summary":"Still patch the README note.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"README.md",'
                        '"expected_old_snippet":"hello\\n",'
                        '"new_snippet":"hello\\nfixed\\n"'
                        "}}"
                    ),
                    (
                        '{"summary":"Patch the number helper instead.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"demo_app/number_tools.py",'
                        '"expected_old_snippet":"return value",'
                        '"new_snippet":"return lower if value < lower else value"'
                        "}}"
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("approval_required", outcome.tool_result.status)
        self.assertEqual("file_patch", outcome.tool_result.tool_name)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_edit_target_second_chance_requested",
            event_types,
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any("off-target edit path" in payload.get("error", "") for payload in event_payloads)
        )

    def test_run_next_step_grants_no_op_patch_second_chance_repair(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            'def slugify_title(value: str) -> str:\n    return value.replace(" ", "_")\n',
            encoding="utf-8",
        )
        read_result = session.request_tool(
            FileReadRequest(relative_path="demo_app/string_tools.py")
        )
        self.assertEqual("executed", read_result.status)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Patch with no real diff.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"demo_app/string_tools.py",'
                        '"expected_old_snippet":"return value.replace(\\" \\", \\"_\\")",'
                        '"new_snippet":"return value.replace(\\" \\", \\"_\\")"'
                        "}}"
                    ),
                    (
                        '{"summary":"Still patch with no real diff.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"demo_app/string_tools.py",'
                        '"expected_old_snippet":"return value.replace(\\" \\", \\"_\\")",'
                        '"new_snippet":"return value.replace(\\" \\", \\"_\\")"'
                        "}}"
                    ),
                    (
                        '{"summary":"Patch the slug helper with a real diff.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"demo_app/string_tools.py",'
                        '"expected_old_snippet":"return value.replace(\\" \\", \\"_\\")",'
                        '"new_snippet":"return value.replace(\\" \\", \\"-\\")"'
                        "}}"
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("approval_required", outcome.tool_result.status)
        self.assertEqual("file_patch", outcome.tool_result.tool_name)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn(
            "agent_step_output_patch_contract_second_chance_requested",
            event_types,
        )

    def test_run_next_step_allows_file_patch_after_recent_read(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        read_result = session.request_tool(FileReadRequest(relative_path="README.md"))
        self.assertEqual("executed", read_result.status)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Patch the README after reading it.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"README.md",'
                        '"expected_old_snippet":"hello\\n",'
                        '"new_snippet":"hello\\nfixed\\n"'
                        "}}"
                    )
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("approval_required", outcome.tool_result.status)

    def test_run_next_step_retries_readme_reread_once(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            'def slugify_title(value: str) -> str:\n    return value.replace(" ", "_")\n',
            encoding="utf-8",
        )
        read_result = session.request_tool(FileReadRequest(relative_path="README.md"))
        self.assertEqual("executed", read_result.status)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the README again before editing.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                    ),
                    (
                        '{"summary":"Read the slug helper instead.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/string_tools.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("executed", outcome.tool_result.status)
        self.assertEqual("read_file", outcome.tool_result.tool_name)
        self.assertEqual(
            "demo_app/string_tools.py",
            outcome.tool_result.data["relative_path"],
        )
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any("rereading README.md" in payload.get("error", "") for payload in event_payloads)
        )

    def test_run_next_step_retries_same_file_reread_once(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            'def slugify_title(value: str) -> str:\n    return value.replace(" ", "_")\n',
            encoding="utf-8",
        )
        read_result = session.request_tool(
            FileReadRequest(relative_path="demo_app/string_tools.py")
        )
        self.assertEqual("executed", read_result.status)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the slug helper again before editing.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"demo_app/string_tools.py"}}'
                    ),
                    (
                        '{"summary":"Patch the slug helper instead.",'
                        '"action":"request_tool",'
                        '"tool_request":{'
                        '"tool_type":"file_patch",'
                        '"relative_path":"demo_app/string_tools.py",'
                        '"expected_old_snippet":"return value.replace(\\" \\", \\"_\\")",'
                        '"new_snippet":"return value.replace(\\" \\", \\"-\\")"'
                        "}}"
                    ),
                ]
            )
        )

        outcome = runner.run_next_step(session)

        self.assertEqual("request_tool", outcome.decision.action)
        self.assertEqual("approval_required", outcome.tool_result.status)
        self.assertEqual("file_patch", outcome.tool_result.tool_name)
        event_payloads = [
            event.payload
            for event in session.timeline
            if event.event_type == "agent_step_output_invalid"
        ]
        self.assertTrue(
            any(
                "recent context for that file is already available"
                in payload.get("error", "")
                for payload in event_payloads
            )
        )

    def test_run_loop_stops_when_approval_is_required(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the README first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                    ),
                (
                    '{"summary":"Apply the fix note.",'
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
        )

        outcome = runner.run_loop(session, max_steps=4)

        self.assertEqual("approval_required", outcome.stop_reason)
        self.assertEqual(2, outcome.steps_completed)
        self.assertEqual("executed", outcome.steps[0].tool_result.status)
        self.assertEqual("approval_required", outcome.steps[1].tool_result.status)
        self.assertEqual("completed", session.todos[0].status.value)
        self.assertEqual("in_progress", session.todos[1].status.value)
        self.assertEqual("pending", session.todos[2].status.value)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn("agent_loop_started", event_types)
        self.assertIn("agent_loop_stopped", event_types)

    def test_run_loop_stops_at_max_steps_when_work_continues(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        self.seed_todos(session)
        runner = AgentRunner(
            FakeModelClient(
                [
                    (
                        '{"summary":"Read the README first.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
                    ),
                    (
                        '{"summary":"Read the test file next.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"tests/test_smoke.py"}}'
                    ),
                ]
            )
        )

        outcome = runner.run_loop(session, max_steps=2)

        self.assertEqual("max_steps_reached", outcome.stop_reason)
        self.assertEqual(2, outcome.steps_completed)
        self.assertEqual("executed", outcome.steps[1].tool_result.status)
        self.assertEqual("completed", session.todos[0].status.value)
        self.assertEqual("completed", session.todos[1].status.value)
        self.assertEqual("in_progress", session.todos[2].status.value)


if __name__ == "__main__":
    unittest.main()
