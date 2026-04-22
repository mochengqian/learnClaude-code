from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import shlex
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid4().hex


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class TodoItem:
    content: str
    status: TodoStatus = TodoStatus.PENDING
    active_form: Optional[str] = None
    id: str = field(default_factory=_new_id)

    def normalized(self) -> "TodoItem":
        content = self.content.strip()
        if not content:
            raise ValueError("Todo content cannot be empty.")

        active_form = (self.active_form or content).strip()
        if not active_form:
            raise ValueError("Todo active_form cannot be empty.")

        return TodoItem(
            id=self.id,
            content=content,
            active_form=active_form,
            status=TodoStatus(self.status),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "id": self.id,
            "content": self.content,
            "active_form": self.active_form or self.content,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class TimelineEvent:
    event_type: str
    payload: Dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)
    event_id: str = field(default_factory=_new_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class FileReadRequest:
    relative_path: str

    def __post_init__(self) -> None:
        if not self.relative_path.strip():
            raise ValueError("relative_path cannot be empty.")


@dataclass(frozen=True)
class WriteFileRequest:
    relative_path: str
    content: str

    def __post_init__(self) -> None:
        if not self.relative_path.strip():
            raise ValueError("relative_path cannot be empty.")


@dataclass(frozen=True)
class FilePatchRequest:
    relative_path: str
    expected_old_snippet: str
    new_snippet: str
    replace_all: bool = False

    def __post_init__(self) -> None:
        if not self.relative_path.strip():
            raise ValueError("relative_path cannot be empty.")
        if not self.expected_old_snippet:
            raise ValueError("expected_old_snippet cannot be empty.")


@dataclass(frozen=True)
class ShellCommandRequest:
    command: Tuple[str, ...]
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        normalized = normalize_command(self.command)
        object.__setattr__(self, "command", normalized)


@dataclass(frozen=True)
class TestCommandRequest:
    command: Tuple[str, ...]
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        normalized = normalize_command(self.command)
        object.__setattr__(self, "command", normalized)


ToolInvocationRequest = Union[
    FileReadRequest,
    FilePatchRequest,
    WriteFileRequest,
    ShellCommandRequest,
    TestCommandRequest,
]


def normalize_command(command: Sequence[str]) -> Tuple[str, ...]:
    items = tuple(part for part in command if part)
    if not items:
        raise ValueError("Command cannot be empty.")
    return items


def tool_name_for_request(request: ToolInvocationRequest) -> str:
    if isinstance(request, FileReadRequest):
        return "read_file"
    if isinstance(request, FilePatchRequest):
        return "file_patch"
    if isinstance(request, WriteFileRequest):
        return "write_file"
    if isinstance(request, TestCommandRequest):
        return "run_test"
    return "shell"


def request_summary(request: ToolInvocationRequest) -> Dict[str, Any]:
    if isinstance(request, FileReadRequest):
        return {"relative_path": request.relative_path}
    if isinstance(request, FilePatchRequest):
        return {
            "relative_path": request.relative_path,
            "expected_old_snippet_preview": _preview_text(
                request.expected_old_snippet
            ),
            "new_snippet_preview": _preview_text(request.new_snippet),
            "expected_old_snippet_chars": len(request.expected_old_snippet),
            "new_snippet_chars": len(request.new_snippet),
            "replace_all": request.replace_all,
        }
    if isinstance(request, WriteFileRequest):
        return {
            "relative_path": request.relative_path,
            "content_chars": len(request.content),
        }
    return {
        "command": list(request.command),
        "timeout_seconds": request.timeout_seconds,
    }


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    tool_name: str
    reason: str
    request: ToolInvocationRequest
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "tool_name": self.tool_name,
            "reason": self.reason,
            "created_at": self.created_at,
            "request": request_summary(self.request),
        }


@dataclass
class ToolExecutionResult:
    status: str
    tool_name: str
    message: str
    approval_id: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    diff: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "tool_name": self.tool_name,
            "message": self.message,
            "approval_id": self.approval_id,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "diff": self.diff,
            "data": self.data,
        }


@dataclass(frozen=True)
class RecentFileContext:
    relative_path: str
    content: str
    source_tool: str
    captured_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "content": self.content,
            "source_tool": self.source_tool,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class RecentTestFailure:
    command: Tuple[str, ...]
    exit_code: Optional[int]
    stdout: str = ""
    stderr: str = ""
    captured_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class SuccessfulTestRun:
    command: Tuple[str, ...]
    exit_code: int
    repo_state_revision: int
    captured_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "repo_state_revision": self.repo_state_revision,
            "captured_at": self.captured_at,
        }


@dataclass
class AgentPlanDraft:
    plan_markdown: str
    todos: Sequence[TodoItem]
    model: str
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_markdown": self.plan_markdown,
            "todos": [todo.to_dict() for todo in self.todos],
            "model": self.model,
            "usage": self.usage,
            "raw_output": self.raw_output,
        }


@dataclass
class AgentDecision:
    summary: str
    action: str
    model: str
    tool_request: Optional[ToolInvocationRequest] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        tool_request = None
        if self.tool_request is not None:
            tool_request = request_summary(self.tool_request)
            tool_request["tool_type"] = tool_name_for_request(self.tool_request)
        return {
            "summary": self.summary,
            "action": self.action,
            "tool_request": tool_request,
            "model": self.model,
            "usage": self.usage,
            "raw_output": self.raw_output,
        }


@dataclass
class AgentStepOutcome:
    decision: AgentDecision
    tool_result: Optional[ToolExecutionResult] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "tool_result": self.tool_result.to_dict() if self.tool_result else None,
        }


@dataclass
class AgentLoopOutcome:
    steps: Sequence[AgentStepOutcome]
    stop_reason: str
    steps_completed: int
    max_steps: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "stop_reason": self.stop_reason,
            "steps_completed": self.steps_completed,
            "max_steps": self.max_steps,
        }


def tool_request_from_payload(payload: Mapping[str, Any]) -> ToolInvocationRequest:
    tool_type = str(payload.get("tool_type") or "").strip()
    timeout_seconds = payload.get("timeout_seconds")

    if timeout_seconds is not None:
        timeout_seconds = int(timeout_seconds)

    if tool_type == "read_file":
        relative_path = str(payload.get("relative_path") or "").strip()
        if not relative_path:
            raise ValueError("relative_path is required for read_file.")
        return FileReadRequest(relative_path=relative_path)

    if tool_type == "write_file":
        relative_path = str(payload.get("relative_path") or "").strip()
        if not relative_path:
            raise ValueError("relative_path is required for write_file.")
        if "content" not in payload or payload.get("content") is None:
            raise ValueError("content is required for write_file.")
        return WriteFileRequest(
            relative_path=relative_path,
            content=str(payload.get("content")),
        )

    if tool_type == "file_patch":
        relative_path = str(payload.get("relative_path") or "").strip()
        if not relative_path:
            raise ValueError("relative_path is required for file_patch.")
        if "expected_old_snippet" not in payload or payload.get("expected_old_snippet") is None:
            raise ValueError("expected_old_snippet is required for file_patch.")
        if "new_snippet" not in payload or payload.get("new_snippet") is None:
            raise ValueError("new_snippet is required for file_patch.")
        replace_all = payload.get("replace_all", False)
        if isinstance(replace_all, str):
            replace_all = replace_all.strip().lower() in {"1", "true", "yes", "on"}
        else:
            replace_all = bool(replace_all)
        return FilePatchRequest(
            relative_path=relative_path,
            expected_old_snippet=str(payload.get("expected_old_snippet")),
            new_snippet=str(payload.get("new_snippet")),
            replace_all=replace_all,
        )

    if tool_type not in {"shell", "run_test"}:
        raise ValueError("Unsupported tool_type: {0}".format(tool_type))

    command = payload.get("command", ())
    if isinstance(command, str):
        command_parts = tuple(shlex.split(command))
    else:
        command_parts = tuple(str(part) for part in command if str(part).strip())

    if tool_type == "shell":
        return ShellCommandRequest(
            command=command_parts,
            timeout_seconds=timeout_seconds or 30,
        )

    return TestCommandRequest(
        command=command_parts,
        timeout_seconds=timeout_seconds or 120,
    )


def _preview_text(value: str, limit: int = 160) -> str:
    if len(value) <= limit:
        return value
    return "{0}...<truncated {1} chars>".format(value[:limit], len(value) - limit)


@dataclass
class TaskSnapshot:
    session_id: str
    repo_path: str
    task_input: Optional[str]
    permission_mode: str
    plan: Optional[str]
    todos: Sequence[TodoItem]
    latest_diff: str
    timeline: Sequence[TimelineEvent]
    pending_approvals: Sequence[ApprovalRequest]
    latest_tool_result: Optional[ToolExecutionResult] = None
    latest_successful_test: Optional[SuccessfulTestRun] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "repo_path": self.repo_path,
            "task_input": self.task_input,
            "permission_mode": self.permission_mode,
            "plan": self.plan,
            "todos": [todo.to_dict() for todo in self.todos],
            "latest_diff": self.latest_diff,
            "timeline": [event.to_dict() for event in self.timeline],
            "pending_approvals": [
                approval.to_dict() for approval in self.pending_approvals
            ],
            "latest_tool_result": (
                self.latest_tool_result.to_dict() if self.latest_tool_result else None
            ),
            "latest_successful_test": (
                self.latest_successful_test.to_dict()
                if self.latest_successful_test
                else None
            ),
        }
