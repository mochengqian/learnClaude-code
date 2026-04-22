from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional
from uuid import uuid4

from .approval import ApprovalPolicy
from .diffing import build_unified_diff, repo_git_diff
from .models import (
    ApprovalRequest,
    FilePatchRequest,
    FileReadRequest,
    PermissionMode,
    RecentFileContext,
    RecentTestFailure,
    ShellCommandRequest,
    SuccessfulTestRun,
    TaskSnapshot,
    TestCommandRequest,
    TimelineEvent,
    TodoItem,
    TodoStatus,
    ToolExecutionResult,
    ToolInvocationRequest,
    WriteFileRequest,
    request_summary,
    tool_name_for_request,
)
import subprocess


class TaskSession:
    def __init__(
        self,
        repo_path: Path,
        session_id: Optional[str] = None,
        approval_policy: Optional[ApprovalPolicy] = None,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.session_id = session_id or uuid4().hex
        self.approval_policy = approval_policy or ApprovalPolicy()
        self.task_input: Optional[str] = None
        self.permission_mode = PermissionMode.DEFAULT
        self.plan: Optional[str] = None
        self.todos: List[TodoItem] = []
        self.latest_diff = ""
        self.latest_tool_result: Optional[ToolExecutionResult] = None
        self.recent_file_contexts: List[RecentFileContext] = []
        self.recent_test_failures: List[RecentTestFailure] = []
        self.latest_successful_test: Optional[SuccessfulTestRun] = None
        self.repo_state_revision = 0
        self.timeline: List[TimelineEvent] = []
        self.pending_approvals: Dict[str, ApprovalRequest] = {}

        if not self.repo_path.exists():
            raise ValueError("Repo path does not exist: {0}".format(self.repo_path))
        if not self.repo_path.is_dir():
            raise ValueError("Repo path must be a directory: {0}".format(self.repo_path))

    def begin_task(self, task_input: str) -> None:
        task_input = task_input.strip()
        if not task_input:
            raise ValueError("Task input cannot be empty.")
        self._reset_task_state()
        self.task_input = task_input
        self.permission_mode = PermissionMode.PLAN
        self._record("task_received", task_input=task_input)
        self._record("plan_mode_entered", permission_mode=self.permission_mode.value)

    def update_plan(self, plan_markdown: str) -> None:
        plan_markdown = plan_markdown.strip()
        if self.permission_mode != PermissionMode.PLAN:
            raise ValueError("Plan can only be updated while the session is in plan mode.")
        if not plan_markdown:
            raise ValueError("Plan cannot be empty.")
        self.plan = plan_markdown
        self._record("plan_updated", plan=plan_markdown)

    def approve_plan(self) -> None:
        if self.permission_mode != PermissionMode.PLAN:
            raise ValueError("Session is not in plan mode.")
        if not self.plan:
            raise ValueError("Cannot exit plan mode without a plan.")
        self.permission_mode = PermissionMode.DEFAULT
        self._record("plan_mode_exited", permission_mode=self.permission_mode.value)

    def replace_todos(self, todos: Iterable[TodoItem]) -> None:
        normalized = [todo.normalized() for todo in todos]
        in_progress_count = sum(
            1 for todo in normalized if todo.status == TodoStatus.IN_PROGRESS
        )
        if in_progress_count > 1:
            raise ValueError("Only one todo item can be in progress at a time.")

        all_completed = bool(normalized) and all(
            todo.status == TodoStatus.COMPLETED for todo in normalized
        )
        self.todos = [] if all_completed else normalized
        self._record(
            "todos_replaced",
            todos=[todo.to_dict() for todo in normalized],
            cleared=all_completed,
        )

    def request_tool(self, request: ToolInvocationRequest) -> ToolExecutionResult:
        decision = self.approval_policy.evaluate(self.permission_mode, request)
        tool_name = tool_name_for_request(request)

        if decision.behavior == "deny":
            self._record(
                "tool_denied",
                tool_name=tool_name,
                reason=decision.reason,
                request=request_summary(request),
            )
            result = ToolExecutionResult(
                status="denied",
                tool_name=tool_name,
                message=decision.reason,
            )
            self.latest_tool_result = result
            return result

        if decision.behavior == "ask":
            approval_id = uuid4().hex
            approval = ApprovalRequest(
                approval_id=approval_id,
                tool_name=tool_name,
                reason=decision.reason,
                request=request,
            )
            self.pending_approvals[approval_id] = approval
            self._record(
                "approval_requested",
                approval=approval.to_dict(),
            )
            result = ToolExecutionResult(
                status="approval_required",
                tool_name=tool_name,
                message=decision.reason,
                approval_id=approval_id,
            )
            self.latest_tool_result = result
            return result

        return self._run_execute_request(request, approved_by=None)

    def resolve_approval(self, approval_id: str, approve: bool) -> ToolExecutionResult:
        approval = self.pending_approvals.pop(approval_id, None)
        if approval is None:
            raise ValueError("Unknown approval id: {0}".format(approval_id))

        if not approve:
            self._record(
                "approval_rejected",
                approval_id=approval_id,
                tool_name=approval.tool_name,
            )
            result = ToolExecutionResult(
                status="rejected",
                tool_name=approval.tool_name,
                message="User rejected the approval request.",
                approval_id=approval_id,
            )
            self.latest_tool_result = result
            return result

        self._record(
            "approval_granted",
            approval_id=approval_id,
            tool_name=approval.tool_name,
        )
        return self._run_execute_request(approval.request, approved_by=approval_id)

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot(
            session_id=self.session_id,
            repo_path=str(self.repo_path),
            task_input=self.task_input,
            permission_mode=self.permission_mode.value,
            plan=self.plan,
            todos=list(self.todos),
            latest_diff=self.latest_diff,
            timeline=list(self.timeline),
            pending_approvals=list(self.pending_approvals.values()),
            latest_tool_result=self.latest_tool_result,
            latest_successful_test=self.latest_successful_test,
        )

    def record_event(self, event_type: str, **payload: object) -> None:
        self._record(event_type, **payload)

    def has_successful_test_for_current_state(self) -> bool:
        if self.latest_successful_test is None:
            return False
        return (
            self.latest_successful_test.repo_state_revision == self.repo_state_revision
        )

    def finish_block_reason(self) -> str:
        return (
            "Cannot finish before a local test has passed for the current repo state."
        )

    def validate_tool_request_path(self, request: ToolInvocationRequest) -> Optional[str]:
        if isinstance(request, FileReadRequest):
            return self._validate_repo_file_path(
                request.relative_path,
                tool_name="read_file",
                must_exist=True,
            )
        if isinstance(request, FilePatchRequest):
            return self._validate_repo_file_path(
                request.relative_path,
                tool_name="file_patch",
                must_exist=True,
            )
        if isinstance(request, WriteFileRequest):
            return self._validate_repo_file_path(
                request.relative_path,
                tool_name="write_file",
                must_exist=False,
            )
        return None

    def validate_tool_request_edit_context(
        self, request: ToolInvocationRequest
    ) -> Optional[str]:
        if isinstance(request, FilePatchRequest):
            if self._has_recent_file_context(request.relative_path):
                return None
            return (
                "Model attempted to edit without recent file context for file_patch: "
                "{0}. Read the target file before editing.".format(
                    request.relative_path
                )
            )

        if isinstance(request, WriteFileRequest):
            candidate = self._resolve_repo_path(request.relative_path)
            if not candidate.exists():
                return None
            if self._has_recent_file_context(request.relative_path):
                return None
            return (
                "Model attempted to edit without recent file context for write_file: "
                "{0}. Read the target file before editing.".format(
                    request.relative_path
                )
            )

        return None

    def _execute_request(
        self, request: ToolInvocationRequest, approved_by: Optional[str]
    ) -> ToolExecutionResult:
        tool_name = tool_name_for_request(request)
        repo_state_mutated = False

        if isinstance(request, FileReadRequest):
            resolved_path = self._resolve_repo_path(request.relative_path)
            content = resolved_path.read_text(encoding="utf-8")
            self._remember_file_context(
                relative_path=request.relative_path,
                content=content,
                source_tool=tool_name,
            )
            result = ToolExecutionResult(
                status="executed",
                tool_name=tool_name,
                message="Read file successfully.",
                data={
                    "relative_path": request.relative_path,
                    "content": content,
                },
            )
        elif isinstance(request, FilePatchRequest):
            resolved_path = self._resolve_repo_path(request.relative_path)
            if not resolved_path.exists():
                raise ValueError(
                    "Cannot patch a missing file: {0}".format(request.relative_path)
                )
            old_content = resolved_path.read_text(encoding="utf-8")
            occurrences = old_content.count(request.expected_old_snippet)
            if occurrences == 0:
                raise ValueError(
                    "expected_old_snippet was not found in {0}".format(
                        request.relative_path
                    )
                )
            if occurrences > 1 and not request.replace_all:
                raise ValueError(
                    "expected_old_snippet matched multiple locations in {0}; "
                    "set replace_all=true or use a more specific snippet.".format(
                        request.relative_path
                    )
                )
            if request.replace_all:
                new_content = old_content.replace(
                    request.expected_old_snippet, request.new_snippet
                )
                replacements = occurrences
            else:
                new_content = old_content.replace(
                    request.expected_old_snippet, request.new_snippet, 1
                )
                replacements = 1
            if new_content == old_content:
                raise ValueError(
                    "file_patch produced no changes for {0}".format(
                        request.relative_path
                    )
                )
            resolved_path.write_text(new_content, encoding="utf-8")
            self._remember_file_context(
                relative_path=request.relative_path,
                content=new_content,
                source_tool=tool_name,
            )
            file_diff = build_unified_diff(
                request.relative_path, old_content, new_content
            )
            result = ToolExecutionResult(
                status="executed",
                tool_name=tool_name,
                message="Patched file successfully.",
                diff=file_diff,
                data={
                    "relative_path": request.relative_path,
                    "replacements": replacements,
                    "replace_all": request.replace_all,
                },
            )
            repo_state_mutated = True
        elif isinstance(request, WriteFileRequest):
            resolved_path = self._resolve_repo_path(request.relative_path)
            old_content = ""
            if resolved_path.exists():
                old_content = resolved_path.read_text(encoding="utf-8")
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_path.write_text(request.content, encoding="utf-8")
            self._remember_file_context(
                relative_path=request.relative_path,
                content=request.content,
                source_tool=tool_name,
            )
            file_diff = build_unified_diff(
                request.relative_path, old_content, request.content
            )
            result = ToolExecutionResult(
                status="executed",
                tool_name=tool_name,
                message="Wrote file successfully.",
                diff=file_diff,
                data={"relative_path": request.relative_path},
            )
            repo_state_mutated = True
        elif isinstance(request, (ShellCommandRequest, TestCommandRequest)):
            before_diff = ""
            if isinstance(request, ShellCommandRequest):
                before_diff = repo_git_diff(self.repo_path)
            completed = subprocess.run(
                list(request.command),
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
            diff = repo_git_diff(self.repo_path)
            result = ToolExecutionResult(
                status="executed",
                tool_name=tool_name,
                message="Command completed.",
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
                diff=diff,
                data={"command": list(request.command)},
            )
            if isinstance(request, TestCommandRequest):
                self._remember_test_result(
                    command=request.command,
                    exit_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            else:
                repo_state_mutated = before_diff != diff
        else:
            raise TypeError("Unsupported request: {0}".format(type(request)))

        if repo_state_mutated:
            self._mark_repo_state_mutated(tool_name)
        self.latest_tool_result = result
        self.latest_diff = repo_git_diff(self.repo_path) or result.diff
        self._record(
            "tool_executed",
            tool_name=tool_name,
            approved_by=approved_by,
            request=request_summary(request),
            exit_code=result.exit_code,
        )
        if self.latest_diff:
            self._record(
                "diff_updated",
                tool_name=tool_name,
                diff_chars=len(self.latest_diff),
            )
        if isinstance(request, TestCommandRequest):
            self._record(
                "local_test_completed",
                command=list(request.command),
                exit_code=result.exit_code,
            )
        return result

    def _run_execute_request(
        self, request: ToolInvocationRequest, approved_by: Optional[str]
    ) -> ToolExecutionResult:
        tool_name = tool_name_for_request(request)
        try:
            return self._execute_request(request, approved_by=approved_by)
        except subprocess.TimeoutExpired as exc:
            message = "Command timed out after {0} seconds.".format(exc.timeout)
        except (FileNotFoundError, OSError, ValueError) as exc:
            message = str(exc)

        self._record(
            "tool_failed",
            tool_name=tool_name,
            approved_by=approved_by,
            request=request_summary(request),
            message=message,
        )
        result = ToolExecutionResult(
            status="failed",
            tool_name=tool_name,
            message=message,
        )
        self.latest_tool_result = result
        return result

    def _record(self, event_type: str, **payload: object) -> None:
        self.timeline.append(TimelineEvent(event_type=event_type, payload=dict(payload)))

    def _reset_task_state(self) -> None:
        self.task_input = None
        self.permission_mode = PermissionMode.DEFAULT
        self.plan = None
        self.todos = []
        self.latest_diff = ""
        self.latest_tool_result = None
        self.recent_file_contexts = []
        self.recent_test_failures = []
        self.latest_successful_test = None
        self.repo_state_revision = 0
        self.timeline = []
        self.pending_approvals = {}

    def _remember_file_context(
        self, relative_path: str, content: str, source_tool: str
    ) -> None:
        self.recent_file_contexts = [
            item
            for item in self.recent_file_contexts
            if item.relative_path != relative_path
        ]
        self.recent_file_contexts.append(
            RecentFileContext(
                relative_path=relative_path,
                content=content,
                source_tool=source_tool,
            )
        )
        self.recent_file_contexts = self.recent_file_contexts[-3:]

    def _remember_test_result(
        self,
        command: tuple[str, ...],
        exit_code: Optional[int],
        stdout: str,
        stderr: str,
    ) -> None:
        if exit_code in {0, None}:
            self.recent_test_failures = []
            self.latest_successful_test = SuccessfulTestRun(
                command=command,
                exit_code=int(exit_code or 0),
                repo_state_revision=self.repo_state_revision,
            )
            return

        self.latest_successful_test = None
        self.recent_test_failures.append(
            RecentTestFailure(
                command=command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        self.recent_test_failures = self.recent_test_failures[-2:]

    def _mark_repo_state_mutated(self, tool_name: str) -> None:
        self.repo_state_revision += 1
        self.latest_successful_test = None
        self._record(
            "repo_state_mutated",
            tool_name=tool_name,
            repo_state_revision=self.repo_state_revision,
        )

    def _resolve_repo_path(self, relative_path: str) -> Path:
        candidate = (self.repo_path / relative_path).resolve()
        try:
            candidate.relative_to(self.repo_path)
        except ValueError as exc:
            raise ValueError(
                "Path escapes the repo root: {0}".format(relative_path)
            ) from exc
        return candidate

    def _validate_repo_file_path(
        self, relative_path: str, *, tool_name: str, must_exist: bool
    ) -> Optional[str]:
        try:
            candidate = self._resolve_repo_path(relative_path)
        except ValueError as exc:
            return "Model selected a path outside the repo root for {0}: {1}".format(
                tool_name, exc
            )

        if candidate.exists():
            if candidate.is_dir():
                suggestion = self._suggest_file_inside(candidate)
                message = (
                    "Model selected a directory path for {0}: {1}.".format(
                        tool_name, relative_path
                    )
                )
                if suggestion:
                    message += " Choose a file path such as {0}.".format(suggestion)
                return message
            return None

        if must_exist:
            message = "Model selected a missing repo file for {0}: {1}.".format(
                tool_name, relative_path
            )
            suggestion = self._suggest_existing_file_near(candidate)
            if suggestion:
                message += " Choose an existing file path such as {0}.".format(
                    suggestion
                )
            return message
        return None

    def _suggest_file_inside(self, directory: Path) -> Optional[str]:
        for candidate in sorted(directory.rglob("*")):
            if candidate.is_file():
                return str(candidate.relative_to(self.repo_path))
        return None

    def _suggest_existing_file_near(self, candidate: Path) -> Optional[str]:
        parent = candidate.parent
        if parent.exists() and parent.is_dir():
            for sibling in sorted(parent.iterdir()):
                if sibling.is_file():
                    return str(sibling.relative_to(self.repo_path))

        for nearby in sorted(self.repo_path.rglob("*")):
            if nearby.is_file():
                return str(nearby.relative_to(self.repo_path))
        return None

    def _has_recent_file_context(self, relative_path: str) -> bool:
        return any(
            item.relative_path == relative_path for item in self.recent_file_contexts
        )
