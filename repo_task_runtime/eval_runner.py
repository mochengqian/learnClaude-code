from __future__ import annotations

from typing import Dict, Iterable, Optional

from .agent import AgentRunner
from .eval_cases import create_eval_repo
from .eval_metrics import (
    aggregate_context_bundle_suite_metrics,
    collect_context_bundle_case_metrics,
    count_agent_steps,
    derive_failure_reason,
    is_successful,
    stop_reason_for_result,
)
from .eval_types import (
    APPROVAL_MODE_AUTO_APPROVE_EDITS,
    APPROVAL_MODE_STOP_ON_REQUEST,
    EvalCase,
    EvalCaseReport,
    EvalSuiteReport,
    SUPPORTED_APPROVAL_MODES,
)
from .models import TestCommandRequest
from .workbench import TaskWorkbench


class EvalRunner:
    def __init__(
        self,
        agent_runner: AgentRunner,
        approval_mode: str = APPROVAL_MODE_AUTO_APPROVE_EDITS,
        max_steps_override: Optional[int] = None,
    ) -> None:
        if approval_mode not in SUPPORTED_APPROVAL_MODES:
            raise ValueError(
                "Unsupported approval_mode: {0}".format(approval_mode)
            )
        self.agent_runner = agent_runner
        self.approval_mode = approval_mode
        self.max_steps_override = max_steps_override

    def run_case(self, case: EvalCase) -> EvalCaseReport:
        repo_path = create_eval_repo(case)
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
                "eval_case_started",
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
                        "eval_auto_approved",
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
                )
            session.record_event(
                "eval_case_completed",
                case_id=case.case_id,
                success=success,
                stop_reason=stop_reason,
                failure_reason=failure_reason,
                verification_exit_code=verification.exit_code,
            )
            context_bundle_metrics = collect_context_bundle_case_metrics(session)
            return EvalCaseReport(
                case_id=case.case_id,
                display_name=case.display_name,
                repo_path=str(repo_path),
                success=success,
                stop_reason=stop_reason,
                failure_reason=failure_reason,
                steps_completed=count_agent_steps(session),
                max_steps=self._max_steps_for_case(case),
                approvals_auto_resolved=approvals_auto_resolved,
                verification_status=verification.status,
                verification_exit_code=verification.exit_code,
                verification_message=verification.message,
                latest_diff_chars=len(session.latest_diff),
                last_failure_message=last_failure_message,
                plan_generated=plan_generated,
                todo_count=len(session.todos),
                context_bundle_metrics=context_bundle_metrics,
            )
        except Exception as exc:
            context_bundle_metrics = collect_context_bundle_case_metrics(session)
            return EvalCaseReport(
                case_id=case.case_id,
                display_name=case.display_name,
                repo_path=str(repo_path),
                success=False,
                stop_reason="runner_failed",
                failure_reason="runner_failed",
                steps_completed=count_agent_steps(session),
                max_steps=self._max_steps_for_case(case),
                approvals_auto_resolved=approvals_auto_resolved,
                verification_status="not_run",
                verification_exit_code=None,
                verification_message="Verification was not run.",
                latest_diff_chars=len(session.latest_diff),
                last_failure_message=str(exc),
                plan_generated=plan_generated,
                todo_count=len(session.todos),
                context_bundle_metrics=context_bundle_metrics,
            )

    def run_cases(self, cases: Iterable[EvalCase]) -> EvalSuiteReport:
        case_reports = [self.run_case(case) for case in cases]
        passed_cases = sum(1 for report in case_reports if report.success)
        failed_cases = len(case_reports) - passed_cases
        average_steps = 0.0
        if case_reports:
            average_steps = sum(report.steps_completed for report in case_reports) / len(
                case_reports
            )
        failure_reason_counts: Dict[str, int] = {}
        for report in case_reports:
            if not report.failure_reason:
                continue
            failure_reason_counts[report.failure_reason] = (
                failure_reason_counts.get(report.failure_reason, 0) + 1
            )
        context_bundle_metrics = aggregate_context_bundle_suite_metrics(case_reports)
        return EvalSuiteReport(
            approval_mode=self.approval_mode,
            case_reports=case_reports,
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            average_steps=round(average_steps, 2),
            failure_reason_counts=failure_reason_counts,
            context_bundle_metrics=context_bundle_metrics,
        )

    def _max_steps_for_case(self, case: EvalCase) -> int:
        if self.max_steps_override is not None:
            return self.max_steps_override
        return case.max_steps
