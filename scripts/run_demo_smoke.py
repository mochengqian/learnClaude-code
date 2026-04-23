from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VENDOR_DIR = REPO_ROOT / ".vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

from repo_task_runtime import AgentRunner, ModelResponse
from repo_task_runtime.api import create_app


class ScriptedModelClient:
    def __init__(self, responses: Iterable[Dict[str, Any]]) -> None:
        self.responses: List[str] = [
            json.dumps(response, ensure_ascii=False) for response in responses
        ]

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        if not self.responses:
            raise AssertionError("No scripted model responses remaining.")
        return ModelResponse(
            text=self.responses.pop(0),
            model="scripted-demo-smoke",
            usage={"total_tokens": 0},
        )


def main() -> int:
    try:
        from fastapi.testclient import TestClient
    except ModuleNotFoundError as exc:
        raise SystemExit("fastapi is required for the demo smoke script.") from exc

    app = create_app(agent_runner=AgentRunner(ScriptedModelClient(_responses())))
    client = TestClient(app)

    demo = _post_json(client, "/demo/setup")["demo"]
    session = _post_json(
        client,
        "/sessions",
        {"repo_path": demo["repo_path"], "task_input": demo["task_input"]},
    )["session"]
    session_id = session["session_id"]

    _post_json(client, f"/sessions/{session_id}/agent/plan")
    _post_json(client, f"/sessions/{session_id}/plan/approve")

    first_loop = _post_json(
        client,
        f"/sessions/{session_id}/agent/loop",
        {"max_steps": 4},
    )
    if first_loop["agent"]["stop_reason"] != "approval_required":
        raise AssertionError("Expected the first loop to stop at edit approval.")

    approval = _single_pending_approval(first_loop["session"])
    approval_result = _post_json(
        client,
        f"/sessions/{session_id}/approvals/{approval['approval_id']}/resolve",
        {"approve": True},
    )["result"]
    if approval_result["status"] != "executed":
        raise AssertionError("Expected edit approval resolution to execute the patch.")

    second_loop = _post_json(
        client,
        f"/sessions/{session_id}/agent/loop",
        {"max_steps": 3},
    )
    if second_loop["agent"]["stop_reason"] != "finished":
        raise AssertionError("Expected the second loop to finish after a passing test.")

    session = _get_json(client, f"/sessions/{session_id}")["session"]
    _assert_closed_loop(session)

    summary = {
        "status": "ok",
        "session_id": session_id,
        "repo_path": demo["repo_path"],
        "first_loop_stop": first_loop["agent"]["stop_reason"],
        "second_loop_stop": second_loop["agent"]["stop_reason"],
        "approval_kind": approval["approval_kind"],
        "latest_diff_chars": len(session["latest_diff"]),
        "latest_successful_test": session["latest_successful_test"] is not None,
        "timeline_events": len(session["timeline"]),
    }
    print("M3 demo smoke completed")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _responses() -> List[Dict[str, Any]]:
    return [
        {
            "plan_markdown": (
                "1. Read the failing module.\n"
                "2. Apply the smallest slug join fix.\n"
                "3. Run the local unittest suite."
            ),
            "todos": [
                {
                    "content": "Read the failing module",
                    "active_form": "Reading the failing module",
                    "status": "in_progress",
                },
                {
                    "content": "Fix the slug join character",
                    "active_form": "Fixing the slug join character",
                    "status": "pending",
                },
                {
                    "content": "Run the local unittest suite",
                    "active_form": "Running the local unittest suite",
                    "status": "pending",
                },
            ],
        },
        {
            "summary": "Read the target module before editing.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "read_file",
                "relative_path": "demo_app/string_tools.py",
            },
        },
        {
            "summary": "Run the current tests to capture the failing behavior.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "run_test",
                "command": [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ],
            },
        },
        {
            "summary": "Patch the join character in the read target file.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "file_patch",
                "relative_path": "demo_app/string_tools.py",
                "expected_old_snippet": 'return "_".join(pieces)',
                "new_snippet": 'return "-".join(pieces)',
            },
        },
        {
            "summary": "Verify the edited repo state with the local test suite.",
            "action": "request_tool",
            "tool_request": {
                "tool_type": "run_test",
                "command": [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ],
            },
        },
        {
            "summary": "The repo state has a diff and the local tests passed.",
            "action": "finish",
        },
    ]


def _post_json(client: Any, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    response = client.post(path, json=payload)
    if response.status_code != 200:
        raise AssertionError("{0} failed: {1}".format(path, response.text))
    return response.json()


def _get_json(client: Any, path: str) -> Dict[str, Any]:
    response = client.get(path)
    if response.status_code != 200:
        raise AssertionError("{0} failed: {1}".format(path, response.text))
    return response.json()


def _single_pending_approval(session: Dict[str, Any]) -> Dict[str, Any]:
    pending = session["pending_approvals"]
    if len(pending) != 1:
        raise AssertionError("Expected exactly one pending approval.")
    approval = pending[0]
    if approval["approval_kind"] != "edit":
        raise AssertionError("Expected an edit approval.")
    return approval


def _assert_closed_loop(session: Dict[str, Any]) -> None:
    if "demo_app/string_tools.py" not in session["latest_diff"]:
        raise AssertionError("Expected the latest diff to include the target file.")
    if session["latest_successful_test"] is None:
        raise AssertionError("Expected a successful local test for the final repo state.")

    event_types = [event["event_type"] for event in session["timeline"]]
    required_events = {
        "agent_plan_drafted",
        "plan_mode_exited",
        "agent_loop_started",
        "approval_requested",
        "approval_granted",
        "repo_state_mutated",
        "local_test_completed",
        "agent_step_finished",
    }
    missing = sorted(required_events.difference(event_types))
    if missing:
        raise AssertionError("Missing timeline events: {0}".format(", ".join(missing)))


if __name__ == "__main__":
    raise SystemExit(main())
