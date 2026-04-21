from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DemoRepoInfo:
    repo_path: str
    task_input: str
    test_command: str
    notes: str

    def to_dict(self):
        return {
            "repo_path": self.repo_path,
            "task_input": self.task_input,
            "test_command": self.test_command,
            "notes": self.notes,
        }


def get_demo_repo_template_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "examples" / "demo_repo_template"


def create_demo_repo(target_dir: Optional[Path] = None) -> DemoRepoInfo:
    source_dir = get_demo_repo_template_dir()
    if not source_dir.exists():
        raise FileNotFoundError("Demo repo template is missing.")

    if target_dir is None:
        destination = Path(tempfile.mkdtemp(prefix="repo-task-demo-"))
    else:
        destination = Path(target_dir).resolve()
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Target demo repo directory must be empty.")
        destination.mkdir(parents=True, exist_ok=True)

    shutil.copytree(source_dir, destination, dirs_exist_ok=True)
    _initialize_git_repo(destination)

    return DemoRepoInfo(
        repo_path=str(destination),
        task_input=(
            "Fix the demo repo bug: slugify_title should use hyphens instead of "
            "underscores, then run the local tests."
        ),
        test_command="python3 -m unittest discover -s tests -v",
        notes=(
            "Read demo_app/string_tools.py, fix the join character, and verify "
            "the unittest suite passes."
        ),
    )


def _initialize_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "demo@example.com"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Repo Task Demo"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial demo repo state"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
