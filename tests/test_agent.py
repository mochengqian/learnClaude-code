import subprocess
import tempfile
import unittest
from pathlib import Path

from repo_task_runtime import AgentRunner, ModelResponse, TaskWorkbench, TodoItem, TodoStatus


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
                        '{"summary":"Read the README again.",'
                        '"action":"request_tool",'
                        '"tool_request":{"tool_type":"read_file","relative_path":"README.md"}}'
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
