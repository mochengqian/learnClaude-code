from __future__ import annotations

from typing import Any, Dict, Optional

from .models import ToolExecutionResult
from .session import TaskSession


class ContextBundleBuilder:
    def __init__(
        self,
        *,
        max_recent_timeline_events: int = 8,
        max_recent_file_contexts: int = 3,
        max_recent_test_failures: int = 2,
        max_diff_chars: int = 4000,
        max_file_content_chars: int = 4000,
        max_test_output_chars: int = 2000,
    ) -> None:
        self.max_recent_timeline_events = max_recent_timeline_events
        self.max_recent_file_contexts = max_recent_file_contexts
        self.max_recent_test_failures = max_recent_test_failures
        self.max_diff_chars = max_diff_chars
        self.max_file_content_chars = max_file_content_chars
        self.max_test_output_chars = max_test_output_chars

    def build(self, session: TaskSession) -> Dict[str, Any]:
        snapshot = session.snapshot()
        recent_timeline = [
            event.to_dict()
            for event in list(snapshot.timeline)[-self.max_recent_timeline_events :]
        ]
        read_focus = session.build_read_focus_snapshot()
        recent_file_contexts = [
            {
                "relative_path": item.relative_path,
                "content": _truncate_text(
                    item.content, limit=self.max_file_content_chars
                ),
                "source_tool": item.source_tool,
                "captured_at": item.captured_at,
            }
            for item in list(session.recent_file_contexts)[-self.max_recent_file_contexts :]
        ]
        recent_test_failures = [
            {
                "command": list(item.command),
                "exit_code": item.exit_code,
                "stdout": _truncate_text(
                    item.stdout, limit=self.max_test_output_chars
                ),
                "stderr": _truncate_text(
                    item.stderr, limit=self.max_test_output_chars
                ),
                "captured_at": item.captured_at,
            }
            for item in list(session.recent_test_failures)[-self.max_recent_test_failures :]
        ]
        return {
            "repo_path": snapshot.repo_path,
            "permission_mode": snapshot.permission_mode,
            "plan": snapshot.plan,
            "todos": [todo.to_dict() for todo in snapshot.todos],
            "pending_approvals": [
                approval.to_dict() for approval in snapshot.pending_approvals
            ],
            "latest_diff": _truncate_text(snapshot.latest_diff, limit=self.max_diff_chars),
            "latest_tool_result": _compact_tool_result(snapshot.latest_tool_result),
            "latest_successful_test": (
                snapshot.latest_successful_test.to_dict()
                if snapshot.latest_successful_test
                else None
            ),
            "read_focus": read_focus,
            "recent_timeline": recent_timeline,
            "recent_file_contexts": recent_file_contexts,
            "recent_test_failures": recent_test_failures,
        }


def _compact_tool_result(
    result: Optional[ToolExecutionResult], *, max_tool_output_chars: int = 4000
) -> Optional[Dict[str, Any]]:
    if result is None:
        return None

    payload = result.to_dict()
    payload["stdout"] = _truncate_text(
        payload.get("stdout", ""), limit=max_tool_output_chars
    )
    payload["stderr"] = _truncate_text(
        payload.get("stderr", ""), limit=max_tool_output_chars
    )
    payload["diff"] = _truncate_text(
        payload.get("diff", ""), limit=max_tool_output_chars
    )
    data = dict(payload.get("data") or {})
    if "content" in data:
        data["content"] = _truncate_text(str(data["content"]), limit=12000)
    payload["data"] = data
    return payload


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return "{0}\n...<truncated {1} chars>...".format(value[:limit], len(value) - limit)
