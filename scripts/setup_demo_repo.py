import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repo_task_runtime.demo_repo import create_demo_repo


def main() -> None:
    info = create_demo_repo()
    print("Demo repo created")
    print("Path:", info.repo_path)
    print("Task:", info.task_input)
    print("Test command:", info.test_command)
    print("Notes:", info.notes)


if __name__ == "__main__":
    main()
