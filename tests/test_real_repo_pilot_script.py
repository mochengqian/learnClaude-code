import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_task_runtime import ModelResponse
from scripts import run_real_repo_pilot


class RuleBasedRealRepoPilotModelClient:
    def __init__(self) -> None:
        self.step_index_by_case = {}

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        case_id = _detect_case_id(user_prompt)

        if "repo-task planning assistant" in system_prompt:
            payload = {
                "plan_markdown": "1. Inspect\n2. Patch\n3. Test",
                "todos": [
                    {
                        "content": "Inspect the target files",
                        "active_form": "Inspecting the target files",
                        "status": "in_progress",
                    },
                    {
                        "content": "Apply the smallest safe edit",
                        "active_form": "Applying the smallest safe edit",
                        "status": "pending",
                    },
                    {
                        "content": "Run the local unittest suite",
                        "active_form": "Running the local unittest suite",
                        "status": "pending",
                    },
                ],
            }
            return _response(payload)

        next_step = self.step_index_by_case.get(case_id, 0) + 1
        self.step_index_by_case[case_id] = next_step

        if case_id == "readme_provider_checkpoint_refresh":
            payload = _readme_case_payload(next_step)
        elif case_id == "provider_content_comment_single_file":
            payload = _provider_comment_payload(next_step)
        elif case_id == "failing_test_points_to_source_real":
            payload = _plan_invalid_output_payload(next_step)
        else:
            raise AssertionError("Unexpected real repo pilot case.")
        return _response(payload)


class RealRepoPilotScriptTest(unittest.TestCase):
    def test_real_repo_pilot_script_runs_all_cases_and_reports_json_summary(self):
        if _should_skip_inside_real_repo_pilot_copy():
            raise unittest.SkipTest("skip recursive real repo pilot execution")

        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(
            run_real_repo_pilot,
            "create_model_client_from_env",
            return_value=RuleBasedRealRepoPilotModelClient(),
        ):
            with patch.object(sys, "argv", ["run_real_repo_pilot.py", "--json"]):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exit_code = run_real_repo_pilot.main()

        self.assertEqual(0, exit_code)
        self.assertEqual("", stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual("real_repo_pilot", payload["suite"])
        self.assertEqual(3, payload["passed_cases"])
        self.assertEqual(3, payload["total_cases"])
        self.assertEqual({}, payload["failure_reason_counts"])
        self.assertEqual(0.0, payload["average_duplicate_reads"])
        self.assertEqual(
            {
                "readme_provider_checkpoint_refresh",
                "provider_content_comment_single_file",
                "failing_test_points_to_source_real",
            },
            {case["case_id"] for case in payload["cases"]},
        )
        self.assertTrue(all(case["success"] for case in payload["cases"]))

    def test_list_cases_prints_builtin_real_repo_cases(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(sys, "argv", ["run_real_repo_pilot.py", "--list-cases"]):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = run_real_repo_pilot.main()

        self.assertEqual(0, exit_code)
        self.assertEqual("", stderr.getvalue())
        output = stdout.getvalue()
        self.assertIn("readme_provider_checkpoint_refresh", output)
        self.assertIn("provider_content_comment_single_file", output)
        self.assertIn("failing_test_points_to_source_real", output)


def _should_skip_inside_real_repo_pilot_copy() -> bool:
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / run_real_repo_pilot.PILOT_SENTINEL_FILENAME).exists()


def _detect_case_id(user_prompt: str) -> str:
    if "M4 provider-stability closeout section" in user_prompt:
        return "readme_provider_checkpoint_refresh"
    if "placeholder comment above _coerce_assistant_content" in user_prompt:
        return "provider_content_comment_single_file"
    if "plan_invalid_output taxonomy regression" in user_prompt:
        return "failing_test_points_to_source_real"
    raise AssertionError("Unexpected real repo pilot prompt.")


def _response(payload):
    return ModelResponse(
        text=json.dumps(payload),
        model="gpt-5.4-mini-test",
        usage={"total_tokens": 111},
    )


def _readme_case_payload(step: int):
    if step == 1:
        return {
            "summary": "Read README.md before patching the M4 checkpoint.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "README.md",
            },
        }
    if step == 2:
        return {
            "summary": "Patch the stale M4 provider-stability checkpoint line.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "file_patch",
                "relative_path": "README.md",
                "expected_old_snippet": run_real_repo_pilot.README_PROVIDER_CHECKPOINT_STALE,
                "new_snippet": run_real_repo_pilot.README_PROVIDER_CHECKPOINT_CURRENT,
            },
        }
    if step == 3:
        return {
            "summary": "Run the full unittest suite after the README update.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "run_test",
                "command": list(run_real_repo_pilot.FULL_TEST_COMMAND),
            },
        }
    return {"summary": "The README checkpoint refresh is done.", "action": "finish"}


def _provider_comment_payload(step: int):
    if step == 1:
        return {
            "summary": "Inspect model_client.py before replacing the placeholder comment.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "repo_task_runtime/model_client.py",
            },
        }
    if step == 2:
        return {
            "summary": "Replace the placeholder comment above _coerce_assistant_content.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "file_patch",
                "relative_path": "repo_task_runtime/model_client.py",
                "expected_old_snippet": run_real_repo_pilot.MODEL_CLIENT_COMMENT_PLACEHOLDER,
                "new_snippet": run_real_repo_pilot.MODEL_CLIENT_COMMENT_EXPECTED,
            },
        }
    if step == 3:
        return {
            "summary": "Run the full unittest suite after the comment-only source edit.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "run_test",
                "command": list(run_real_repo_pilot.FULL_TEST_COMMAND),
            },
        }
    return {
        "summary": "The single-file provider content comment task is complete.",
        "action": "finish",
    }


def _plan_invalid_output_payload(step: int):
    if step == 1:
        return {
            "summary": "Run the full unittest suite to capture the failing taxonomy regression.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "run_test",
                "command": list(run_real_repo_pilot.FULL_TEST_COMMAND),
            },
        }
    if step == 2:
        return {
            "summary": "Read the eval pack test that points to the failing taxonomy.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "tests/test_eval_pack.py",
            },
        }
    if step == 3:
        return {
            "summary": "Read eval_metrics.py before patching the taxonomy regression.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "repo_task_runtime/eval_metrics.py",
            },
        }
    if step == 4:
        return {
            "summary": "Patch the broken plan_invalid_output mapping back to the specific taxonomy.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "file_patch",
                "relative_path": "repo_task_runtime/eval_metrics.py",
                "expected_old_snippet": run_real_repo_pilot.PLAN_INVALID_OUTPUT_BLOCK_BROKEN.rstrip("\n"),
                "new_snippet": run_real_repo_pilot.PLAN_INVALID_OUTPUT_BLOCK_FIXED.rstrip("\n"),
            },
        }
    if step == 5:
        return {
            "summary": "Run the full unittest suite after the eval_metrics.py fix.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "run_test",
                "command": list(run_real_repo_pilot.FULL_TEST_COMMAND),
            },
        }
    return {"summary": "The taxonomy regression has been fixed.", "action": "finish"}


if __name__ == "__main__":
    unittest.main()
