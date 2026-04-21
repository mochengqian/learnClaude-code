from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .model_client import ModelClientError
from .models import (
    AgentDecision,
    AgentLoopOutcome,
    AgentPlanDraft,
    AgentStepOutcome,
    PermissionMode,
    TaskSnapshot,
    TodoItem,
    TodoStatus,
    ToolExecutionResult,
    tool_name_for_request,
    tool_request_from_payload,
)
from .session import TaskSession

_PLAN_SYSTEM_PROMPT = """
You are a repo-task planning assistant for a restricted coding runtime.
Return JSON only.

The required JSON shape is:
{
  "plan_markdown": "1. ...\\n2. ...\\n3. ...",
  "todos": [
    {"content": "...", "active_form": "...", "status": "in_progress"},
    {"content": "...", "active_form": "...", "status": "pending"}
  ]
}

Rules:
- Keep the plan short and executable, usually 3 steps.
- Stay inside this runtime: read_file, write_file, shell, run_test.
- Do not mention tools that do not exist.
- If todos are present, make exactly one todo in_progress.
- Do not add commentary outside the JSON object.
""".strip()

_STEP_SYSTEM_PROMPT = """
You are the single-agent decision layer for a restricted repo-task runtime.
Return JSON only.

The required JSON shape is:
{
  "summary": "short reason for the next step",
  "action": "request_tool" | "finish",
  "tool_request": {
    "tool_type": "read_file" | "write_file" | "shell" | "run_test",
    "relative_path": "path/inside/repo",
    "content": "full file contents for write_file",
    "command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
  }
}

Rules:
- Choose exactly one next action.
- Respect the current plan and todos.
- Prefer read_file before write_file.
- write_file must contain the full file contents, not a patch.
- run_test is only for local tests.
- shell should stay conservative and read-oriented.
- If the task is complete, return action=finish.
- Do not add commentary outside the JSON object.
""".strip()


class AgentRunner:
    def __init__(self, model_client: Any) -> None:
        self.model_client = model_client

    def draft_plan(self, session: TaskSession) -> AgentPlanDraft:
        if not session.task_input:
            raise ValueError("Cannot ask the model for a plan before a task is started.")
        if session.permission_mode != PermissionMode.PLAN:
            raise ValueError("Agent plan generation is only available in plan mode.")

        prompt_context = {
            "task_input": session.task_input,
            "snapshot": self._snapshot_context(session.snapshot()),
        }
        response = self.model_client.complete(
            system_prompt=_PLAN_SYSTEM_PROMPT,
            user_prompt=json.dumps(prompt_context, indent=2, ensure_ascii=False),
        )
        payload = _parse_json_object(response.text)
        plan_markdown = str(payload.get("plan_markdown") or "").strip()
        if not plan_markdown:
            raise ModelClientError("Model returned an empty plan_markdown.")

        todos = _normalize_todos(payload.get("todos") or [])
        session.update_plan(plan_markdown)
        session.replace_todos(todos)
        session.record_event(
            "agent_plan_drafted",
            model=response.model,
            todo_count=len(todos),
            usage=response.usage,
        )
        return AgentPlanDraft(
            plan_markdown=plan_markdown,
            todos=todos,
            model=response.model,
            usage=response.usage,
            raw_output=response.text,
        )

    def run_next_step(self, session: TaskSession) -> AgentStepOutcome:
        self._ensure_ready_for_step(session)

        prompt_context = {
            "task_input": session.task_input,
            "snapshot": self._snapshot_context(session.snapshot()),
        }
        response = self.model_client.complete(
            system_prompt=_STEP_SYSTEM_PROMPT,
            user_prompt=json.dumps(prompt_context, indent=2, ensure_ascii=False),
        )
        payload = _parse_json_object(response.text)

        action = str(payload.get("action") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        if action not in {"request_tool", "finish"}:
            raise ModelClientError("Model returned an unsupported action: {0}".format(action))
        if not summary:
            raise ModelClientError("Model returned an empty action summary.")

        if action == "finish":
            decision = AgentDecision(
                summary=summary,
                action=action,
                model=response.model,
                usage=response.usage,
                raw_output=response.text,
            )
            session.record_event(
                "agent_step_finished",
                model=response.model,
                summary=summary,
                usage=response.usage,
            )
            outcome = AgentStepOutcome(decision=decision)
            _sync_todos_after_agent_step(session, outcome)
            return outcome

        tool_payload = payload.get("tool_request")
        if not isinstance(tool_payload, dict):
            raise ModelClientError("Model returned request_tool without a tool_request object.")
        try:
            tool_request = tool_request_from_payload(tool_payload)
        except (TypeError, ValueError) as exc:
            raise ModelClientError(
                "Model returned an invalid tool_request: {0}".format(exc)
            ) from exc
        decision = AgentDecision(
            summary=summary,
            action=action,
            model=response.model,
            tool_request=tool_request,
            usage=response.usage,
            raw_output=response.text,
        )
        session.record_event(
            "agent_step_decided",
            model=response.model,
            summary=summary,
            tool_name=tool_name_for_request(tool_request),
            usage=response.usage,
        )
        tool_result = session.request_tool(tool_request)
        session.record_event(
            "agent_step_applied",
            tool_name=tool_result.tool_name,
            result_status=tool_result.status,
        )
        outcome = AgentStepOutcome(decision=decision, tool_result=tool_result)
        _sync_todos_after_agent_step(session, outcome)
        return outcome

    def run_loop(self, session: TaskSession, max_steps: int) -> AgentLoopOutcome:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1.")
        if max_steps > 8:
            raise ValueError("max_steps cannot exceed 8.")

        self._ensure_ready_for_step(session)
        session.record_event("agent_loop_started", max_steps=max_steps)

        steps: List[AgentStepOutcome] = []
        stop_reason = "max_steps_reached"

        for _ in range(max_steps):
            outcome = self.run_next_step(session)
            steps.append(outcome)

            stop_reason = _stop_reason_for_step(outcome)
            if stop_reason is not None:
                break
        else:
            stop_reason = "max_steps_reached"

        loop_outcome = AgentLoopOutcome(
            steps=steps,
            stop_reason=stop_reason,
            steps_completed=len(steps),
            max_steps=max_steps,
        )
        session.record_event(
            "agent_loop_stopped",
            stop_reason=stop_reason,
            steps_completed=len(steps),
            max_steps=max_steps,
        )
        return loop_outcome

    def _snapshot_context(self, snapshot: TaskSnapshot) -> Dict[str, Any]:
        recent_timeline = [
            event.to_dict() for event in list(snapshot.timeline)[-8:]
        ]
        return {
            "repo_path": snapshot.repo_path,
            "permission_mode": snapshot.permission_mode,
            "plan": snapshot.plan,
            "todos": [todo.to_dict() for todo in snapshot.todos],
            "pending_approvals": [approval.to_dict() for approval in snapshot.pending_approvals],
            "latest_diff": _truncate_text(snapshot.latest_diff, limit=4000),
            "latest_tool_result": _compact_tool_result(snapshot.latest_tool_result),
            "recent_timeline": recent_timeline,
        }

    def _ensure_ready_for_step(self, session: TaskSession) -> None:
        if not session.task_input:
            raise ValueError("Cannot ask the model for a step before a task is started.")
        if session.permission_mode == PermissionMode.PLAN:
            raise ValueError("Approve the plan before asking the agent to take the next step.")
        if session.pending_approvals:
            raise ValueError("Resolve pending approvals before asking for another agent step.")


def _parse_json_object(raw_text: str) -> Dict[str, Any]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ModelClientError("Model did not return a JSON object.")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelClientError("Model returned invalid JSON.") from exc

    if not isinstance(parsed, dict):
        raise ModelClientError("Model output must be a JSON object.")
    return parsed


def _normalize_todos(raw_todos: Any) -> List[TodoItem]:
    if not isinstance(raw_todos, list):
        raise ModelClientError("Model todos must be a JSON array.")

    normalized: List[TodoItem] = []
    for index, raw_todo in enumerate(raw_todos):
        if not isinstance(raw_todo, dict):
            raise ModelClientError("Each todo must be a JSON object.")
        status = str(raw_todo.get("status") or "").strip() or (
            TodoStatus.IN_PROGRESS.value if index == 0 else TodoStatus.PENDING.value
        )
        try:
            normalized.append(
                TodoItem(
                    content=str(raw_todo.get("content") or ""),
                    active_form=str(raw_todo.get("active_form") or raw_todo.get("content") or ""),
                    status=TodoStatus(status),
                ).normalized()
            )
        except ValueError as exc:
            raise ModelClientError("Model returned an invalid todo: {0}".format(exc)) from exc

    in_progress_seen = False
    fixed: List[TodoItem] = []
    for index, todo in enumerate(normalized):
        status = todo.status
        if status == TodoStatus.IN_PROGRESS:
            if in_progress_seen:
                status = TodoStatus.PENDING
            in_progress_seen = True
        fixed.append(
            TodoItem(
                id=todo.id,
                content=todo.content,
                active_form=todo.active_form,
                status=status,
            )
        )

    if fixed and not in_progress_seen:
        first = fixed[0]
        fixed[0] = TodoItem(
            id=first.id,
            content=first.content,
            active_form=first.active_form,
            status=TodoStatus.IN_PROGRESS,
        )
    return fixed


def _compact_tool_result(result: Optional[ToolExecutionResult]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None

    payload = result.to_dict()
    payload["stdout"] = _truncate_text(payload.get("stdout", ""), limit=4000)
    payload["stderr"] = _truncate_text(payload.get("stderr", ""), limit=4000)
    payload["diff"] = _truncate_text(payload.get("diff", ""), limit=4000)
    data = dict(payload.get("data") or {})
    if "content" in data:
        data["content"] = _truncate_text(str(data["content"]), limit=12000)
    payload["data"] = data
    return payload


def _stop_reason_for_step(outcome: AgentStepOutcome) -> Optional[str]:
    if outcome.decision.action == "finish":
        return "finished"

    if outcome.tool_result is None:
        return None

    status = outcome.tool_result.status
    if status == "approval_required":
        return "approval_required"
    if status in {"denied", "rejected"}:
        return "tool_blocked"
    if status == "failed":
        return "tool_failed"
    return None


def _sync_todos_after_agent_step(session: TaskSession, outcome: AgentStepOutcome) -> None:
    sync_mode = _todo_sync_mode(outcome)
    if sync_mode is None or not session.todos:
        return

    current_index = next(
        (index for index, todo in enumerate(session.todos) if todo.status == TodoStatus.IN_PROGRESS),
        None,
    )
    if current_index is None:
        return

    synced: List[TodoItem] = []
    completed_todo_id: Optional[str] = None
    next_todo_id: Optional[str] = None

    for index, todo in enumerate(session.todos):
        status = todo.status
        if index == current_index:
            status = TodoStatus.COMPLETED
            completed_todo_id = todo.id
        synced.append(
            TodoItem(
                id=todo.id,
                content=todo.content,
                active_form=todo.active_form,
                status=status,
            )
        )

    if sync_mode == "advance":
        for index, todo in enumerate(synced):
            if index == current_index or todo.status != TodoStatus.PENDING:
                continue
            synced[index] = TodoItem(
                id=todo.id,
                content=todo.content,
                active_form=todo.active_form,
                status=TodoStatus.IN_PROGRESS,
            )
            next_todo_id = todo.id
            break

    session.replace_todos(synced)
    session.record_event(
        "agent_todos_synced",
        mode=sync_mode,
        completed_todo_id=completed_todo_id,
        next_todo_id=next_todo_id,
        remaining_count=len(session.todos),
    )


def _todo_sync_mode(outcome: AgentStepOutcome) -> Optional[str]:
    if outcome.decision.action == "finish":
        return "complete_current_only"
    if outcome.tool_result is None:
        return None
    if outcome.tool_result.status == "executed":
        return "advance"
    return None


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return "{0}\n...<truncated {1} chars>...".format(value[:limit], len(value) - limit)
