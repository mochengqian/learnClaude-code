from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

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
