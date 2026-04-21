from __future__ import annotations

import difflib
import subprocess
from pathlib import Path


def build_unified_diff(relative_path: str, old_text: str, new_text: str) -> str:
    diff_lines = difflib.unified_diff(
        old_text.splitlines(True),
        new_text.splitlines(True),
        fromfile="a/{0}".format(relative_path),
        tofile="b/{0}".format(relative_path),
    )
    return "".join(diff_lines)


def repo_git_diff(repo_path: Path) -> str:
    diff_proc = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--relative"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )
    status_proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
    )

    parts = []
    if diff_proc.stdout.strip():
        parts.append(diff_proc.stdout)
    if status_proc.stdout.strip():
        parts.append("# git status --short\n{0}\n".format(status_proc.stdout.strip()))
    return "\n".join(parts).strip()
