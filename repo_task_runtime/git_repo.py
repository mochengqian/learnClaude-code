from __future__ import annotations

import subprocess
from pathlib import Path


def initialize_git_repo(
    repo_path: Path,
    *,
    user_email: str,
    user_name: str,
    initial_commit_message: str,
) -> None:
    commands = (
        ("git", "init"),
        ("git", "config", "user.email", user_email),
        ("git", "config", "user.name", user_name),
        ("git", "add", "."),
        ("git", "commit", "-m", initial_commit_message),
    )
    for command in commands:
        subprocess.run(command, cwd=str(repo_path), check=True, capture_output=True)
