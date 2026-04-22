import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from repo_task_runtime import ModelResponse
from scripts import run_eval


class SlugOnlyEvalModelClient:
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return ModelResponse(
                text=json.dumps(
                    {
                        "plan_markdown": "1. Inspect\n2. Patch\n3. Test",
                        "todos": [
                            {"content": "Inspect the failing module", "status": "in_progress"},
                            {"content": "Patch the bug", "status": "pending"},
                            {"content": "Run tests", "status": "pending"},
                        ],
                    }
                ),
                model="gpt-5.4-mini-test",
                usage={"total_tokens": 111},
            )

        if '"latest_tool_result": null' in user_prompt:
            payload = {
                "summary": "Read the slug helper first.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "demo_app/string_tools.py",
                },
            }
        elif '"tool_name": "read_file"' in user_prompt:
            payload = {
                "summary": "Patch the slug join character.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "file_patch",
                    "relative_path": "demo_app/string_tools.py",
                    "expected_old_snippet": '"_".join(parts)',
                    "new_snippet": '"-".join(parts)',
                },
            }
        elif '"tool_name": "file_patch"' in user_prompt:
            payload = {
                "summary": "Run the test suite now.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "run_test",
                    "command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
                },
            }
        else:
            payload = {"summary": "The slug task is done.", "action": "finish"}

        return ModelResponse(
            text=json.dumps(payload),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class RunEvalCliTest(unittest.TestCase):
    def test_output_json_writes_report_and_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "artifacts" / "eval" / "slug-join.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch.object(
                run_eval,
                "create_model_client_from_env",
                return_value=SlugOnlyEvalModelClient(),
            ):
                with patch.object(
                    sys,
                    "argv",
                    [
                        "run_eval.py",
                        "--case",
                        "slug_join",
                        "--output-json",
                        str(output_path),
                    ],
                ):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        exit_code = run_eval.main()

            self.assertEqual(0, exit_code)
            self.assertEqual("", stderr.getvalue())
            self.assertTrue(output_path.exists())

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("auto_approve_edits", payload["approval_mode"])
            self.assertEqual(1, payload["total_cases"])
            self.assertEqual(1, payload["passed_cases"])
            self.assertEqual("slug_join", payload["cases"][0]["case_id"])
            self.assertIn("Wrote eval report to", stdout.getvalue())
