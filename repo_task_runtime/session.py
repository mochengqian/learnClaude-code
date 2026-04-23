from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional
from uuid import uuid4

from .approval import ApprovalPolicy
from .diffing import build_unified_diff, repo_git_diff
from .models import (
    ApprovalKind,
    ApprovalRequest,
    approval_kind_for_request,
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


_CODE_FILE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".py",
    ".rb",
    ".rs",
    ".ts",
    ".tsx",
}
_SOURCE_ROOT_NAMES = {"app", "demo_app", "lib", "pkg", "src"}
_TEST_ROOT_NAMES = {"test", "tests"}


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
            approval_kind = approval_kind_for_request(request)
            if approval_kind is None:
                raise ValueError(
                    "Approval kind is missing for tool request: {0}".format(tool_name)
                )
            approval = ApprovalRequest(
                approval_id=approval_id,
                tool_name=tool_name,
                approval_kind=approval_kind,
                reason=decision.reason,
                request=request,
            )
            self.pending_approvals[approval_id] = approval
            self._record(
                "approval_requested",
                approval_id=approval_id,
                tool_name=tool_name,
                approval_kind=approval_kind.value,
                reason=decision.reason,
                approval=approval.to_dict(),
            )
            result = ToolExecutionResult(
                status="approval_required",
                tool_name=tool_name,
                message=decision.reason,
                approval_id=approval_id,
                approval_kind=approval_kind,
            )
            self.latest_tool_result = result
            return result

        return self._run_execute_request(
            request,
            approved_by=None,
            approval_kind=None,
        )

    def resolve_approval(self, approval_id: str, approve: bool) -> ToolExecutionResult:
        approval = self.pending_approvals.pop(approval_id, None)
        if approval is None:
            raise ValueError("Unknown approval id: {0}".format(approval_id))

        if not approve:
            self._record(
                "approval_rejected",
                approval_id=approval_id,
                tool_name=approval.tool_name,
                approval_kind=approval.approval_kind.value,
            )
            result = ToolExecutionResult(
                status="rejected",
                tool_name=approval.tool_name,
                message="User rejected the approval request.",
                approval_id=approval_id,
                approval_kind=approval.approval_kind,
            )
            self.latest_tool_result = result
            return result

        self._record(
            "approval_granted",
            approval_id=approval_id,
            tool_name=approval.tool_name,
            approval_kind=approval.approval_kind.value,
        )
        return self._run_execute_request(
            approval.request,
            approved_by=approval_id,
            approval_kind=approval.approval_kind,
        )

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
            target_binding_error = self._validate_edit_target_binding(
                request.relative_path,
                tool_name="file_patch",
            )
            if target_binding_error:
                return target_binding_error
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
            target_binding_error = self._validate_edit_target_binding(
                request.relative_path,
                tool_name="write_file",
            )
            if target_binding_error:
                return target_binding_error
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

    def validate_tool_request_read_focus(
        self, request: ToolInvocationRequest
    ) -> Optional[str]:
        if not isinstance(request, FileReadRequest):
            return None
        if self._is_readme_path(request.relative_path):
            if self._count_executed_reads(request.relative_path) < 1:
                return None
            suggestion = self._best_repo_file_suggestion(
                requested_path=self._resolve_repo_path(request.relative_path),
                search_root=self.repo_path,
                include_readme=False,
            )
            if suggestion is None:
                return None
            return (
                "Model is rereading {0} after it was already read. "
                "Read a more task-relevant file such as {1} instead."
            ).format(request.relative_path, suggestion)

        if not self._should_block_same_file_reread(request.relative_path):
            return None

        return (
            "Model is rereading {0} even though recent context for that file is "
            "already available. Use recent_file_contexts and continue with "
            "file_patch/write_file or run_test instead of reading the same file again."
        ).format(request.relative_path)

    def validate_tool_request_approval_focus(
        self, request: ToolInvocationRequest
    ) -> Optional[str]:
        if not isinstance(request, ShellCommandRequest):
            return None

        guidance = self.approval_policy.guidance_for_shell_request(request)
        if guidance is None:
            return None

        command_preview = " ".join(request.command)
        if guidance.preferred_tool == "run_test":
            return (
                "Model selected shell for a local test command: {0}. "
                "Use run_test instead of shell for local tests."
            ).format(command_preview)

        relative_path = (guidance.relative_path or "").strip()
        if not relative_path:
            return None

        path_error = self._validate_repo_file_path(
            relative_path,
            tool_name="read_file",
            must_exist=True,
        )
        if path_error is not None:
            return None

        return (
            "Model selected shell to read a repo file directly: {0}. "
            "Use read_file for that file instead of shell."
        ).format(relative_path)

    def build_read_focus_snapshot(self) -> Dict[str, object]:
        recent_context_paths = [
            item.relative_path for item in self.recent_file_contexts
        ]
        primary_target_path = self.current_primary_target_path()

        avoid_reread_paths: List[str] = []
        preferred_next_action = "gather_context"
        instruction = (
            "Read task-relevant files first, then move to patching or tests."
        )

        if self.pending_approvals:
            preferred_next_action = "await_approval"
            instruction = (
                "An approval is pending. Resolve the approval before requesting "
                "additional file reads."
            )
        elif self.recent_test_failures:
            preferred_next_action = "inspect_test_failure"
            instruction = (
                "A local test is failing. Use recent_test_failures and existing "
                "recent_file_contexts before rereading files."
            )
        elif self.has_successful_test_for_current_state():
            avoid_reread_paths = list(recent_context_paths)
            preferred_next_action = "finish"
            instruction = (
                "The current repo state already passed local tests. Finish instead "
                "of rereading files."
            )
        elif recent_context_paths:
            avoid_reread_paths = list(recent_context_paths)
            preferred_next_action = "patch_or_test"
            instruction = (
                "Recent file context is already available. Use "
                "recent_file_contexts instead of rereading these files, then prefer "
                "file_patch/write_file or run_test. Use run_test instead of shell for "
                "local tests, and use read_file instead of shell when inspecting a "
                "specific repo file."
            )
            if primary_target_path:
                instruction += " Keep edits on {0} unless you first read a different target file.".format(
                    primary_target_path
                )

        return {
            "recent_context_paths": recent_context_paths,
            "avoid_reread_paths": avoid_reread_paths,
            "primary_target_path": primary_target_path,
            "preferred_next_action": preferred_next_action,
            "instruction": instruction,
        }

    def current_primary_target_path(self) -> Optional[str]:
        for item in reversed(self.recent_file_contexts):
            relative_path = item.relative_path
            if self._is_readme_path(relative_path):
                continue
            if self._is_test_like_relative_path(relative_path):
                continue
            return Path(relative_path).as_posix()

        for item in reversed(self.recent_file_contexts):
            relative_path = item.relative_path
            if self._is_readme_path(relative_path):
                continue
            return Path(relative_path).as_posix()

        if not self.recent_file_contexts:
            return None
        return Path(self.recent_file_contexts[-1].relative_path).as_posix()

    def _execute_request(
        self,
        request: ToolInvocationRequest,
        approved_by: Optional[str],
        approval_kind: Optional[ApprovalKind],
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
                approval_kind=approval_kind,
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
                approval_kind=approval_kind,
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
                approval_kind=approval_kind,
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
                approval_kind=approval_kind,
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
            approval_kind=(
                approval_kind.value if approval_kind is not None else None
            ),
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
        self,
        request: ToolInvocationRequest,
        approved_by: Optional[str],
        approval_kind: Optional[ApprovalKind],
    ) -> ToolExecutionResult:
        tool_name = tool_name_for_request(request)
        try:
            return self._execute_request(
                request,
                approved_by=approved_by,
                approval_kind=approval_kind,
            )
        except subprocess.TimeoutExpired as exc:
            message = "Command timed out after {0} seconds.".format(exc.timeout)
        except (FileNotFoundError, OSError, ValueError) as exc:
            message = str(exc)

        self._record(
            "tool_failed",
            tool_name=tool_name,
            approved_by=approved_by,
            approval_kind=(
                approval_kind.value if approval_kind is not None else None
            ),
            request=request_summary(request),
            message=message,
        )
        result = ToolExecutionResult(
            status="failed",
            tool_name=tool_name,
            message=message,
            approval_kind=approval_kind,
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
            suggestions = self._suggest_existing_files_near(candidate, limit=3)
            if suggestions:
                message += " Choose one of these existing file paths instead: [{0}].".format(
                    ", ".join(suggestions)
                )
            return message
        return None

    def _suggest_file_inside(self, directory: Path) -> Optional[str]:
        return self._best_repo_file_suggestion(
            requested_path=directory,
            search_root=directory,
            include_readme=False,
        )

    def _suggest_existing_file_near(self, candidate: Path) -> Optional[str]:
        suggestions = self._suggest_existing_files_near(candidate, limit=1)
        if not suggestions:
            return None
        return suggestions[0]

    def suggest_existing_files_for_missing_relative_path(
        self, *, tool_name: str, limit: int = 3
    ) -> List[str]:
        if limit < 1:
            return []

        suggestions: List[str] = []
        seen: set[str] = set()
        for item in reversed(self.recent_file_contexts):
            relative_path = item.relative_path
            if self._is_readme_path(relative_path):
                continue
            candidate = self._resolve_repo_path(relative_path)
            if not candidate.exists() or not candidate.is_file():
                continue
            normalized = Path(relative_path).as_posix()
            if normalized in seen:
                continue
            suggestions.append(normalized)
            seen.add(normalized)
            if len(suggestions) >= limit:
                return suggestions

        requested_path = self.repo_path / "{0}.py".format(tool_name)
        repo_suggestions = self._best_repo_file_suggestions(
            requested_path=requested_path,
            search_root=self.repo_path,
            include_readme=False,
            limit=limit,
        )
        for suggestion in repo_suggestions:
            normalized = Path(suggestion).as_posix()
            if normalized in seen:
                continue
            suggestions.append(normalized)
            seen.add(normalized)
            if len(suggestions) >= limit:
                break
        return suggestions

    def _suggest_existing_files_near(
        self, candidate: Path, *, limit: int
    ) -> List[str]:
        search_root = self._nearest_existing_directory(candidate.parent)
        return self._best_repo_file_suggestions(
            requested_path=candidate,
            search_root=search_root,
            include_readme=False,
            limit=limit,
        )

    def _nearest_existing_directory(self, candidate: Path) -> Path:
        current = candidate
        while True:
            try:
                current.relative_to(self.repo_path)
            except ValueError:
                return self.repo_path
            if current.exists() and current.is_dir():
                return current
            if current == self.repo_path:
                return self.repo_path
            if current == current.parent:
                return self.repo_path
            current = current.parent

    def _best_repo_file_suggestions(
        self,
        *,
        requested_path: Path,
        search_root: Path,
        include_readme: bool,
        limit: int,
    ) -> List[str]:
        if limit < 1:
            return []

        candidates = self._collect_suggestable_files(
            search_root,
            include_readme=include_readme,
        )
        if not candidates and search_root != self.repo_path:
            candidates = self._collect_suggestable_files(
                self.repo_path,
                include_readme=include_readme,
            )
        if not candidates and not include_readme:
            return self._best_repo_file_suggestions(
                requested_path=requested_path,
                search_root=search_root,
                include_readme=True,
                limit=limit,
            )
        if not candidates:
            return []

        ordered = sorted(
            candidates,
            key=lambda path: self._suggestion_sort_key(
                path,
                requested_path=requested_path,
            ),
        )
        return [
            str(path.relative_to(self.repo_path)) for path in ordered[:limit]
        ]

    def _best_repo_file_suggestion(
        self,
        *,
        requested_path: Path,
        search_root: Path,
        include_readme: bool,
    ) -> Optional[str]:
        suggestions = self._best_repo_file_suggestions(
            requested_path=requested_path,
            search_root=search_root,
            include_readme=include_readme,
            limit=1,
        )
        if not suggestions:
            return None
        return suggestions[0]

    def _collect_suggestable_files(
        self, search_root: Path, *, include_readme: bool
    ) -> List[Path]:
        files: List[Path] = []
        for candidate in sorted(search_root.rglob("*")):
            if not candidate.is_file():
                continue
            try:
                relative_path = candidate.relative_to(self.repo_path)
            except ValueError:
                continue
            if self._is_hidden_relative_path(relative_path):
                continue
            if not include_readme and self._is_readme_path(str(relative_path)):
                continue
            files.append(candidate)
        return files

    def _suggestion_sort_key(
        self, candidate: Path, *, requested_path: Path
    ) -> tuple[object, ...]:
        relative_path = candidate.relative_to(self.repo_path)
        relative_text = relative_path.as_posix()
        shared_tokens = len(
            self._path_tokens(self._path_text_for_matching(requested_path))
            & self._path_tokens(relative_text)
        )
        return (
            0 if shared_tokens else 1,
            -shared_tokens,
            self._file_kind_rank(relative_path),
            0 if self._has_recent_file_context(relative_text) else 1,
            1 if relative_path.name == "__init__.py" else 0,
            1 if self._is_readme_path(relative_text) else 0,
            len(relative_path.parts),
            relative_text,
        )

    def _file_kind_rank(self, relative_path: Path) -> int:
        if not self._is_code_like_file(relative_path):
            return 3

        first_part = ""
        if relative_path.parts:
            first_part = relative_path.parts[0].lower()
        if first_part in _SOURCE_ROOT_NAMES:
            return 0
        if first_part in _TEST_ROOT_NAMES or relative_path.name.startswith("test_"):
            return 1
        return 2

    def _is_code_like_file(self, relative_path: Path) -> bool:
        return relative_path.suffix.lower() in _CODE_FILE_SUFFIXES

    def _is_test_like_relative_path(self, relative_path: str) -> bool:
        return self._file_kind_rank(Path(relative_path)) == 1

    def _is_hidden_relative_path(self, relative_path: Path) -> bool:
        return any(part.startswith(".") for part in relative_path.parts)

    def _is_readme_path(self, relative_path: str) -> bool:
        return Path(relative_path).name.lower() == "readme.md"

    def _count_executed_reads(self, relative_path: str) -> int:
        normalized_path = Path(relative_path).as_posix().lower()
        count = 0
        for event in self.timeline:
            if event.event_type != "tool_executed":
                continue
            if event.payload.get("tool_name") != "read_file":
                continue
            request = event.payload.get("request") or {}
            request_path = str(request.get("relative_path") or "").strip()
            if not request_path:
                continue
            if Path(request_path).as_posix().lower() == normalized_path:
                count += 1
        return count

    def _path_text_for_matching(self, path: Path) -> str:
        try:
            return path.relative_to(self.repo_path).as_posix()
        except ValueError:
            return path.as_posix()

    def _path_tokens(self, value: str) -> set[str]:
        return {
            token
            for token in re.split(r"[^a-z0-9]+", value.lower())
            if len(token) >= 2
        }

    def _has_recent_file_context(self, relative_path: str) -> bool:
        return any(
            item.relative_path == relative_path for item in self.recent_file_contexts
        )

    def _validate_edit_target_binding(
        self, relative_path: str, *, tool_name: str
    ) -> Optional[str]:
        primary_target_path = self.current_primary_target_path()
        if primary_target_path is None:
            return None

        normalized_relative_path = Path(relative_path).as_posix()
        normalized_primary_target = Path(primary_target_path).as_posix()
        if normalized_relative_path == normalized_primary_target:
            return None

        return (
            "Model selected an off-target edit path for {0}: {1}. "
            "The current primary target from recent file context is {2}. "
            "Keep the edit on {2}, or read {1} first if you truly need to switch targets."
        ).format(tool_name, normalized_relative_path, normalized_primary_target)

    def _should_block_same_file_reread(self, relative_path: str) -> bool:
        if not self._has_recent_file_context(relative_path):
            return False
        if self.recent_test_failures:
            return False
        return True
