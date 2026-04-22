import json
import unittest

from repo_task_runtime import AgentRunner, ModelResponse
from repo_task_runtime.eval_pack import (
    APPROVAL_MODE_AUTO_APPROVE_EDITS,
    APPROVAL_MODE_STOP_ON_REQUEST,
    EvalRunner,
    builtin_eval_cases,
    get_builtin_eval_case,
)


class RuleBasedEvalModelClient:
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

        if "slugify_title so it uses hyphens" in user_prompt:
            return _slug_join_response(user_prompt)
        if "values below the lower bound return the lower bound" in user_prompt:
            return _clamp_response(user_prompt)
        if "compact_whitespace so it trims edges" in user_prompt:
            return _whitespace_response(user_prompt)
        raise AssertionError("Unexpected eval prompt.")


class BadPatchModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        if "slugify_title so it uses hyphens" in user_prompt and '"tool_name": "read_file"' in user_prompt:
            return ModelResponse(
                text=json.dumps(
                    {
                        "summary": "Patch the slug helper.",
                        "action": "request_tool",
                        "tool_request": {
                            "tool_type": "file_patch",
                            "relative_path": "demo_app/string_tools.py",
                            "expected_old_snippet": '".".join(parts)',
                            "new_snippet": '"-".join(parts)',
                        },
                    }
                ),
                model="gpt-5.4-mini-test",
                usage={"total_tokens": 111},
            )
        return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)


class RepeatedReadEvalModelClient(RuleBasedEvalModelClient):
    def __init__(self) -> None:
        self.slug_read_count = 0

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)

        if "slugify_title so it uses hyphens" not in user_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)

        prompt_payload = json.loads(user_prompt)
        latest_tool_result = prompt_payload["snapshot"].get("latest_tool_result")
        latest_tool_name = None
        if latest_tool_result is not None:
            latest_tool_name = latest_tool_result.get("tool_name")

        if latest_tool_name is None:
            payload = {
                "summary": "Read the slug helper first.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "demo_app/string_tools.py",
                },
            }
        elif latest_tool_name == "read_file":
            self.slug_read_count += 1
            if self.slug_read_count == 1:
                payload = {
                    "summary": "Read the slug helper again before editing.",
                    "action": "request_tool",
                    "tool_request": {
                        "tool_type": "read_file",
                        "relative_path": "demo_app/string_tools.py",
                    },
                }
            else:
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
        elif latest_tool_name == "file_patch":
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


class EvalPackTest(unittest.TestCase):
    def test_builtin_eval_cases_are_stable(self):
        cases = builtin_eval_cases()
        self.assertEqual(
            ["slug_join", "clamp_lower_bound", "compact_whitespace"],
            [case.case_id for case in cases],
        )

    def test_eval_runner_reports_success_with_auto_approval(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(RuleBasedEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_cases(builtin_eval_cases())

        self.assertEqual(3, report.passed_cases)
        self.assertEqual(0, report.failed_cases)
        self.assertEqual({}, report.failure_reason_counts)
        self.assertEqual(1.0, report.context_bundle_metrics.average_read_file_calls)
        self.assertEqual(0.0, report.context_bundle_metrics.average_duplicate_read_file_calls)
        self.assertEqual(0, report.context_bundle_metrics.cases_with_same_file_rereads)
        for case in report.case_reports:
            self.assertTrue(case.success)
            self.assertEqual("executed", case.verification_status)
            self.assertEqual(0, case.verification_exit_code)
            self.assertGreaterEqual(case.approvals_auto_resolved, 1)
            self.assertEqual(1, case.context_bundle_metrics.read_file_calls)
            self.assertEqual(0, case.context_bundle_metrics.duplicate_read_file_calls)
            self.assertFalse(case.context_bundle_metrics.same_file_reread_detected)
            self.assertEqual((), case.context_bundle_metrics.same_file_reread_paths)

    def test_eval_runner_stops_on_manual_approval_mode(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(RuleBasedEvalModelClient()),
            approval_mode=APPROVAL_MODE_STOP_ON_REQUEST,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("approval_required", report.stop_reason)
        self.assertEqual("approval_required", report.failure_reason)

    def test_eval_runner_classifies_bad_patch_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(BadPatchModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("tool_failed", report.stop_reason)
        self.assertEqual("bad_patch", report.failure_reason)

    def test_eval_runner_tracks_same_file_rereads(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(RepeatedReadEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_cases([get_builtin_eval_case("slug_join")])

        self.assertEqual(1, report.passed_cases)
        self.assertEqual(0, report.failed_cases)
        self.assertEqual(5.0, report.average_steps)
        self.assertEqual(2.0, report.context_bundle_metrics.average_read_file_calls)
        self.assertEqual(1.0, report.context_bundle_metrics.average_duplicate_read_file_calls)
        self.assertEqual(1, report.context_bundle_metrics.cases_with_same_file_rereads)

        case = report.case_reports[0]
        self.assertTrue(case.success)
        self.assertEqual(2, case.context_bundle_metrics.read_file_calls)
        self.assertEqual(1, case.context_bundle_metrics.duplicate_read_file_calls)
        self.assertTrue(case.context_bundle_metrics.same_file_reread_detected)
        self.assertEqual(
            ("demo_app/string_tools.py",),
            case.context_bundle_metrics.same_file_reread_paths,
        )


def _slug_join_response(user_prompt: str) -> ModelResponse:
    if '"latest_tool_result": null' in user_prompt:
        payload = {
            "summary": "Read the slug helper first.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "demo_app/string_tools.py",
            },
        }
    elif '"tool_name": "read_file"' in user_prompt and "demo_app/string_tools.py" in user_prompt:
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


def _clamp_response(user_prompt: str) -> ModelResponse:
    if '"latest_tool_result": null' in user_prompt:
        payload = {
            "summary": "Read the clamp helper first.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "demo_app/number_tools.py",
            },
        }
    elif '"tool_name": "read_file"' in user_prompt and "demo_app/number_tools.py" in user_prompt:
        payload = {
            "summary": "Patch the lower-bound branch.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "file_patch",
                "relative_path": "demo_app/number_tools.py",
                "expected_old_snippet": "if value < lower:\n        return upper",
                "new_snippet": "if value < lower:\n        return lower",
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
        payload = {"summary": "The clamp task is done.", "action": "finish"}
    return ModelResponse(
        text=json.dumps(payload),
        model="gpt-5.4-mini-test",
        usage={"total_tokens": 111},
    )


def _whitespace_response(user_prompt: str) -> ModelResponse:
    if '"latest_tool_result": null' in user_prompt:
        payload = {
            "summary": "Read the whitespace helper first.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "demo_app/text_tools.py",
            },
        }
    elif '"tool_name": "read_file"' in user_prompt and "demo_app/text_tools.py" in user_prompt:
        payload = {
            "summary": "Patch the whitespace splitting logic.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "file_patch",
                "relative_path": "demo_app/text_tools.py",
                "expected_old_snippet": 'split(" ")',
                "new_snippet": "split()",
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
        payload = {"summary": "The whitespace task is done.", "action": "finish"}
    return ModelResponse(
        text=json.dumps(payload),
        model="gpt-5.4-mini-test",
        usage={"total_tokens": 111},
    )
