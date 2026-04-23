import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / ".vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))


def _ensure_fastapi():
    try:
        from fastapi.testclient import TestClient  # noqa: F401
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest("fastapi is not installed") from exc


class DemoSmokeScriptTest(unittest.TestCase):
    def test_demo_smoke_script_runs_closed_loop(self):
        _ensure_fastapi()
        from scripts import run_demo_smoke

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = run_demo_smoke.main()

        self.assertEqual(0, exit_code)
        output = stdout.getvalue()
        self.assertIn("M3 demo smoke completed", output)
        payload = json.loads("\n".join(output.splitlines()[1:]))
        self.assertEqual("ok", payload["status"])
        self.assertEqual("approval_required", payload["first_loop_stop"])
        self.assertEqual("finished", payload["second_loop_stop"])
        self.assertEqual("edit", payload["approval_kind"])
        self.assertTrue(payload["latest_successful_test"])
        self.assertGreater(payload["latest_diff_chars"], 0)


if __name__ == "__main__":
    unittest.main()
