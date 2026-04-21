from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_VENDOR_DIR = Path(__file__).resolve().parent.parent / ".vendor"
if _VENDOR_DIR.exists():
    sys.path.insert(0, str(_VENDOR_DIR))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import AgentRunner
from .demo_repo import DemoRepoInfo, create_demo_repo
from .model_client import ModelClientError, create_model_client_from_env
from .models import (
    TaskSnapshot,
    TodoItem,
    TodoStatus,
    ToolExecutionResult,
    tool_request_from_payload,
)
from .workbench import TaskWorkbench


class CreateSessionBody(BaseModel):
    repo_path: str
    task_input: Optional[str] = None


class BeginTaskBody(BaseModel):
    task_input: str


class UpdatePlanBody(BaseModel):
    plan_markdown: str


class ApprovalDecisionBody(BaseModel):
    approve: bool


class TodoBody(BaseModel):
    content: str
    status: TodoStatus = TodoStatus.PENDING
    active_form: Optional[str] = None
    id: Optional[str] = None


class ReplaceTodosBody(BaseModel):
    todos: List[TodoBody]


class ToolRequestBody(BaseModel):
    tool_type: str = Field(pattern="^(read_file|write_file|shell|run_test)$")
    relative_path: Optional[str] = None
    content: Optional[str] = None
    command: List[str] = Field(default_factory=list)
    timeout_seconds: Optional[int] = None


class AgentLoopBody(BaseModel):
    max_steps: int = Field(default=3, ge=1, le=8)


class SessionEnvelope(BaseModel):
    session: Dict[str, Any]


class ToolEnvelope(BaseModel):
    result: Dict[str, Any]
    session: Dict[str, Any]


class AgentEnvelope(BaseModel):
    agent: Dict[str, Any]
    session: Dict[str, Any]


class DemoEnvelope(BaseModel):
    demo: Dict[str, Any]


def create_app(
    workbench: Optional[TaskWorkbench] = None,
    agent_runner: Optional[AgentRunner] = None,
) -> FastAPI:
    runtime = workbench or TaskWorkbench()
    model_agent = agent_runner
    if model_agent is None:
        model_client = create_model_client_from_env()
        if model_client is not None:
            model_agent = AgentRunner(model_client)
    app = FastAPI(title="Repo-Task Runtime API", version="0.1.0")
    app.state.workbench = runtime
    app.state.agent_runner = model_agent
    web_dir = Path(__file__).resolve().parent / "web"
    app.mount("/assets", StaticFiles(directory=str(web_dir)), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def console() -> HTMLResponse:
        html = (web_dir / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(content=html)

    @app.get("/healthz")
    def healthz() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/demo/setup", response_model=DemoEnvelope)
    def setup_demo_repo() -> DemoEnvelope:
        demo = create_demo_repo()
        return DemoEnvelope(demo=_demo_to_dict(demo))

    @app.post("/sessions", response_model=SessionEnvelope)
    def create_session(body: CreateSessionBody) -> SessionEnvelope:
        session = runtime.create_session(Path(body.repo_path))
        if body.task_input:
            session.begin_task(body.task_input)
        return SessionEnvelope(session=_snapshot_to_dict(session.snapshot()))

    @app.post("/sessions/{session_id}/task", response_model=SessionEnvelope)
    def begin_task(session_id: str, body: BeginTaskBody) -> SessionEnvelope:
        session = _get_session(runtime, session_id)
        session.begin_task(body.task_input)
        return SessionEnvelope(session=_snapshot_to_dict(session.snapshot()))

    @app.post("/sessions/{session_id}/plan", response_model=SessionEnvelope)
    def update_plan(session_id: str, body: UpdatePlanBody) -> SessionEnvelope:
        session = _get_session(runtime, session_id)
        try:
            session.update_plan(body.plan_markdown)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return SessionEnvelope(session=_snapshot_to_dict(session.snapshot()))

    @app.post("/sessions/{session_id}/plan/approve", response_model=SessionEnvelope)
    def approve_plan(session_id: str) -> SessionEnvelope:
        session = _get_session(runtime, session_id)
        try:
            session.approve_plan()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return SessionEnvelope(session=_snapshot_to_dict(session.snapshot()))

    @app.put("/sessions/{session_id}/todos", response_model=SessionEnvelope)
    def replace_todos(session_id: str, body: ReplaceTodosBody) -> SessionEnvelope:
        session = _get_session(runtime, session_id)
        todos = [
            TodoItem(
                id=item.id or "",
                content=item.content,
                active_form=item.active_form,
                status=item.status,
            )
            if item.id
            else TodoItem(
                content=item.content,
                active_form=item.active_form,
                status=item.status,
            )
            for item in body.todos
        ]
        try:
            session.replace_todos(todos)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return SessionEnvelope(session=_snapshot_to_dict(session.snapshot()))

    @app.get("/sessions/{session_id}", response_model=SessionEnvelope)
    def get_session(session_id: str) -> SessionEnvelope:
        session = _get_session(runtime, session_id)
        return SessionEnvelope(session=_snapshot_to_dict(session.snapshot()))

    @app.post("/sessions/{session_id}/tools", response_model=ToolEnvelope)
    def request_tool(session_id: str, body: ToolRequestBody) -> ToolEnvelope:
        session = _get_session(runtime, session_id)
        try:
            request = _build_tool_request(body)
            result = session.request_tool(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return ToolEnvelope(
            result=_result_to_dict(result),
            session=_snapshot_to_dict(session.snapshot()),
        )

    @app.post(
        "/sessions/{session_id}/approvals/{approval_id}/resolve",
        response_model=ToolEnvelope,
    )
    def resolve_approval(
        session_id: str, approval_id: str, body: ApprovalDecisionBody
    ) -> ToolEnvelope:
        session = _get_session(runtime, session_id)
        try:
            result = session.resolve_approval(approval_id, approve=body.approve)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return ToolEnvelope(
            result=_result_to_dict(result),
            session=_snapshot_to_dict(session.snapshot()),
        )

    @app.post("/sessions/{session_id}/agent/plan", response_model=AgentEnvelope)
    def generate_plan(session_id: str) -> AgentEnvelope:
        session = _get_session(runtime, session_id)
        runner = _get_agent_runner(app)
        try:
            draft = runner.draft_plan(session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ModelClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return AgentEnvelope(
            agent=draft.to_dict(),
            session=_snapshot_to_dict(session.snapshot()),
        )

    @app.post("/sessions/{session_id}/agent/step", response_model=AgentEnvelope)
    def run_agent_step(session_id: str) -> AgentEnvelope:
        session = _get_session(runtime, session_id)
        runner = _get_agent_runner(app)
        try:
            outcome = runner.run_next_step(session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ModelClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return AgentEnvelope(
            agent=outcome.to_dict(),
            session=_snapshot_to_dict(session.snapshot()),
        )

    @app.post("/sessions/{session_id}/agent/loop", response_model=AgentEnvelope)
    def run_agent_loop(session_id: str, body: AgentLoopBody) -> AgentEnvelope:
        session = _get_session(runtime, session_id)
        runner = _get_agent_runner(app)
        try:
            outcome = runner.run_loop(session, max_steps=body.max_steps)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ModelClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return AgentEnvelope(
            agent=outcome.to_dict(),
            session=_snapshot_to_dict(session.snapshot()),
        )

    return app


def _get_session(runtime: TaskWorkbench, session_id: str):
    try:
        return runtime.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _get_agent_runner(app: FastAPI) -> AgentRunner:
    runner = app.state.agent_runner
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Model agent is not configured. Set REPO_TASK_MODEL_BASE_URL, "
                "REPO_TASK_MODEL_API_KEY, and REPO_TASK_MODEL_NAME to enable it."
            ),
        )
    return runner


def _build_tool_request(body: ToolRequestBody):
    return tool_request_from_payload(
        {
            "tool_type": body.tool_type,
            "relative_path": body.relative_path,
            "content": body.content,
            "command": body.command,
            "timeout_seconds": body.timeout_seconds,
        }
    )


def _snapshot_to_dict(snapshot: TaskSnapshot) -> Dict[str, Any]:
    return snapshot.to_dict()


def _result_to_dict(result: ToolExecutionResult) -> Dict[str, Any]:
    return result.to_dict()


def _demo_to_dict(demo: DemoRepoInfo) -> Dict[str, Any]:
    return demo.to_dict()


app = create_app()
