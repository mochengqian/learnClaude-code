from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from repo_task_runtime import (
    APPROVAL_MODE_AUTO_APPROVE_EDITS,
    APPROVAL_MODE_STOP_ON_REQUEST,
    AgentRunner,
    TaskWorkbench,
    TestCommandRequest,
    create_model_client_from_env,
)
from repo_task_runtime.eval_metrics import (
    classify_runner_failure,
    collect_context_bundle_case_metrics,
    count_agent_steps,
    derive_failure_reason,
    is_successful,
    stop_reason_for_result,
)
from repo_task_runtime.git_repo import initialize_git_repo


FULL_TEST_COMMAND: Tuple[str, ...] = (
    "python3",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-v",
)
PILOT_SENTINEL_FILENAME = ".repo_task_real_repo_pilot_case.json"

README_PROVIDER_CHECKPOINT_CURRENT = "- 当前远端 checkpoint：`effc35b`。"
README_PROVIDER_CHECKPOINT_STALE = "- 当前远端 checkpoint：`fa64829`。"
MODEL_CLIENT_COMMENT_PLACEHOLDER = (
    "    # TODO: explain accepted provider content shapes.\n"
    "    def _coerce_assistant_content(self, content: object) -> str:\n"
)
MODEL_CLIENT_COMMENT_EXPECTED = (
    "    # Providers may return plain strings, text objects, or text-part arrays.\n"
    "    def _coerce_assistant_content(self, content: object) -> str:\n"
)
PLAN_INVALID_OUTPUT_BLOCK_FIXED = (
    '    if "plan output invalid" in message:\n'
    '        return "plan_invalid_output"\n'
)
PLAN_INVALID_OUTPUT_BLOCK_BROKEN = (
    '    if "plan output invalid" in message:\n'
    '        return "invalid_model_output"\n'
)


def _noop_setup(_: Path) -> None:
    return None


@dataclass(frozen=True)
class RealRepoPilotCase:
    case_id: str
    display_name: str
    task_input: str
    setup: Callable[[Path], None] = _noop_setup
    test_command: Tuple[str, ...] = FULL_TEST_COMMAND
    max_steps: int = 8


def builtin_real_repo_pilot_cases() -> List[RealRepoPilotCase]:
    return [
        RealRepoPilotCase(
            case_id="readme_provider_checkpoint_refresh",
            display_name="README Provider Checkpoint Refresh",
            task_input=(
                "Update README.md only: in the M4 provider-stability closeout section, "
                "change the current remote checkpoint from fa64829 to effc35b. "
                "Do not edit runtime code. Run the full unittest suite before finishing."
            ),
            setup=_setup_readme_provider_checkpoint_refresh,
        ),
        RealRepoPilotCase(
            case_id="provider_content_comment_single_file",
            display_name="Provider Content Comment Single File",
            task_input=(
                "Edit repo_task_runtime/model_client.py only. Replace the placeholder "
                "comment above _coerce_assistant_content with one short note that "
                "providers may return plain strings, text objects, or text-part arrays. "
                "Do not change behavior. Run the full unittest suite before finishing."
            ),
            setup=_setup_provider_content_comment_single_file,
        ),
        RealRepoPilotCase(
            case_id="failing_test_points_to_source_real",
            display_name="Failing Test Points To Source Real",
            task_input=(
                "Fix the failing plan_invalid_output taxonomy regression. First run "
                "the full unittest suite, then inspect tests/test_eval_pack.py and "
                "repo_task_runtime/eval_metrics.py. Edit only "
                "repo_task_runtime/eval_metrics.py, and run the full unittest suite "
                "before finishing."
            ),
            setup=_setup_plan_invalid_output_regression,
        ),
    ]


def get_builtin_real_repo_pilot_case(case_id: str) -> RealRepoPilotCase:
    for case in builtin_real_repo_pilot_cases():
        if case.case_id == case_id:
            return case
    raise KeyError("Unknown real repo pilot case id: {0}".format(case_id))


class RealRepoPilotRunner:
    def __init__(
        self,
        *,
        agent_runner: AgentRunner,
        approval_mode: str = APPROVAL_MODE_AUTO_APPROVE_EDITS,
        max_steps_override: Optional[int] = None,
        source_repo: Path = ROOT_DIR,
    ) -> None:
        if approval_mode not in {
            APPROVAL_MODE_AUTO_APPROVE_EDITS,
            APPROVAL_MODE_STOP_ON_REQUEST,
        }:
            raise ValueError("Unsupported approval_mode: {0}".format(approval_mode))
        self.agent_runner = agent_runner
        self.approval_mode = approval_mode
        self.max_steps_override = max_steps_override
        self.source_repo = Path(source_repo).resolve()

    def run_case(self, case: RealRepoPilotCase) -> Dict[str, object]:
        with tempfile.TemporaryDirectory(
            prefix="repo-task-real-pilot-{0}-".format(case.case_id)
        ) as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            _copy_source_repo(self.source_repo, repo_path)
            case.setup(repo_path)
            _write_pilot_sentinel(repo_path, case)
            initialize_git_repo(
                repo_path,
                user_email="real-pilot@example.com",
                user_name="Repo Task Real Pilot",
                initial_commit_message="Initial real repo pilot state",
            )

            session = TaskWorkbench().create_session(repo_path)
            stop_reason = "runner_failed"
            last_failure_message = ""
            approvals_auto_resolved = 0
            plan_generated = False

            try:
                session.begin_task(case.task_input)
                self.agent_runner.draft_plan(session)
                plan_generated = True
                session.approve_plan()
                session.record_event(
                    "real_repo_pilot_case_started",
                    case_id=case.case_id,
                    display_name=case.display_name,
                    approval_mode=self.approval_mode,
                    max_steps=self._max_steps_for_case(case),
                )

                for _ in range(self._max_steps_for_case(case)):
                    outcome = self.agent_runner.run_next_step(session)

                    if outcome.decision.action == "finish":
                        stop_reason = "finished"
                        break

                    tool_result = outcome.tool_result
                    if tool_result is None:
                        continue

                    if tool_result.status == "approval_required":
                        if self.approval_mode == APPROVAL_MODE_STOP_ON_REQUEST:
                            stop_reason = "approval_required"
                            last_failure_message = tool_result.message
                            break

                        approval_result = session.resolve_approval(
                            tool_result.approval_id, approve=True
                        )
                        approvals_auto_resolved += 1
                        session.record_event(
                            "real_repo_pilot_auto_approved",
                            case_id=case.case_id,
                            approval_id=tool_result.approval_id,
                            tool_name=approval_result.tool_name,
                        )
                        if approval_result.status != "executed":
                            stop_reason = stop_reason_for_result(approval_result)
                            last_failure_message = approval_result.message
                            break
                        continue

                    stop_reason = stop_reason_for_result(tool_result)
                    if stop_reason != "continue":
                        last_failure_message = tool_result.message
                        break
                else:
                    stop_reason = "max_steps_reached"

                verification = session.request_tool(
                    TestCommandRequest(command=case.test_command)
                )
                success = is_successful(stop_reason, verification, session)
                failure_reason = None
                if not success:
                    failure_reason = derive_failure_reason(
                        stop_reason=stop_reason,
                        verification=verification,
                        last_failure_message=last_failure_message,
                        session=session,
                    )
                context_bundle_metrics = collect_context_bundle_case_metrics(session)
                return {
                    "case_id": case.case_id,
                    "display_name": case.display_name,
                    "task_input": case.task_input,
                    "success": success,
                    "stop_reason": stop_reason,
                    "failure_reason": failure_reason,
                    "last_failure_message": last_failure_message,
                    "steps_completed": count_agent_steps(session),
                    "max_steps": self._max_steps_for_case(case),
                    "approvals_auto_resolved": approvals_auto_resolved,
                    "plan_generated": plan_generated,
                    "verification_status": verification.status,
                    "verification_exit_code": verification.exit_code,
                    "latest_diff_chars": len(session.latest_diff),
                    "read_file_calls": context_bundle_metrics.read_file_calls,
                    "duplicate_read_file_calls": (
                        context_bundle_metrics.duplicate_read_file_calls
                    ),
                }
            except Exception as exc:
                context_bundle_metrics = collect_context_bundle_case_metrics(session)
                return {
                    "case_id": case.case_id,
                    "display_name": case.display_name,
                    "task_input": case.task_input,
                    "success": False,
                    "stop_reason": "runner_failed",
                    "failure_reason": classify_runner_failure(str(exc)),
                    "last_failure_message": str(exc),
                    "steps_completed": count_agent_steps(session),
                    "max_steps": self._max_steps_for_case(case),
                    "approvals_auto_resolved": approvals_auto_resolved,
                    "plan_generated": plan_generated,
                    "verification_status": "not_run",
                    "verification_exit_code": None,
                    "latest_diff_chars": len(session.latest_diff),
                    "read_file_calls": context_bundle_metrics.read_file_calls,
                    "duplicate_read_file_calls": (
                        context_bundle_metrics.duplicate_read_file_calls
                    ),
                }

    def run_cases(self, cases: Iterable[RealRepoPilotCase]) -> Dict[str, object]:
        case_reports = [self.run_case(case) for case in cases]
        passed_cases = sum(1 for report in case_reports if report["success"])
        average_steps = 0.0
        average_read_file_calls = 0.0
        average_duplicate_reads = 0.0
        if case_reports:
            average_steps = sum(
                int(report["steps_completed"]) for report in case_reports
            ) / len(case_reports)
            average_read_file_calls = sum(
                int(report["read_file_calls"]) for report in case_reports
            ) / len(case_reports)
            average_duplicate_reads = sum(
                int(report["duplicate_read_file_calls"]) for report in case_reports
            ) / len(case_reports)
        failure_reason_counts: Dict[str, int] = {}
        for report in case_reports:
            failure_reason = str(report.get("failure_reason") or "").strip()
            if not failure_reason:
                continue
            failure_reason_counts[failure_reason] = (
                failure_reason_counts.get(failure_reason, 0) + 1
            )
        return {
            "suite": "real_repo_pilot",
            "source_repo": str(self.source_repo),
            "approval_mode": self.approval_mode,
            "passed_cases": passed_cases,
            "total_cases": len(case_reports),
            "average_steps": round(average_steps, 2),
            "average_read_file_calls": round(average_read_file_calls, 2),
            "average_duplicate_reads": round(average_duplicate_reads, 2),
            "failure_reason_counts": failure_reason_counts,
            "cases": case_reports,
        }

    def _max_steps_for_case(self, case: RealRepoPilotCase) -> int:
        if self.max_steps_override is not None:
            return self.max_steps_override
        return case.max_steps


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the built-in real repo pilot cases on temporary repo copies."
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run one or more specific case ids. Defaults to all built-in real repo cases.",
    )
    parser.add_argument(
        "--approval-mode",
        choices=("auto_approve_edits", "stop_on_request"),
        default=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        help="How to handle edit approvals during real repo pilot execution.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Override the per-case max step limit.",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="List the built-in real repo pilot cases and exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the real repo pilot report as JSON.",
    )
    args = parser.parse_args()

    cases = builtin_real_repo_pilot_cases()
    if args.case_ids:
        cases = [get_builtin_real_repo_pilot_case(case_id) for case_id in args.case_ids]

    if args.list_cases:
        for case in cases:
            print("{0}: {1}".format(case.case_id, case.task_input))
        return 0

    model_client = create_model_client_from_env()
    if model_client is None:
        print(
            "Model client is not configured. Set REPO_TASK_MODEL_BASE_URL, "
            "REPO_TASK_MODEL_API_KEY, and REPO_TASK_MODEL_NAME.",
            file=sys.stderr,
        )
        return 2

    runner = RealRepoPilotRunner(
        agent_runner=AgentRunner(model_client),
        approval_mode=args.approval_mode,
        max_steps_override=args.max_steps,
    )
    report = runner.run_cases(cases)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(
        "Real repo pilot completed: {0}/{1} passed, avg_steps={2}, avg_reads={3}, "
        "avg_duplicate_reads={4}, approval_mode={5}".format(
            report["passed_cases"],
            report["total_cases"],
            report["average_steps"],
            report["average_read_file_calls"],
            report["average_duplicate_reads"],
            report["approval_mode"],
        )
    )
    print(
        "Failure taxonomy: {0}".format(
            json.dumps(report["failure_reason_counts"], sort_keys=True)
        )
    )
    for case in report["cases"]:
        status = "PASS" if case["success"] else "FAIL"
        print(
            "- {0} [{1}] steps={2}/{3} reads={4} dup_reads={5} stop={6} "
            "failure={7} verify={8}".format(
                case["case_id"],
                status,
                case["steps_completed"],
                case["max_steps"],
                case["read_file_calls"],
                case["duplicate_read_file_calls"],
                case["stop_reason"],
                case["failure_reason"] or "-",
                case["verification_exit_code"],
            )
        )
    return 0


def _copy_source_repo(source_repo: Path, repo_path: Path) -> None:
    shutil.copytree(
        source_repo,
        repo_path,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".DS_Store",
        ),
    )


def _write_pilot_sentinel(repo_path: Path, case: RealRepoPilotCase) -> None:
    sentinel_path = repo_path / PILOT_SENTINEL_FILENAME
    sentinel_path.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "display_name": case.display_name,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _setup_readme_provider_checkpoint_refresh(repo_path: Path) -> None:
    _replace_once(
        repo_path / "README.md",
        README_PROVIDER_CHECKPOINT_CURRENT,
        README_PROVIDER_CHECKPOINT_STALE,
    )


def _setup_provider_content_comment_single_file(repo_path: Path) -> None:
    _replace_once(
        repo_path / "repo_task_runtime" / "model_client.py",
        "    def _coerce_assistant_content(self, content: object) -> str:\n",
        MODEL_CLIENT_COMMENT_PLACEHOLDER,
    )


def _setup_plan_invalid_output_regression(repo_path: Path) -> None:
    _replace_once(
        repo_path / "repo_task_runtime" / "eval_metrics.py",
        PLAN_INVALID_OUTPUT_BLOCK_FIXED,
        PLAN_INVALID_OUTPUT_BLOCK_BROKEN,
    )


def _replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    if old not in content:
        raise ValueError("Expected text not found in {0}".format(path))
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
