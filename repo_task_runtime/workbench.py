from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .approval import ApprovalPolicy
from .session import TaskSession


class TaskWorkbench:
    def __init__(self, approval_policy: Optional[ApprovalPolicy] = None) -> None:
        self.approval_policy = approval_policy or ApprovalPolicy()
        self.sessions: Dict[str, TaskSession] = {}

    def create_session(self, repo_path: Path) -> TaskSession:
        session = TaskSession(
            repo_path=repo_path,
            approval_policy=self.approval_policy,
        )
        self.sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> TaskSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise KeyError("Unknown session id: {0}".format(session_id)) from exc
