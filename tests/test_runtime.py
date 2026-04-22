import subprocess
import tempfile
import unittest
from pathlib import Path

from repo_task_runtime import (
    FilePatchRequest,
    FileReadRequest,
    ShellCommandRequest,
    TaskWorkbench,
    TestCommandRequest,
    TodoItem,
    TodoStatus,
    WriteFileRequest,
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


class TaskRuntimeTest(unittest.TestCase):
    def make_session(self):
        temp_dir = tempfile.TemporaryDirectory()
        repo_path = Path(temp_dir.name)
        init_git_repo(repo_path)
        workbench = TaskWorkbench()
        session = workbench.create_session(repo_path)
        session.begin_task("Fix a local issue")
        session.update_plan("1. Inspect\n2. Edit\n3. Test")
        return temp_dir, session

    def test_plan_mode_blocks_mutating_tools_until_plan_is_approved(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)

        denied = session.request_tool(
            WriteFileRequest(relative_path="notes.txt", content="draft\n")
        )
        self.assertEqual("denied", denied.status)
        self.assertIn("plan mode", denied.message)

        session.approve_plan()
        pending = session.request_tool(
            WriteFileRequest(relative_path="notes.txt", content="approved\n")
        )
        self.assertEqual("approval_required", pending.status)
        executed = session.resolve_approval(pending.approval_id, approve=True)
        self.assertEqual("executed", executed.status)
        self.assertIn("+approved", executed.diff)
        self.assertTrue((session.repo_path / "notes.txt").exists())

    def test_file_patch_requires_approval_and_rejects_ambiguous_matches(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.approve_plan()

        target = session.repo_path / "module.py"
        target.write_text('value = "same"\nother = "same"\n', encoding="utf-8")

        pending = session.request_tool(
            FilePatchRequest(
                relative_path="module.py",
                expected_old_snippet='"same"',
                new_snippet='"updated"',
            )
        )
        self.assertEqual("approval_required", pending.status)

        executed = session.resolve_approval(pending.approval_id, approve=True)
        self.assertEqual("failed", executed.status)
        self.assertIn("matched multiple locations", executed.message)

        pending = session.request_tool(
            FilePatchRequest(
                relative_path="module.py",
                expected_old_snippet='value = "same"',
                new_snippet='value = "updated"',
            )
        )
        executed = session.resolve_approval(pending.approval_id, approve=True)
        self.assertEqual("executed", executed.status)
        self.assertIn('-value = "same"', executed.diff)
        self.assertIn('+value = "updated"', executed.diff)
        self.assertIn('other = "same"', target.read_text(encoding="utf-8"))

    def test_todo_lifecycle_enforces_single_in_progress(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)

        session.replace_todos(
            [
                TodoItem(content="Inspect failure", status=TodoStatus.IN_PROGRESS),
                TodoItem(content="Patch code", status=TodoStatus.PENDING),
                TodoItem(content="Run tests", status=TodoStatus.PENDING),
            ]
        )
        self.assertEqual(3, len(session.todos))

        with self.assertRaises(ValueError):
            session.replace_todos(
                [
                    TodoItem(content="Inspect failure", status=TodoStatus.IN_PROGRESS),
                    TodoItem(content="Patch code", status=TodoStatus.IN_PROGRESS),
                ]
            )

        session.replace_todos(
            [
                TodoItem(content="Inspect failure", status=TodoStatus.COMPLETED),
                TodoItem(content="Patch code", status=TodoStatus.COMPLETED),
            ]
        )
        self.assertEqual([], session.todos)

    def test_safe_test_command_runs_without_manual_approval(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.approve_plan()

        tests_dir = session.repo_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_smoke.py").write_text(
            "import unittest\n\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_truth(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        result = session.request_tool(
            TestCommandRequest(
                command=("python3", "-m", "unittest", "discover", "-s", "tests")
            )
        )
        self.assertEqual("executed", result.status)
        self.assertEqual(0, result.exit_code)
        event_types = [event.event_type for event in session.timeline]
        self.assertIn("local_test_completed", event_types)

    def test_shell_policy_allows_safe_reads_and_denies_dangerous_commands(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.approve_plan()

        safe = session.request_tool(ShellCommandRequest(command=("git", "status")))
        self.assertEqual("executed", safe.status)
        self.assertEqual(0, safe.exit_code)

        denied = session.request_tool(ShellCommandRequest(command=("rm", "-rf", ".")))
        self.assertEqual("denied", denied.status)
        self.assertIn("dangerous", denied.message)

    def test_successful_test_is_bound_to_current_repo_state(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.approve_plan()

        tests_dir = session.repo_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_smoke.py").write_text(
            "import unittest\n\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_truth(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        test_result = session.request_tool(
            TestCommandRequest(
                command=("python3", "-m", "unittest", "discover", "-s", "tests")
            )
        )
        self.assertEqual(0, test_result.exit_code)
        self.assertTrue(session.has_successful_test_for_current_state())
        self.assertIsNotNone(session.snapshot().latest_successful_test)

        pending = session.request_tool(
            WriteFileRequest(relative_path="notes.txt", content="mutated\n")
        )
        executed = session.resolve_approval(pending.approval_id, approve=True)
        self.assertEqual("executed", executed.status)
        self.assertFalse(session.has_successful_test_for_current_state())
        self.assertIsNone(session.snapshot().latest_successful_test)

    def test_missing_path_suggestion_prefers_source_file_over_readme(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            "def slugify_title(value: str) -> str:\n    return value\n",
            encoding="utf-8",
        )
        tests_dir = session.repo_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_string_tools.py").write_text(
            "def test_placeholder() -> None:\n    assert True\n",
            encoding="utf-8",
        )

        message = session.validate_tool_request_path(
            FileReadRequest(relative_path="slugify.py")
        )

        self.assertIsNotNone(message)
        self.assertIn("demo_app/string_tools.py", message)
        self.assertIn("tests/test_string_tools.py", message)
        self.assertNotIn("README.md", message)

    def test_missing_path_suggestion_uses_nearest_existing_directory(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        helpers_dir = session.repo_path / "demo_app" / "helpers"
        helpers_dir.mkdir(parents=True)
        (helpers_dir / "number_tools.py").write_text(
            "def clamp(value: int, lower: int, upper: int) -> int:\n    return value\n",
            encoding="utf-8",
        )
        (session.repo_path / "demo_app" / "string_tools.py").write_text(
            "def slugify_title(value: str) -> str:\n    return value\n",
            encoding="utf-8",
        )

        message = session.validate_tool_request_path(
            FileReadRequest(relative_path="demo_app/helpers/missing/clamp.py")
        )

        self.assertIsNotNone(message)
        self.assertIn("demo_app/helpers/number_tools.py", message)
        self.assertNotIn("demo_app/string_tools.py", message)

    def test_missing_relative_path_suggestions_prefer_recent_code_context(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.approve_plan()
        source_dir = session.repo_path / "demo_app"
        source_dir.mkdir()
        (source_dir / "string_tools.py").write_text(
            "def slugify_title(value: str) -> str:\n    return value\n",
            encoding="utf-8",
        )
        tests_dir = session.repo_path / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_string_tools.py").write_text(
            "def test_placeholder() -> None:\n    assert True\n",
            encoding="utf-8",
        )

        read_result = session.request_tool(
            FileReadRequest(relative_path="demo_app/string_tools.py")
        )
        self.assertEqual("executed", read_result.status)

        suggestions = session.suggest_existing_files_for_missing_relative_path(
            tool_name="read_file",
            limit=3,
        )

        self.assertGreaterEqual(len(suggestions), 1)
        self.assertEqual("demo_app/string_tools.py", suggestions[0])
        self.assertNotIn("README.md", suggestions)

    def test_directory_path_suggestion_prefers_file_inside_directory(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        tests_dir = session.repo_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_string_tools.py").write_text(
            "def test_placeholder() -> None:\n    assert True\n",
            encoding="utf-8",
        )

        message = session.validate_tool_request_path(
            FileReadRequest(relative_path="tests")
        )

        self.assertIsNotNone(message)
        self.assertIn("tests/test_string_tools.py", message)
        self.assertNotIn("README.md", message)

    def test_begin_task_resets_previous_task_state(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)
        session.approve_plan()
        session.replace_todos(
            [
                TodoItem(content="Inspect failure", status=TodoStatus.IN_PROGRESS),
                TodoItem(content="Patch code", status=TodoStatus.PENDING),
            ]
        )
        read_result = session.request_tool(FileReadRequest(relative_path="README.md"))
        self.assertEqual("executed", read_result.status)
        pending = session.request_tool(
            WriteFileRequest(relative_path="notes.txt", content="draft\n")
        )
        self.assertEqual("approval_required", pending.status)
        self.assertEqual(1, len(session.pending_approvals))
        self.assertEqual(1, len(session.recent_file_contexts))

        session.begin_task("Start a fresh task")

        snapshot = session.snapshot()
        self.assertEqual("Start a fresh task", snapshot.task_input)
        self.assertEqual("plan", snapshot.permission_mode)
        self.assertIsNone(snapshot.plan)
        self.assertEqual([], list(snapshot.todos))
        self.assertEqual("", snapshot.latest_diff)
        self.assertIsNone(snapshot.latest_tool_result)
        self.assertEqual([], list(snapshot.pending_approvals))
        self.assertEqual([], session.recent_file_contexts)
        self.assertEqual([], session.recent_test_failures)
        self.assertEqual(
            ["task_received", "plan_mode_entered"],
            [event.event_type for event in snapshot.timeline],
        )


if __name__ == "__main__":
    unittest.main()
