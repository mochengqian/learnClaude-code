import subprocess
import tempfile
import unittest
from pathlib import Path

from repo_task_runtime import (
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


if __name__ == "__main__":
    unittest.main()
