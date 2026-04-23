from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from .eval_types import EvalCase
from .git_repo import initialize_git_repo


def get_eval_template_root() -> Path:
    return Path(__file__).resolve().parent.parent / "examples" / "eval_repo_templates"


def builtin_eval_cases() -> List[EvalCase]:
    return [
        EvalCase(
            case_id="slug_join",
            display_name="Slug Join Character",
            template_dir_name="slug_join",
            task_input=(
                "Fix slugify_title so it uses hyphens instead of underscores, "
                "then run the local tests."
            ),
            test_command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v"),
            notes="Read demo_app/string_tools.py, patch the join character, and verify tests.",
        ),
        EvalCase(
            case_id="clamp_lower_bound",
            display_name="Clamp Lower Bound",
            template_dir_name="clamp_lower_bound",
            task_input=(
                "Fix clamp so values below the lower bound return the lower bound, "
                "then run the local tests."
            ),
            test_command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v"),
            notes="Read demo_app/number_tools.py, patch the lower-bound branch, and verify tests.",
        ),
        EvalCase(
            case_id="compact_whitespace",
            display_name="Compact Whitespace",
            template_dir_name="compact_whitespace",
            task_input=(
                "Fix compact_whitespace so it trims edges and collapses all whitespace, "
                "then run the local tests."
            ),
            test_command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v"),
            notes="Read demo_app/text_tools.py, patch the whitespace splitting logic, and verify tests.",
        ),
        EvalCase(
            case_id="implementation_only_change",
            display_name="Implementation Only Change",
            template_dir_name="implementation_only_change",
            task_input=(
                "Fix format_status_label so multi-word statuses use hyphens, "
                "do not modify tests, then run the local tests."
            ),
            test_command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v"),
            notes=(
                "Read demo_app/status_tools.py, patch only the implementation, "
                "and verify tests without editing tests."
            ),
        ),
        EvalCase(
            case_id="failing_test_points_to_source",
            display_name="Failing Test Points To Source",
            template_dir_name="failing_test_points_to_source",
            task_input=(
                "Use the failing discount test as the clue, fix apply_discount_cents "
                "in the source implementation, then run the local tests."
            ),
            test_command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v"),
            notes=(
                "Read tests/test_discounts.py first, then demo_app/discounts.py, "
                "patch only the source implementation, and verify tests."
            ),
        ),
        EvalCase(
            case_id="multi_file_context_single_edit",
            display_name="Multi File Context Single Edit",
            template_dir_name="multi_file_context_single_edit",
            task_input=(
                "Render messages with the shared DEFAULT_SUFFIX. You may read both "
                "format_rules.py and message_tools.py, but edit only message_tools.py, "
                "then run the local tests."
            ),
            test_command=("python3", "-m", "unittest", "discover", "-s", "tests", "-v"),
            notes=(
                "Read demo_app/format_rules.py and demo_app/message_tools.py, "
                "patch only demo_app/message_tools.py, and verify tests."
            ),
        ),
    ]


def get_builtin_eval_case(case_id: str) -> EvalCase:
    for case in builtin_eval_cases():
        if case.case_id == case_id:
            return case
    raise KeyError("Unknown eval case id: {0}".format(case_id))


def create_eval_repo(case: EvalCase, target_dir: Optional[Path] = None) -> Path:
    source_dir = case.template_dir
    if not source_dir.exists():
        raise FileNotFoundError(
            "Eval case template is missing: {0}".format(source_dir)
        )

    if target_dir is None:
        destination = Path(
            tempfile.mkdtemp(prefix="repo-task-eval-{0}-".format(case.case_id))
        )
    else:
        destination = Path(target_dir).resolve()
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Target eval repo directory must be empty.")
        destination.mkdir(parents=True, exist_ok=True)

    shutil.copytree(source_dir, destination, dirs_exist_ok=True)
    initialize_git_repo(
        destination,
        user_email="eval@example.com",
        user_name="Repo Task Eval",
        initial_commit_message="Initial eval repo state",
    )
    return destination
