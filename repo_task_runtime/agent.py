from __future__ import annotations

import difflib
import json
from pathlib import Path
import re
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
- Read README.md at most once; after the first pass, inspect code or tests instead of rereading it.
- Use snapshot.read_focus.avoid_reread_paths as cached context. If a path appears there,
  do not request read_file for that same path again unless a new failing test means you
  need fresh context.
- When snapshot.read_focus.preferred_next_action is patch_or_test, prefer file_patch,
  write_file, or run_test over rereading the same file.
- When snapshot.read_focus.primary_target_path is set and you choose file_patch or
  write_file, keep the edit on that same path unless you first read a different file
  because it is truly the intended target.
- Do not patch README.md or another previously seen file just because it was read
  earlier; switch edit targets only after reading the new target file.
- Prefer file_patch for small, localized edits.
- write_file must contain the full file contents, not a patch.
- Use write_file mainly for new files or full rewrites.
- run_test is only for local tests.
- Use run_test instead of shell for local test commands such as python -m unittest or pytest.
- Use read_file instead of shell when inspecting a specific repo file.
- Do not use shell to cat/sed a repo file or to run the local test suite.
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
        response, plan_markdown, todos = self._request_plan_payload(
            session, prompt_context
        )
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

    def _request_plan_payload(
        self, session: TaskSession, prompt_context: Dict[str, Any]
    ) -> Tuple[Any, str, List[TodoItem]]:
        current_prompt_context = prompt_context

        for attempt in range(self.max_output_retries + 1):
            response = self.model_client.complete(
                system_prompt=_PLAN_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    current_prompt_context, indent=2, ensure_ascii=False
                ),
            )
            try:
                payload = _parse_json_object(response.text)
                plan_markdown = str(payload.get("plan_markdown") or "").strip()
                if not plan_markdown:
                    raise ModelClientError("Model returned an empty plan_markdown.")
                todos = _normalize_todos(payload.get("todos") or [])
            except ModelClientError as exc:
                session.record_event(
                    "agent_plan_output_invalid",
                    model=response.model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt >= self.max_output_retries:
                    raise ModelClientError(
                        "Plan output invalid: {0}".format(exc)
                    ) from exc
                session.record_event(
                    "agent_plan_output_retry_requested",
                    model=response.model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                current_prompt_context = self._build_plan_repair_prompt_context(
                    session=session,
                    validation_error=str(exc),
                    previous_output=response.text,
                )
                continue

            if attempt > 0:
                session.record_event(
                    "agent_plan_output_repaired",
                    model=response.model,
                    attempts_used=attempt + 1,
                )
            return response, plan_markdown, todos

        raise AssertionError("unreachable")

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
        remaining_approval_path_second_chances = 1
        remaining_directory_path_second_chances = 1
        remaining_edit_target_second_chances = 1
        remaining_missing_relative_path_second_chances = 1
        remaining_missing_repo_file_second_chances = 1
        remaining_patch_contract_second_chances = 1

        for attempt in range(self.max_output_retries + 2):
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
                standard_retry_available = attempt < self.max_output_retries
                approval_path_second_chance = (
                    not standard_retry_available
                    and remaining_approval_path_second_chances > 0
                    and self._is_approval_path_error(str(exc))
                )
                directory_path_second_chance = (
                    not standard_retry_available
                    and remaining_directory_path_second_chances > 0
                    and self._is_directory_path_error(str(exc))
                )
                edit_target_second_chance = (
                    not standard_retry_available
                    and remaining_edit_target_second_chances > 0
                    and self._is_edit_target_error(str(exc))
                )
                missing_repo_file_second_chance = (
                    not standard_retry_available
                    and remaining_missing_repo_file_second_chances > 0
                    and self._is_missing_repo_file_error(str(exc))
                )
                missing_relative_path_second_chance = (
                    not standard_retry_available
                    and remaining_missing_relative_path_second_chances > 0
                    and self._is_missing_relative_path_error(str(exc))
                )
                patch_contract_second_chance = (
                    not standard_retry_available
                    and remaining_patch_contract_second_chances > 0
                    and self._is_patch_contract_error(str(exc))
                )
                if (
                    not standard_retry_available
                    and not approval_path_second_chance
                    and not directory_path_second_chance
                    and not edit_target_second_chance
                    and not missing_relative_path_second_chance
                    and not missing_repo_file_second_chance
                    and not patch_contract_second_chance
                ):
                    raise
                if approval_path_second_chance:
                    remaining_approval_path_second_chances -= 1
                    session.record_event(
                        "agent_step_output_approval_path_second_chance_requested",
                        model=response.model,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                if directory_path_second_chance:
                    remaining_directory_path_second_chances -= 1
                    session.record_event(
                        "agent_step_output_directory_path_second_chance_requested",
                        model=response.model,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                if edit_target_second_chance:
                    remaining_edit_target_second_chances -= 1
                    session.record_event(
                        "agent_step_output_edit_target_second_chance_requested",
                        model=response.model,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                if missing_relative_path_second_chance:
                    remaining_missing_relative_path_second_chances -= 1
                    session.record_event(
                        "agent_step_output_missing_relative_path_second_chance_requested",
                        model=response.model,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                if missing_repo_file_second_chance:
                    remaining_missing_repo_file_second_chances -= 1
                    session.record_event(
                        "agent_step_output_missing_repo_file_second_chance_requested",
                        model=response.model,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                if patch_contract_second_chance:
                    remaining_patch_contract_second_chances -= 1
                    session.record_event(
                        "agent_step_output_patch_contract_second_chance_requested",
                        model=response.model,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
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

    def _build_plan_repair_prompt_context(
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
                    "Return corrected JSON only using the required plan schema. "
                    "Do not include markdown fences, prose, or comments. Include "
                    "a non-empty plan_markdown string and a todos array with "
                    "exactly one in_progress todo. Do not invent runtime tools."
                ),
                "required_schema": {
                    "plan_markdown": "1. Inspect\n2. Patch\n3. Test",
                    "todos": [
                        {
                            "content": "Inspect the relevant repo files",
                            "active_form": "Inspecting the relevant repo files",
                            "status": "in_progress",
                        },
                        {
                            "content": "Apply the smallest safe change",
                            "active_form": "Applying the smallest safe change",
                            "status": "pending",
                        },
                        {
                            "content": "Run local tests",
                            "active_form": "Running local tests",
                            "status": "pending",
                        },
                    ],
                },
            },
        }

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
                "approval_path_repair": self._build_approval_path_repair(
                    validation_error
                ),
                "directory_path_repair": self._build_directory_path_repair(
                    validation_error
                ),
                "edit_target_repair": self._build_edit_target_repair(
                    session=session,
                    validation_error=validation_error,
                ),
                "missing_relative_path_repair": self._build_missing_relative_path_repair(
                    session=session,
                    validation_error=validation_error,
                ),
                "read_focus_repair": self._build_read_focus_repair(
                    session=session,
                    validation_error=validation_error,
                ),
                "missing_repo_file_repair": self._build_missing_repo_file_repair(
                    validation_error
                ),
                "patch_contract_repair": self._build_patch_contract_repair(
                    session=session,
                    validation_error=validation_error,
                    previous_output=previous_output,
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
            read_focus_error = session.validate_tool_request_read_focus(tool_request)
            if read_focus_error:
                raise ModelClientError(read_focus_error)
            edit_context_error = session.validate_tool_request_edit_context(tool_request)
            if edit_context_error:
                raise ModelClientError(edit_context_error)
            approval_focus_error = session.validate_tool_request_approval_focus(
                tool_request
            )
            if approval_focus_error:
                raise ModelClientError(approval_focus_error)
            completion_contract_error = (
                session.validate_tool_request_completion_contract(tool_request)
            )
            if completion_contract_error:
                raise ModelClientError(completion_contract_error)

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

    def _is_directory_path_error(self, validation_error: str) -> bool:
        return "directory path for" in validation_error.lower()

    def _is_approval_path_error(self, validation_error: str) -> bool:
        lowered = validation_error.lower()
        return (
            "selected shell for a local test command" in lowered
            or "selected shell to read a repo file directly" in lowered
        )

    def _is_edit_target_error(self, validation_error: str) -> bool:
        lowered = validation_error.lower()
        return (
            "off-target edit path for" in lowered
            or "edit without recent file context for" in lowered
        )

    def _is_missing_relative_path_error(self, validation_error: str) -> bool:
        return "relative_path is required for" in validation_error.lower()

    def _is_missing_repo_file_error(self, validation_error: str) -> bool:
        return "missing repo file for" in validation_error.lower()

    def _is_patch_contract_error(self, validation_error: str) -> bool:
        lowered = validation_error.lower()
        return (
            "no-op file_patch" in lowered
            or "bad patch snippet for file_patch" in lowered
            or "expected_old_snippet was not found in" in lowered
            or "expected_old_snippet matched multiple locations in" in lowered
            or "expected_old_snippet cannot be empty" in lowered
            or "expected_old_snippet is required for file_patch" in lowered
            or "new_snippet is required for file_patch" in lowered
        )

    def _build_patch_contract_repair(
        self,
        *,
        session: TaskSession,
        validation_error: str,
        previous_output: str,
    ) -> Optional[Dict[str, Any]]:
        if not self._is_patch_contract_error(validation_error):
            return None

        attempted_request = self._extract_patch_contract_request(previous_output)
        attempted_target_path = None
        attempted_expected_old_snippet = None
        attempted_new_snippet = None
        if attempted_request:
            attempted_target_path = attempted_request.get("relative_path")
            attempted_expected_old_snippet = attempted_request.get(
                "expected_old_snippet"
            )
            attempted_new_snippet = attempted_request.get("new_snippet")

        read_focus = session.build_read_focus_snapshot()
        primary_target_path = read_focus.get("primary_target_path")
        patch_target_path = (
            self._extract_patch_contract_relative_path(validation_error)
            or attempted_target_path
            or primary_target_path
        )
        recent_read_anchor = None
        if patch_target_path:
            recent_read_anchor = self._build_patch_contract_recent_read_anchor(
                session=session,
                relative_path=patch_target_path,
                query=self._build_patch_contract_anchor_query(
                    task_input=session.task_input,
                    attempted_expected_old_snippet=attempted_expected_old_snippet,
                    patch_target_path=patch_target_path,
                ),
            )
        instruction = (
            "The previous file_patch broke the patch contract. Return corrected "
            "JSON only using the same schema. expected_old_snippet must be a "
            "non-empty exact snippet from the target file, new_snippet must differ "
            "from expected_old_snippet, and the edit must produce a repo diff."
        )
        if "expected_old_snippet was not found in" in validation_error.lower():
            instruction += (
                " The previous expected_old_snippet does not exist in the current "
                "repo file. Do not invent or approximate the snippet."
            )
        if "expected_old_snippet matched multiple locations in" in validation_error.lower():
            instruction += (
                " The previous expected_old_snippet was too short or ambiguous and "
                "matched multiple places. Use a longer exact snippet that uniquely "
                "identifies the intended line or local block."
            )
        if patch_target_path:
            instruction += " Keep the patch target on {0}.".format(patch_target_path)
        if recent_read_anchor:
            instruction += (
                " Use recent_read_anchor as the source of truth and copy "
                "expected_old_snippet exactly from that excerpt instead of "
                "paraphrasing it."
            )
            anchor_line = str(recent_read_anchor.get("anchor_line") or "").strip()
            anchor_line_number = recent_read_anchor.get("anchor_line_number")
            if anchor_line and anchor_line_number is not None:
                instruction += (
                    " For a single-line patch, prefer the exact anchor_line at line "
                    "{0}: {1}."
                ).format(anchor_line_number, _preview_text(anchor_line, limit=140))
            instruction += (
                " Keep new_snippet focused on the same local area as that anchor."
            )
        if primary_target_path:
            instruction += (
                " Prefer patching the current primary target {0}. If you cannot "
                "produce a real patch for that file, choose run_test or read_file "
                "instead of returning another no-op file_patch."
            ).format(primary_target_path)
        else:
            instruction += (
                " If you do not have enough file context for a real patch, read the "
                "target file before editing."
            )

        return {
            "primary_target_path": primary_target_path,
            "patch_target_path": patch_target_path,
            "attempted_expected_old_snippet": attempted_expected_old_snippet,
            "attempted_new_snippet": attempted_new_snippet,
            "recent_read_anchor": recent_read_anchor,
            "instruction": instruction,
        }

    def _build_directory_path_repair(
        self, validation_error: str
    ) -> Optional[Dict[str, Any]]:
        if not self._is_directory_path_error(validation_error):
            return None

        suggested_relative_path = None
        match = re.search(
            r"Choose a file path such as ([^.\n]+(?:\.[^.\n]+)+)\.",
            validation_error,
        )
        if match:
            suggested_relative_path = match.group(1).strip()

        instruction = (
            "The previous tool_request used a directory path. "
            "Return the same JSON schema, but replace relative_path with an existing "
            "file path inside the repo. Do not return a directory path again."
        )
        if suggested_relative_path:
            instruction += (
                " Prefer this exact existing file path if it fits the task: {0}."
            ).format(suggested_relative_path)

        return {
            "must_choose_file_path": True,
            "suggested_relative_path": suggested_relative_path,
            "instruction": instruction,
        }

    def _build_approval_path_repair(
        self, validation_error: str
    ) -> Optional[Dict[str, Any]]:
        if not self._is_approval_path_error(validation_error):
            return None

        instruction = (
            "The previous tool_request used shell even though a more specific runtime "
            "tool should be used. Return corrected JSON only using the same schema."
        )

        shell_read_relative_path = self._extract_shell_read_relative_path(
            validation_error
        )
        if shell_read_relative_path:
            instruction += (
                " Change tool_type to read_file and use relative_path {0}. "
                "Do not use shell for direct repo file reads."
            ).format(shell_read_relative_path)
            return {
                "preferred_tool_type": "read_file",
                "relative_path": shell_read_relative_path,
                "instruction": instruction,
            }

        if "local test command" in validation_error.lower():
            instruction += (
                " Change tool_type to run_test and keep the same local test command. "
                "Do not use shell for the local test suite."
            )
            return {
                "preferred_tool_type": "run_test",
                "instruction": instruction,
            }

        return None

    def _build_edit_target_repair(
        self, *, session: TaskSession, validation_error: str
    ) -> Optional[Dict[str, Any]]:
        if not self._is_edit_target_error(validation_error):
            return None

        tool_name = self._extract_edit_target_tool_name(validation_error)
        attempted_relative_path = self._extract_edit_target_relative_path(
            validation_error
        )
        read_focus = session.build_read_focus_snapshot()
        primary_target_path = read_focus.get("primary_target_path")
        recent_context_paths = list(read_focus.get("recent_context_paths") or [])

        instruction = (
            "The previous tool_request broke the current edit-target binding. "
            "Return corrected JSON only using the same schema."
        )
        if primary_target_path and attempted_relative_path:
            instruction += (
                " The active primary target from recent_file_contexts is {0}, "
                "but the previous output tried to edit {1}."
            ).format(primary_target_path, attempted_relative_path)
        elif primary_target_path:
            instruction += (
                " The active primary target from recent_file_contexts is {0}."
            ).format(primary_target_path)
        elif attempted_relative_path:
            instruction += " The previous output tried to edit {0} without context.".format(
                attempted_relative_path
            )

        if primary_target_path and tool_name in {"file_patch", "write_file"}:
            instruction += " Prefer {0} on {1}.".format(
                tool_name, primary_target_path
            )
        if attempted_relative_path and attempted_relative_path != primary_target_path:
            instruction += (
                " Do not edit {0} unless you first read that file in the current repo state."
            ).format(attempted_relative_path)
        elif attempted_relative_path:
            instruction += " Read {0} before editing it if you still need that target.".format(
                attempted_relative_path
            )

        return {
            "tool_type": tool_name,
            "attempted_relative_path": attempted_relative_path,
            "primary_target_path": primary_target_path,
            "recent_context_paths": recent_context_paths,
            "instruction": instruction,
        }

    def _build_missing_relative_path_repair(
        self, *, session: TaskSession, validation_error: str
    ) -> Optional[Dict[str, Any]]:
        if not self._is_missing_relative_path_error(validation_error):
            return None

        tool_name = self._extract_missing_relative_path_tool_name(validation_error)
        suggested_relative_paths: List[str] = []
        if tool_name in {"read_file", "file_patch", "write_file"}:
            suggested_relative_paths = session.suggest_existing_files_for_missing_relative_path(
                tool_name=tool_name,
                limit=3,
            )

        instruction = (
            "The previous tool_request omitted the required relative_path field. "
            "Return the same JSON schema, include a non-empty relative_path, and do "
            "not omit the field again."
        )
        if tool_name in {"read_file", "file_patch", "write_file"}:
            instruction += (
                " For {0}, relative_path must point to an existing repo file."
            ).format(tool_name)
        if suggested_relative_paths:
            instruction += " Choose one of these existing file paths if it fits the task: {0}.".format(
                ", ".join(suggested_relative_paths)
            )

        return {
            "required_field": "relative_path",
            "tool_type": tool_name,
            "suggested_relative_paths": suggested_relative_paths,
            "instruction": instruction,
        }

    def _build_missing_repo_file_repair(
        self, validation_error: str
    ) -> Optional[Dict[str, Any]]:
        if not self._is_missing_repo_file_error(validation_error):
            return None

        suggested_relative_paths = self._extract_missing_repo_file_suggestions(
            validation_error
        )
        instruction = (
            "The previous tool_request used a relative_path that does not exist in "
            "the repo. Return the same JSON schema, but replace relative_path with "
            "an existing repo file path. Do not invent a new filename."
        )
        if suggested_relative_paths:
            instruction += " Choose one of these existing file paths if it fits the task: {0}.".format(
                ", ".join(suggested_relative_paths)
            )

        return {
            "must_choose_existing_file_path": True,
            "suggested_relative_paths": suggested_relative_paths,
            "instruction": instruction,
        }

    def _build_read_focus_repair(
        self, *, session: TaskSession, validation_error: str
    ) -> Optional[Dict[str, Any]]:
        blocked_relative_path = self._extract_reread_relative_path(validation_error)
        if not blocked_relative_path:
            return None

        read_focus = session.build_read_focus_snapshot()
        preferred_next_action = str(read_focus.get("preferred_next_action") or "")
        instruction = (
            "The previous tool_request reread a file whose context is already "
            "available in recent_file_contexts. Return the same JSON schema, do not "
            "request read_file for {0} again, and use the existing context instead."
        ).format(blocked_relative_path)
        if preferred_next_action == "patch_or_test":
            instruction += (
                " Prefer file_patch/write_file for the current target, or run_test if "
                "the repo state is ready."
            )
        elif preferred_next_action == "finish":
            instruction += " The current repo state already passed local tests, so finish."
        elif preferred_next_action == "inspect_test_failure":
            instruction += (
                " Use recent_test_failures before choosing a different file to read."
            )

        alternative_relative_path = self._extract_read_focus_alternative_path(
            validation_error
        )
        if alternative_relative_path:
            instruction += (
                " If you truly need another read, prefer {0} instead."
            ).format(alternative_relative_path)

        return {
            "blocked_relative_path": blocked_relative_path,
            "preferred_next_action": preferred_next_action,
            "avoid_reread_paths": list(read_focus.get("avoid_reread_paths") or []),
            "primary_target_path": read_focus.get("primary_target_path"),
            "instruction": instruction,
        }

    def _extract_missing_repo_file_suggestions(
        self, validation_error: str
    ) -> List[str]:
        match = re.search(
            r"Choose one of these existing file paths instead: \[([^\]]+)\]\.",
            validation_error,
        )
        if not match:
            return []
        return [
            suggestion.strip()
            for suggestion in match.group(1).split(",")
            if suggestion.strip()
        ]

    def _extract_missing_relative_path_tool_name(
        self, validation_error: str
    ) -> Optional[str]:
        match = re.search(
            r"relative_path is required for ([a-z_]+)\.",
            validation_error,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip().lower()

    def _extract_edit_target_tool_name(
        self, validation_error: str
    ) -> Optional[str]:
        match = re.search(
            r"for ([a-z_]+):",
            validation_error,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip().lower()

    def _extract_edit_target_relative_path(
        self, validation_error: str
    ) -> Optional[str]:
        match = re.search(
            r"for [a-z_]+: ([^\n]+?)\.",
            validation_error,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    def _extract_reread_relative_path(self, validation_error: str) -> Optional[str]:
        match = re.search(
            r"Model is rereading ([^\s]+)",
            validation_error,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    def _extract_read_focus_alternative_path(
        self, validation_error: str
    ) -> Optional[str]:
        match = re.search(
            r"such as ([^.\n]+(?:\.[^.\n]+)+) instead\.",
            validation_error,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    def _extract_shell_read_relative_path(
        self, validation_error: str
    ) -> Optional[str]:
        match = re.search(
            r"repo file directly: ([^\s]+)\.",
            validation_error,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    def _extract_patch_contract_relative_path(
        self, validation_error: str
    ) -> Optional[str]:
        match = re.search(
            r"expected_old_snippet (?:was not found|matched multiple locations) in ([^\s]+)\.",
            validation_error,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    def _extract_patch_contract_request(
        self, previous_output: str
    ) -> Optional[Dict[str, str]]:
        try:
            payload = _parse_json_object(previous_output)
        except ModelClientError:
            return None

        tool_payload = payload.get("tool_request")
        if not isinstance(tool_payload, dict):
            return None
        if str(tool_payload.get("tool_type") or "").strip() != "file_patch":
            return None

        request: Dict[str, str] = {}
        for field_name in ("relative_path", "expected_old_snippet", "new_snippet"):
            raw_value = tool_payload.get(field_name)
            if raw_value is None:
                continue
            request[field_name] = str(raw_value)
        return request or None

    def _build_patch_contract_anchor_query(
        self,
        *,
        task_input: Optional[str],
        attempted_expected_old_snippet: Optional[str],
        patch_target_path: str,
    ) -> str:
        query_parts = [
            str(task_input or "").strip(),
            str(attempted_expected_old_snippet or "").strip(),
            str(patch_target_path or "").strip(),
        ]
        return "\n".join(part for part in query_parts if part)

    def _build_patch_contract_recent_read_anchor(
        self, *, session: TaskSession, relative_path: str, query: str
    ) -> Optional[Dict[str, Any]]:
        recent_context = self._find_recent_file_context_for_path(
            session=session,
            relative_path=relative_path,
        )
        if recent_context is None:
            return None

        anchor = self._select_patch_anchor_from_content(
            content=recent_context.content,
            query=query,
        )
        if anchor is None:
            return None

        anchor["relative_path"] = Path(recent_context.relative_path).as_posix()
        anchor["source_tool"] = recent_context.source_tool
        anchor["captured_at"] = recent_context.captured_at
        return anchor

    def _find_recent_file_context_for_path(
        self, *, session: TaskSession, relative_path: str
    ) -> Optional[Any]:
        normalized_relative_path = Path(relative_path).as_posix()
        fallback = None
        for item in reversed(session.recent_file_contexts):
            if Path(item.relative_path).as_posix() != normalized_relative_path:
                continue
            if fallback is None:
                fallback = item
            if item.source_tool == "read_file":
                return item
        return fallback

    def _select_patch_anchor_from_content(
        self, *, content: str, query: str
    ) -> Optional[Dict[str, Any]]:
        lines = content.splitlines()
        if not lines:
            stripped = content.strip()
            if not stripped:
                return None
            return {
                "start_line": 1,
                "end_line": 1,
                "anchor_line_number": 1,
                "anchor_line": stripped,
                "excerpt": stripped,
            }

        start_line_index, end_line_index, anchor_line_index = (
            self._best_patch_anchor_span(lines, query)
        )
        excerpt_start = max(0, start_line_index - 1)
        excerpt_end = min(len(lines), end_line_index + 1)
        excerpt = "\n".join(lines[excerpt_start:excerpt_end]).strip("\n")
        if not excerpt:
            excerpt = "\n".join(lines[start_line_index:end_line_index]).strip("\n")

        return {
            "start_line": excerpt_start + 1,
            "end_line": excerpt_end,
            "anchor_line_number": anchor_line_index + 1,
            "anchor_line": lines[anchor_line_index],
            "excerpt": excerpt,
            "line_numbered_excerpt": self._line_numbered_excerpt(
                lines=lines,
                start_index=excerpt_start,
                end_index=excerpt_end,
            ),
        }

    def _line_numbered_excerpt(
        self, *, lines: List[str], start_index: int, end_index: int
    ) -> str:
        return "\n".join(
            "{0}: {1}".format(index + 1, lines[index])
            for index in range(start_index, end_index)
        )

    def _best_patch_anchor_span(
        self, lines: List[str], query: str
    ) -> Tuple[int, int, int]:
        non_empty_indexes = [index for index, line in enumerate(lines) if line.strip()]
        if not non_empty_indexes:
            return (0, 1, 0)

        normalized_query = _normalize_text_for_match(query)
        if not normalized_query:
            first_non_empty = non_empty_indexes[0]
            return (first_non_empty, first_non_empty + 1, first_non_empty)

        query_line_count = max(1, min(4, query.count("\n") + 1))
        window_sizes = sorted(
            {
                1,
                query_line_count,
                min(len(lines), query_line_count + 1),
                min(len(lines), query_line_count + 2),
            }
        )

        best_score = -1.0
        best_span = (non_empty_indexes[0], non_empty_indexes[0] + 1, non_empty_indexes[0])

        for window_size in window_sizes:
            for start in range(0, len(lines) - window_size + 1):
                candidate_lines = lines[start : start + window_size]
                candidate_text = "\n".join(candidate_lines).strip()
                if not candidate_text:
                    continue
                score = self._patch_anchor_match_score(
                    candidate_text, normalized_query
                )
                anchor_offset = max(
                    range(len(candidate_lines)),
                    key=lambda index: self._patch_anchor_match_score(
                        candidate_lines[index], normalized_query
                    ),
                )
                candidate_span = (start, start + window_size, start + anchor_offset)
                if score > best_score:
                    best_score = score
                    best_span = candidate_span
                    continue
                if score == best_score and window_size < (
                    best_span[1] - best_span[0]
                ):
                    best_span = candidate_span

        return best_span

    def _patch_anchor_match_score(self, candidate: str, normalized_query: str) -> float:
        normalized_candidate = _normalize_text_for_match(candidate)
        if not normalized_candidate:
            return 0.0
        if not normalized_query:
            return 0.1

        ratio = difflib.SequenceMatcher(
            None, normalized_candidate, normalized_query
        ).ratio()
        query_tokens = _text_tokens_for_match(normalized_query)
        candidate_tokens = _text_tokens_for_match(normalized_candidate)
        shared_tokens = query_tokens & candidate_tokens
        overlap_score = 0.0
        if query_tokens:
            overlap_score = len(shared_tokens) / len(query_tokens)

        query_digit_tokens = {
            token for token in query_tokens if any(char.isdigit() for char in token)
        }
        digit_overlap_score = 0.0
        if query_digit_tokens:
            digit_overlap_score = len(query_digit_tokens & candidate_tokens) / len(
                query_digit_tokens
            )

        containment_bonus = 0.0
        if (
            normalized_query in normalized_candidate
            or normalized_candidate in normalized_query
        ):
            containment_bonus = 0.2

        return ratio + overlap_score + digit_overlap_score + containment_bonus


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


def _normalize_text_for_match(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _text_tokens_for_match(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if len(token) >= 2
    }


def _preview_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return "{0}...".format(value[: max(0, limit - 3)])


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
