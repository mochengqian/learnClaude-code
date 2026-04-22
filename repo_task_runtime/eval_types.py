from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


APPROVAL_MODE_AUTO_APPROVE_EDITS = "auto_approve_edits"
APPROVAL_MODE_STOP_ON_REQUEST = "stop_on_request"
SUPPORTED_APPROVAL_MODES = (
    APPROVAL_MODE_AUTO_APPROVE_EDITS,
    APPROVAL_MODE_STOP_ON_REQUEST,
)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    display_name: str
    template_dir_name: str
    task_input: str
    test_command: Tuple[str, ...]
    notes: str
    max_steps: int = 8

    @property
    def template_dir(self) -> Path:
        return (
            Path(__file__).resolve().parent.parent
            / "examples"
            / "eval_repo_templates"
            / self.template_dir_name
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "display_name": self.display_name,
            "template_dir": str(self.template_dir),
            "task_input": self.task_input,
            "test_command": list(self.test_command),
            "notes": self.notes,
            "max_steps": self.max_steps,
        }


@dataclass(frozen=True)
class ContextBundleCaseMetrics:
    read_file_calls: int
    duplicate_read_file_calls: int
    same_file_reread_detected: bool
    same_file_reread_paths: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "read_file_calls": self.read_file_calls,
            "duplicate_read_file_calls": self.duplicate_read_file_calls,
            "same_file_reread_detected": self.same_file_reread_detected,
            "same_file_reread_paths": list(self.same_file_reread_paths),
        }


@dataclass(frozen=True)
class ContextBundleSuiteMetrics:
    average_read_file_calls: float
    average_duplicate_read_file_calls: float
    cases_with_same_file_rereads: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "average_read_file_calls": self.average_read_file_calls,
            "average_duplicate_read_file_calls": self.average_duplicate_read_file_calls,
            "cases_with_same_file_rereads": self.cases_with_same_file_rereads,
        }


@dataclass
class EvalCaseReport:
    case_id: str
    display_name: str
    repo_path: str
    success: bool
    stop_reason: str
    failure_reason: Optional[str]
    steps_completed: int
    max_steps: int
    approvals_auto_resolved: int
    verification_status: str
    verification_exit_code: Optional[int]
    verification_message: str
    latest_diff_chars: int
    last_failure_message: str = ""
    plan_generated: bool = False
    todo_count: int = 0
    context_bundle_metrics: ContextBundleCaseMetrics = field(
        default_factory=lambda: ContextBundleCaseMetrics(
            read_file_calls=0,
            duplicate_read_file_calls=0,
            same_file_reread_detected=False,
            same_file_reread_paths=(),
        )
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "display_name": self.display_name,
            "repo_path": self.repo_path,
            "success": self.success,
            "stop_reason": self.stop_reason,
            "failure_reason": self.failure_reason,
            "steps_completed": self.steps_completed,
            "max_steps": self.max_steps,
            "approvals_auto_resolved": self.approvals_auto_resolved,
            "verification_status": self.verification_status,
            "verification_exit_code": self.verification_exit_code,
            "verification_message": self.verification_message,
            "latest_diff_chars": self.latest_diff_chars,
            "last_failure_message": self.last_failure_message,
            "plan_generated": self.plan_generated,
            "todo_count": self.todo_count,
            "context_bundle_metrics": self.context_bundle_metrics.to_dict(),
        }


@dataclass
class EvalSuiteReport:
    approval_mode: str
    case_reports: Sequence[EvalCaseReport]
    passed_cases: int
    failed_cases: int
    average_steps: float
    failure_reason_counts: Dict[str, int] = field(default_factory=dict)
    context_bundle_metrics: ContextBundleSuiteMetrics = field(
        default_factory=lambda: ContextBundleSuiteMetrics(
            average_read_file_calls=0.0,
            average_duplicate_read_file_calls=0.0,
            cases_with_same_file_rereads=0,
        )
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_mode": self.approval_mode,
            "total_cases": len(self.case_reports),
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "average_steps": self.average_steps,
            "failure_reason_counts": self.failure_reason_counts,
            "context_bundle_metrics": self.context_bundle_metrics.to_dict(),
            "cases": [report.to_dict() for report in self.case_reports],
        }
