import json
import shutil
import unittest

from repo_task_runtime import (
    AgentRunner,
    FileReadRequest,
    ModelClientError,
    ModelResponse,
    TaskWorkbench,
)
from repo_task_runtime.eval_metrics import collect_context_bundle_case_metrics
from repo_task_runtime.eval_pack import (
    APPROVAL_MODE_AUTO_APPROVE_EDITS,
    APPROVAL_MODE_STOP_ON_REQUEST,
    EvalRunner,
    builtin_eval_cases,
    create_eval_repo,
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


class MissingPathEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        return ModelResponse(
            text=json.dumps(
                {
                    "summary": "Read the implementation first.",
                    "action": "request_tool",
                    "tool_request": {"tool_type": "read_file"},
                }
            ),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class DirectoryPathEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        return ModelResponse(
            text=json.dumps(
                {
                    "summary": "Read the demo_app directory first.",
                    "action": "request_tool",
                    "tool_request": {
                        "tool_type": "read_file",
                        "relative_path": "demo_app",
                    },
                }
            ),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class InvalidFinishEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        return ModelResponse(
            text=json.dumps(
                {
                    "summary": "The task is complete.",
                    "action": "finish",
                }
            ),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class EditWithoutReadEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        return ModelResponse(
            text=json.dumps(
                {
                    "summary": "Patch the slug helper immediately.",
                    "action": "request_tool",
                    "tool_request": {
                        "tool_type": "file_patch",
                        "relative_path": "demo_app/string_tools.py",
                        "expected_old_snippet": '"_".join(parts)',
                        "new_snippet": '"-".join(parts)',
                    },
                }
            ),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class TransportFailureEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        raise ModelClientError(
            "Model request failed after 2 attempts: EOF occurred in violation of protocol (_ssl.c:1129)"
        )


class ReadmeRereadEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        if '"latest_tool_result": null' in user_prompt:
            payload = {
                "summary": "Read the README first.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "README.md",
                },
            }
        else:
            payload = {
                "summary": "Read the README again.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "README.md",
                },
            }
        return ModelResponse(
            text=json.dumps(payload),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class SameFileRereadEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        if '"latest_tool_result": null' in user_prompt:
            payload = {
                "summary": "Read the slug helper first.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "demo_app/string_tools.py",
                },
            }
        else:
            payload = {
                "summary": "Read the slug helper again.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "demo_app/string_tools.py",
                },
            }
        return ModelResponse(
            text=json.dumps(payload),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class OffTargetEditEvalModelClient(RuleBasedEvalModelClient):
    def __init__(self) -> None:
        self.step_index = 0

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        self.step_index += 1
        if self.step_index == 1:
            payload = {
                "summary": "Read the README first.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "README.md",
                },
            }
        elif self.step_index == 2:
            payload = {
                "summary": "Read the slug helper next.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "demo_app/string_tools.py",
                },
            }
        else:
            payload = {
                "summary": "Patch the README note.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "file_patch",
                    "relative_path": "README.md",
                    "expected_old_snippet": "hello\n",
                    "new_snippet": "hello\nfixed\n",
                },
            }
        return ModelResponse(
            text=json.dumps(payload),
            model="gpt-5.4-mini-test",
            usage={"total_tokens": 111},
        )


class BadPatchTargetEvalModelClient(RuleBasedEvalModelClient):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if "repo-task planning assistant" in system_prompt:
            return super().complete(system_prompt=system_prompt, user_prompt=user_prompt)
        if '"latest_tool_result": null' in user_prompt:
            payload = {
                "summary": "Read the slug helper first.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "read_file",
                    "relative_path": "demo_app/string_tools.py",
                },
            }
        else:
            payload = {
                "summary": "Patch the slug helper with the same content.",
                "action": "request_tool",
                "tool_request": {
                    "tool_type": "file_patch",
                    "relative_path": "demo_app/string_tools.py",
                    "expected_old_snippet": '"_".join(parts)',
                    "new_snippet": '"_".join(parts)',
                },
            }
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

    def test_collect_context_bundle_case_metrics_tracks_same_file_rereads(self):
        case = get_builtin_eval_case("slug_join")
        repo_path = create_eval_repo(case)
        self.addCleanup(lambda: shutil.rmtree(repo_path, ignore_errors=True))
        session = TaskWorkbench().create_session(repo_path)
        session.begin_task(case.task_input)
        session.update_plan("1. Inspect\n2. Patch\n3. Test")
        session.approve_plan()

        first_read = session.request_tool(
            FileReadRequest(relative_path="demo_app/string_tools.py")
        )
        second_read = session.request_tool(
            FileReadRequest(relative_path="demo_app/string_tools.py")
        )
        self.assertEqual("executed", first_read.status)
        self.assertEqual("executed", second_read.status)

        metrics = collect_context_bundle_case_metrics(session)

        self.assertEqual(2, metrics.read_file_calls)
        self.assertEqual(1, metrics.duplicate_read_file_calls)
        self.assertTrue(metrics.same_file_reread_detected)
        self.assertEqual(("demo_app/string_tools.py",), metrics.same_file_reread_paths)

    def test_eval_runner_classifies_missing_relative_path_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(MissingPathEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("missing_relative_path", report.failure_reason)

    def test_eval_runner_classifies_directory_path_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(DirectoryPathEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("directory_path", report.failure_reason)

    def test_eval_runner_classifies_invalid_finish_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(InvalidFinishEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("invalid_finish", report.failure_reason)

    def test_eval_runner_classifies_edit_without_read_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(EditWithoutReadEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("edit_without_read", report.failure_reason)

    def test_eval_runner_classifies_model_transport_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(TransportFailureEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("model_transport_failed", report.failure_reason)

    def test_eval_runner_classifies_readme_reread_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(ReadmeRereadEvalModelClient(), max_output_retries=0),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("readme_reread", report.failure_reason)

    def test_eval_runner_classifies_same_file_reread_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(SameFileRereadEvalModelClient(), max_output_retries=0),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("same_file_reread", report.failure_reason)

    def test_eval_runner_classifies_off_target_edit_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(OffTargetEditEvalModelClient(), max_output_retries=0),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("runner_failed", report.stop_reason)
        self.assertEqual("off_target_edit", report.failure_reason)

    def test_eval_runner_classifies_bad_patch_target_failures(self):
        runner = EvalRunner(
            agent_runner=AgentRunner(BadPatchTargetEvalModelClient()),
            approval_mode=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        )

        report = runner.run_case(get_builtin_eval_case("slug_join"))

        self.assertFalse(report.success)
        self.assertEqual("tool_failed", report.stop_reason)
        self.assertEqual("bad_patch_target", report.failure_reason)


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
