from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .context_bundle import ContextBundleBuilder
from .model_client import ModelClientError
from .models import (
    AgentDecision,
    AgentLoopOutcome,
    AgentPlanDraft,
    AgentStepOutcome,
    PermissionMode,
    TodoItem,
    TodoStatus,
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
- Stay inside this runtime: read_file, file_patch, write_file, shell, run_test.
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
    "tool_type": "read_file" | "file_patch" | "write_file" | "shell" | "run_test",
    "relative_path": "path/inside/repo",
    "expected_old_snippet": "exact snippet already in the file for file_patch",
    "new_snippet": "replacement snippet for file_patch",
    "replace_all": false,
    "content": "full file contents for write_file",
    "command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
  }
}

Rules:
- Choose exactly one next action.
- Respect the current plan and todos.
- Prefer read_file before file_patch or write_file.
- Prefer file_patch for small, localized edits.
- write_file must contain the full file contents, not a patch.
- Use write_file mainly for new files or full rewrites.
- run_test is only for local tests.
- shell should stay conservative and read-oriented.
- Do not return finish unless a local run_test has already succeeded for the current repo state.
- If the task might be complete but the current repo state has not passed a local test yet, request run_test instead of finish.
- Do not add commentary outside the JSON object.
""".strip()


class AgentRunner:
    def __init__(
        self,
        model_client: Any,
        context_builder: Optional[ContextBundleBuilder] = None,
        max_output_retries: int = 1,
    ) -> None:
        self.model_client = model_client
        self.context_builder = context_builder or ContextBundleBuilder()
        self.max_output_retries = max_output_retries

    def draft_plan(self, session: TaskSession) -> AgentPlanDraft:
        if not session.task_input:
            raise ValueError("Cannot ask the model for a plan before a task is started.")
        if session.permission_mode != PermissionMode.PLAN:
            raise ValueError("Agent plan generation is only available in plan mode.")

        prompt_context = {
            "task_input": session.task_input,
            "snapshot": self._snapshot_context(session),
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
            "snapshot": self._snapshot_context(session),
        }
        response, payload = self._request_step_payload(session, prompt_context)
        action = str(payload.get("action") or "").strip()
        summary = str(payload.get("summary") or "").strip()

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

        tool_request = self._tool_request_from_payload(payload)
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

    def _snapshot_context(self, session: TaskSession) -> Dict[str, Any]:
        return self.context_builder.build(session)

    def _request_step_payload(
        self, session: TaskSession, prompt_context: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, Any]]:
        current_prompt_context = prompt_context

        for attempt in range(self.max_output_retries + 1):
            response = self.model_client.complete(
                system_prompt=_STEP_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    current_prompt_context, indent=2, ensure_ascii=False
                ),
            )
            try:
                payload = _parse_json_object(response.text)
                self._validate_step_payload(session, payload)
            except ModelClientError as exc:
                session.record_event(
                    "agent_step_output_invalid",
                    model=response.model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt >= self.max_output_retries:
                    raise
                session.record_event(
                    "agent_step_output_retry_requested",
                    model=response.model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                current_prompt_context = self._build_repair_prompt_context(
                    session=session,
                    validation_error=str(exc),
                    previous_output=response.text,
                )
                continue

            if attempt > 0:
                session.record_event(
                    "agent_step_output_repaired",
                    model=response.model,
                    attempts_used=attempt + 1,
                )
            return response, payload

        raise AssertionError("unreachable")

    def _build_repair_prompt_context(
        self,
        *,
        session: TaskSession,
        validation_error: str,
        previous_output: str,
    ) -> Dict[str, Any]:
        return {
            "task_input": session.task_input,
            "snapshot": self._snapshot_context(session),
            "repair_request": {
                "validation_error": validation_error,
                "previous_output": previous_output,
                "instruction": (
                    "Return corrected JSON only using the required runtime schema. "
                    "If finish is blocked, choose the next request_tool action instead."
                ),
            },
        }

    def _validate_step_payload(
        self, session: TaskSession, payload: Dict[str, Any]
    ) -> None:
        action = str(payload.get("action") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        if action not in {"request_tool", "finish"}:
            raise ModelClientError(
                "Model returned an unsupported action: {0}".format(action)
            )
        if not summary:
            raise ModelClientError("Model returned an empty action summary.")
        if action == "finish" and not session.has_successful_test_for_current_state():
            raise ModelClientError(
                "Model returned an invalid finish action: {0}".format(
                    session.finish_block_reason()
                )
            )
        if action == "request_tool":
            tool_request = self._tool_request_from_payload(payload)
            path_error = session.validate_tool_request_path(tool_request)
            if path_error:
                raise ModelClientError(path_error)
            edit_context_error = session.validate_tool_request_edit_context(tool_request)
            if edit_context_error:
                raise ModelClientError(edit_context_error)

    def _tool_request_from_payload(self, payload: Dict[str, Any]):
        tool_payload = payload.get("tool_request")
        if not isinstance(tool_payload, dict):
            raise ModelClientError(
                "Model returned request_tool without a tool_request object."
            )
        try:
            return tool_request_from_payload(tool_payload)
        except (TypeError, ValueError) as exc:
            raise ModelClientError(
                "Model returned an invalid tool_request: {0}".format(exc)
            ) from exc

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
