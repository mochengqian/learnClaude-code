from __future__ import annotations

from typing import Dict, List, Sequence

from .eval_types import (
    ContextBundleCaseMetrics,
    ContextBundleSuiteMetrics,
    EvalCaseReport,
)
from .models import ToolExecutionResult
from .session import TaskSession


TRANSPORT_FAILURE_KEYWORDS = (
    "connection aborted",
    "connection reset",
    "eof occurred in violation of protocol",
    "remote end closed connection",
    "temporary failure",
    "timed out",
    "timeout",
    "unexpected eof",
)
TRANSPORT_FAILURE_HTTP_CODES = (
    "http 408",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


def count_agent_steps(session: TaskSession) -> int:
    return sum(
        1
        for event in session.timeline
        if event.event_type in {"agent_step_decided", "agent_step_finished"}
    )


def collect_context_bundle_case_metrics(session: TaskSession) -> ContextBundleCaseMetrics:
    read_paths: List[str] = []
    repeated_paths: List[str] = []
    read_counts: Dict[str, int] = {}

    for event in session.timeline:
        if event.event_type != "tool_executed":
            continue
        if event.payload.get("tool_name") != "read_file":
            continue
        request = event.payload.get("request") or {}
        relative_path = str(request.get("relative_path") or "").strip()
        if not relative_path:
            continue
        read_paths.append(relative_path)
        read_counts[relative_path] = read_counts.get(relative_path, 0) + 1
        if read_counts[relative_path] == 2:
            repeated_paths.append(relative_path)

    return ContextBundleCaseMetrics(
        read_file_calls=len(read_paths),
        duplicate_read_file_calls=max(0, len(read_paths) - len(read_counts)),
        same_file_reread_detected=bool(repeated_paths),
        same_file_reread_paths=tuple(repeated_paths),
    )


def aggregate_context_bundle_suite_metrics(
    case_reports: Sequence[EvalCaseReport],
) -> ContextBundleSuiteMetrics:
    if not case_reports:
        return ContextBundleSuiteMetrics(
            average_read_file_calls=0.0,
            average_duplicate_read_file_calls=0.0,
            cases_with_same_file_rereads=0,
        )

    total_read_calls = sum(
        report.context_bundle_metrics.read_file_calls for report in case_reports
    )
    total_duplicate_reads = sum(
        report.context_bundle_metrics.duplicate_read_file_calls for report in case_reports
    )
    cases_with_same_file_rereads = sum(
        1
        for report in case_reports
        if report.context_bundle_metrics.same_file_reread_detected
    )
    case_count = len(case_reports)
    return ContextBundleSuiteMetrics(
        average_read_file_calls=round(total_read_calls / case_count, 2),
        average_duplicate_read_file_calls=round(
            total_duplicate_reads / case_count, 2
        ),
        cases_with_same_file_rereads=cases_with_same_file_rereads,
    )


def is_successful(
    stop_reason: str, verification: ToolExecutionResult, session: TaskSession
) -> bool:
    if verification.status != "executed":
        return False
    if verification.exit_code not in {0, None}:
        return False
    if session.pending_approvals:
        return False
    return stop_reason in {"finished", "max_steps_reached"}


def derive_failure_reason(
    *,
    stop_reason: str,
    verification: ToolExecutionResult,
    last_failure_message: str,
) -> str:
    if stop_reason == "approval_required":
        return _classify_approval_required(last_failure_message)
    if stop_reason == "max_steps_reached":
        return "max_steps_reached"
    if stop_reason == "tool_blocked":
        return "tool_blocked"
    if stop_reason == "tool_failed":
        return _classify_tool_failure(last_failure_message)
    if verification.status != "executed":
        return "verification_failed"
    if verification.exit_code not in {0, None}:
        return "verification_failed"
    return "runner_failed"


def classify_runner_failure(last_failure_message: str) -> str:
    message = last_failure_message.strip().lower()
    if not message:
        return "runner_failed"

    if _is_model_transport_failure(message):
        return "model_transport_failed"
    if "rereading readme.md" in message:
        return "readme_reread"
    if "recent context for that file is already available" in message:
        return "same_file_reread"
    if "off-target edit path for" in message:
        return "off_target_edit"
    if "relative_path is required" in message:
        return "missing_relative_path"
    if "selected shell for a local test command" in message:
        return "shell_tool_misuse"
    if "selected shell to read a repo file directly" in message:
        return "shell_tool_misuse"
    if "edit without recent file context for file_patch" in message:
        return "edit_without_read"
    if "edit without recent file context for write_file" in message:
        return "edit_without_read"
    if "directory path for" in message:
        return "directory_path"
    if "missing repo file for" in message:
        return "missing_repo_file"
    if "invalid finish action" in message:
        return "invalid_finish"
    if (
        "invalid json" in message
        or "did not return a json object" in message
        or "unsupported action" in message
        or "tool_request without a tool_request object" in message
        or "invalid tool_request" in message
    ):
        return "invalid_model_output"
    return "runner_failed"


def _classify_tool_failure(last_failure_message: str) -> str:
    message = last_failure_message.strip().lower()
    if "expected_old_snippet" in message:
        return "bad_patch"
    if "file_patch produced no changes for readme.md" in message:
        return "off_target_edit"
    if "file_patch produced no changes for " in message:
        return "bad_patch_target"
    return "tool_failed"


def _classify_approval_required(last_failure_message: str) -> str:
    message = last_failure_message.strip().lower()
    if "editing files requires user approval" in message:
        return "edit_approval_required"
    if "shell command requires explicit approval" in message:
        return "shell_approval_required"
    if "unknown test command requires approval" in message:
        return "test_approval_required"
    return "approval_required"


def _is_model_transport_failure(message: str) -> bool:
    if "model request failed" not in message:
        return False
    if any(keyword in message for keyword in TRANSPORT_FAILURE_KEYWORDS):
        return True
    return any(code in message for code in TRANSPORT_FAILURE_HTTP_CODES)


def stop_reason_for_result(result: ToolExecutionResult) -> str:
    if result.status == "approval_required":
        return "approval_required"
    if result.status in {"denied", "rejected"}:
        return "tool_blocked"
    if result.status == "failed":
        return "tool_failed"
    return "continue"
