import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from repo_task_runtime import (
    AgentRunner,
    ContextBundleBuilder,
    FileReadRequest,
    FilePatchRequest,
    ModelResponse,
    TaskWorkbench,
    TestCommandRequest,
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


class RecordingModelClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.prompts = []

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        self.prompts.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return ModelResponse(
            text=self.response_text,
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 123},
        )


class ContextBundleTest(unittest.TestCase):
    def make_session(self):
        temp_dir = tempfile.TemporaryDirectory()
        repo_path = Path(temp_dir.name)
        init_git_repo(repo_path)

        app_dir = repo_path / "demo_app"
        tests_dir = repo_path / "tests"
        app_dir.mkdir()
        tests_dir.mkdir()
        (app_dir / "__init__.py").write_text("", encoding="utf-8")
        (app_dir / "app.py").write_text(
            'def label() -> str:\n'
            '    return "bad"\n',
            encoding="utf-8",
        )
        (tests_dir / "test_app.py").write_text(
            "import unittest\n\n"
            "from demo_app.app import label\n\n"
            "class AppTest(unittest.TestCase):\n"
            "    def test_label(self):\n"
            '        self.assertEqual("good", label())\n',
            encoding="utf-8",
        )

        session = TaskWorkbench().create_session(repo_path)
        session.begin_task("Fix the label helper and run tests.")
        session.update_plan("1. Inspect\n2. Fix\n3. Test")
        session.approve_plan()
        return temp_dir, session

    def test_builder_tracks_recent_file_contexts_and_clears_stale_test_failures(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)

        read_result = session.request_tool(
            FileReadRequest(relative_path="demo_app/app.py")
        )
        self.assertEqual("executed", read_result.status)

        failed_test = session.request_tool(
            TestCommandRequest(
                command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v")
            )
        )
        self.assertEqual(1, failed_test.exit_code)

        bundle = ContextBundleBuilder().build(session)
        self.assertEqual("demo_app/app.py", bundle["recent_file_contexts"][0]["relative_path"])
        self.assertIn('return "bad"', bundle["recent_file_contexts"][0]["content"])
        self.assertEqual(1, bundle["recent_test_failures"][0]["exit_code"])

        pending = session.request_tool(
            FilePatchRequest(
                relative_path="demo_app/app.py",
                expected_old_snippet='return "bad"',
                new_snippet='return "good"',
            )
        )
        self.assertEqual("approval_required", pending.status)
        executed = session.resolve_approval(pending.approval_id, approve=True)
        self.assertEqual("executed", executed.status)

        passed_test = session.request_tool(
            TestCommandRequest(
                command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v")
            )
        )
        self.assertEqual(0, passed_test.exit_code)

        bundle = ContextBundleBuilder().build(session)
        self.assertEqual("file_patch", bundle["recent_file_contexts"][0]["source_tool"])
        self.assertIn('return "good"', bundle["recent_file_contexts"][0]["content"])
        self.assertEqual([], bundle["recent_test_failures"])

    def test_agent_step_prompt_includes_context_bundle(self):
        temp_dir, session = self.make_session()
        self.addCleanup(temp_dir.cleanup)

        read_result = session.request_tool(
            FileReadRequest(relative_path="demo_app/app.py")
        )
        self.assertEqual("executed", read_result.status)
        failed_test = session.request_tool(
            TestCommandRequest(
                command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v")
            )
        )
        self.assertEqual(1, failed_test.exit_code)

        model_client = RecordingModelClient('{"summary":"Done for now.","action":"finish"}')
        runner = AgentRunner(model_client, context_builder=ContextBundleBuilder())

        outcome = runner.run_next_step(session)

        self.assertEqual("finish", outcome.decision.action)
        prompt_payload = json.loads(model_client.prompts[-1]["user_prompt"])
        snapshot = prompt_payload["snapshot"]
        self.assertEqual("run_test", snapshot["latest_tool_result"]["tool_name"])
        self.assertEqual("demo_app/app.py", snapshot["recent_file_contexts"][0]["relative_path"])
        self.assertEqual(1, snapshot["recent_test_failures"][0]["exit_code"])


if __name__ == "__main__":
    unittest.main()
