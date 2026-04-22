from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from repo_task_runtime import AgentRunner, create_model_client_from_env
from repo_task_runtime.eval_pack import (
    APPROVAL_MODE_AUTO_APPROVE_EDITS,
    EvalRunner,
    builtin_eval_cases,
    get_builtin_eval_case,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the built-in repo-task eval pack.")
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run one or more specific case ids. Defaults to all built-in cases.",
    )
    parser.add_argument(
        "--approval-mode",
        choices=("auto_approve_edits", "stop_on_request"),
        default=APPROVAL_MODE_AUTO_APPROVE_EDITS,
        help="How to handle edit approvals during eval execution.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Override the per-case max step limit.",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="List the built-in eval cases and exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the eval suite report as JSON.",
    )
    parser.add_argument(
        "--output-json",
        help="Write the eval suite report JSON to a file. Parent directories are created automatically.",
    )
    args = parser.parse_args()

    cases = builtin_eval_cases()
    if args.case_ids:
        cases = [get_builtin_eval_case(case_id) for case_id in args.case_ids]

    if args.list_cases:
        for case in cases:
            print("{0}: {1}".format(case.case_id, case.task_input))
        return 0

    model_client = create_model_client_from_env()
    if model_client is None:
        print(
            "Model client is not configured. Set REPO_TASK_MODEL_BASE_URL, "
            "REPO_TASK_MODEL_API_KEY, and REPO_TASK_MODEL_NAME.",
            file=sys.stderr,
        )
        return 2

    runner = EvalRunner(
        agent_runner=AgentRunner(model_client),
        approval_mode=args.approval_mode,
        max_steps_override=args.max_steps,
    )
    report = runner.run_cases(cases)
    report_payload = report.to_dict()

    if args.json:
        print(json.dumps(report_payload, indent=2, ensure_ascii=False))

    if args.output_json:
        output_path = Path(args.output_json).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("Wrote eval report to {0}".format(output_path))

    if args.json:
        return 0

    print(
        "Eval suite completed: {0}/{1} passed, avg_steps={2}, avg_reads={3}, "
        "avg_duplicate_reads={4}, reread_cases={5}, approval_mode={6}".format(
            report.passed_cases,
            len(report.case_reports),
            report.average_steps,
            report.context_bundle_metrics.average_read_file_calls,
            report.context_bundle_metrics.average_duplicate_read_file_calls,
            report.context_bundle_metrics.cases_with_same_file_rereads,
            report.approval_mode,
        )
    )
    for case in report.case_reports:
        status = "PASS" if case.success else "FAIL"
        reread_value = "no"
        if case.context_bundle_metrics.same_file_reread_detected:
            reread_value = "yes:{0}".format(
                ",".join(case.context_bundle_metrics.same_file_reread_paths)
            )
        print(
            "- {0} [{1}] steps={2}/{3} reads={4} dup_reads={5} reread={6} "
            "stop={7} failure={8} verify={9}".format(
                case.case_id,
                status,
                case.steps_completed,
                case.max_steps,
                case.context_bundle_metrics.read_file_calls,
                case.context_bundle_metrics.duplicate_read_file_calls,
                reread_value,
                case.stop_reason,
                case.failure_reason or "-",
                case.verification_exit_code,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
