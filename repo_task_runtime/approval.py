from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from .models import (
    FileReadRequest,
    FilePatchRequest,
    PermissionMode,
    ShellCommandRequest,
    TestCommandRequest,
    ToolInvocationRequest,
    WriteFileRequest,
)


SAFE_READ_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("pwd",),
    ("ls",),
    ("find",),
    ("rg",),
    ("cat",),
    ("sed",),
    ("git", "status"),
    ("git", "diff"),
)

SAFE_TEST_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("python", "-m", "unittest"),
    ("python3", "-m", "unittest"),
)

DANGEROUS_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("rm",),
    ("sudo",),
    ("curl",),
    ("wget",),
    ("ssh",),
    ("scp",),
    ("git", "reset"),
    ("git", "checkout", "--"),
    ("git", "clean"),
    ("dd",),
)


@dataclass(frozen=True)
class ApprovalDecision:
    behavior: str
    reason: str


@dataclass(frozen=True)
class ShellToolGuidance:
    preferred_tool: str
    reason: str
    relative_path: Optional[str] = None


def _matches_prefix(command: Sequence[str], prefixes: Sequence[Sequence[str]]) -> bool:
    for prefix in prefixes:
        if len(command) < len(prefix):
            continue
        if tuple(command[: len(prefix)]) == tuple(prefix):
            return True
    return False


class ApprovalPolicy:
    def __init__(
        self,
        safe_read_prefixes: Sequence[Sequence[str]] = SAFE_READ_PREFIXES,
        safe_test_prefixes: Sequence[Sequence[str]] = SAFE_TEST_PREFIXES,
        dangerous_prefixes: Sequence[Sequence[str]] = DANGEROUS_PREFIXES,
    ) -> None:
        self.safe_read_prefixes = tuple(tuple(item) for item in safe_read_prefixes)
        self.safe_test_prefixes = tuple(tuple(item) for item in safe_test_prefixes)
        self.dangerous_prefixes = tuple(tuple(item) for item in dangerous_prefixes)

    def guidance_for_shell_request(
        self, request: ShellCommandRequest
    ) -> Optional[ShellToolGuidance]:
        if _looks_like_local_test_command(request.command, self.safe_test_prefixes):
            return ShellToolGuidance(
                preferred_tool="run_test",
                reason="Use run_test instead of shell for local test commands.",
            )

        relative_path = _extract_direct_file_read_path(request.command)
        if relative_path:
            return ShellToolGuidance(
                preferred_tool="read_file",
                reason="Use read_file instead of shell when inspecting a repo file directly.",
                relative_path=relative_path,
            )
        return None

    def evaluate(
        self, mode: PermissionMode, request: ToolInvocationRequest
    ) -> ApprovalDecision:
        if isinstance(request, FileReadRequest):
            return ApprovalDecision("allow", "read-only file access is always allowed")

        if mode == PermissionMode.PLAN:
            return ApprovalDecision(
                "deny", "plan mode is read-only; exit plan mode before mutating the repo"
            )

        if isinstance(request, (WriteFileRequest, FilePatchRequest)):
            if mode == PermissionMode.ACCEPT_EDITS:
                return ApprovalDecision(
                    "allow", "accept_edits mode auto-allows file edits"
                )
            return ApprovalDecision("ask", "editing files requires user approval")

        if isinstance(request, TestCommandRequest):
            if _matches_prefix(request.command, self.safe_test_prefixes):
                return ApprovalDecision(
                    "allow", "approved test command prefix is safe to run"
                )
            return ApprovalDecision("ask", "unknown test command requires approval")

        if isinstance(request, ShellCommandRequest):
            if _matches_prefix(request.command, self.dangerous_prefixes):
                return ApprovalDecision("deny", "dangerous shell prefix is blocked")
            if _matches_prefix(request.command, self.safe_read_prefixes):
                return ApprovalDecision(
                    "allow", "approved read-only shell prefix is safe to run"
                )
            return ApprovalDecision("ask", "shell command requires explicit approval")

        return ApprovalDecision("ask", "tool requires explicit approval")


def _looks_like_local_test_command(
    command: Sequence[str], safe_test_prefixes: Sequence[Sequence[str]]
) -> bool:
    if _matches_prefix(command, safe_test_prefixes):
        return True

    lowered = [part.lower() for part in command]
    if "pytest" in lowered:
        return True

    for index, part in enumerate(lowered[:-1]):
        if part != "-m":
            continue
        if lowered[index + 1] in {"pytest", "unittest"}:
            return True
    return False


def _extract_direct_file_read_path(command: Sequence[str]) -> Optional[str]:
    if not command:
        return None

    command_name = command[0]
    if command_name == "cat" and len(command) == 2:
        return command[1]

    if command_name == "sed" and len(command) >= 2:
        candidate = command[-1].strip()
        if candidate and not candidate.startswith("-"):
            return candidate

    return None
