# Owner Review Pack

This is the five-minute review path for the Repo-Task Agent Runtime / Workbench.
It is intentionally small: the project proves a controlled repo-task loop, not a
general agent platform.

## Project Goal

Build a Python runtime that can execute small, real repository-local tasks with a
reviewable control plane.

The core loop is:

```text
task input -> plan mode -> todo lifecycle -> restricted tools -> approval -> diff -> local test -> event timeline
```

The project is not a Claude Code clone, chat shell, RAG system, MCP/plugin
platform, IDE extension, multi-agent team, memory system, or general command
runner.

## Architecture Boundary

- Python owns orchestration: `AgentRunner`, `TaskSession`, approval policy,
  context bundle, diff tracking, test execution, model/provider client, eval
  metrics, and demo/pilot scripts.
- The web console is a thin viewer over existing session state: plan/todo,
  approval kind, latest result, diff/test status, and timeline events.
- The tool surface stays deliberately narrow: read file, patch/write file, shell,
  and run test. Shell remains conservative and is not promoted into a generic
  command platform.
- Eval and pilot assets are evidence, not a benchmark product. Raw JSON stays
  local; only stable summaries belong in `artifacts/eval/BASELINE.md`.

## What Is Proven

- Demo smoke proves the local closed loop from task input through approval,
  patch, test, finish, and timeline.
- Real repo pilot currently covers six small repository-local cases with explicit
  target boundaries.
- `stop_on_request` is an approval-gate rehearsal mode. `0/6` finished is
  expected when all cases stop cleanly at `edit_approval_required`.
- Failure taxonomy is intentionally specific enough to separate approval,
  provider/transport, patch-contract, plan-output, read-focus, and verification
  failures.

## Current Evidence

- M6.1 real repo pilot baseline: RightCode / `gpt-5.4-mini`,
  `auto_approve_edits = 6/6`, average duplicate reads `0.0`.
- M6.1 `stop_on_request`: `0/6` finished, all cases expectedly stopped at
  `edit_approval_required`; a one-off duplicate-read noise case returned to
  `0.0` in focused rerun.
- M7 rehearsal: one README checkpoint case showed non-stable model/patch noise,
  then recovered in focused rerun. Owner decision: no runtime hardening without
  stable reproduction.
- M8 fresh checkout review: tests can pass in a clean checkout; demo smoke needs
  API dependencies installed into `./.vendor`.
- M9 final rehearsal: RightCode / `gpt-5.4-mini` real repo pilot passed `6/6`
  in `auto_approve_edits`; `stop_on_request` stopped `0/6` at
  `edit_approval_required` with average duplicate reads `0.0`, as expected.

## Verification Commands

```bash
python3 -m pip install --target ./.vendor fastapi uvicorn pydantic httpx
python3 scripts/run_demo_smoke.py
python3 -m unittest discover -s tests -v
```

When a RightCode-compatible local token is configured:

```bash
python3 scripts/run_real_repo_pilot.py
python3 scripts/run_real_repo_pilot.py --approval-mode stop_on_request
```

## Owner Review Checklist

- Do not claim the runtime supports open-ended repository discovery; current
  tasks must provide clear target boundaries.
- Do not treat `stop_on_request` `0/N` as product failure when the stop reason is
  `edit_approval_required`.
- Do not present six pilot cases as broad benchmark coverage. They are a small
  evidence pack for the controlled loop.
- Do not add tools, directory browsing, RAG, memory, MCP/plugins, multi-agent
  orchestration, worktree management, or complex UI without a stable failure
  that proves the need.
- Only open runtime hardening when the same failure is reproducible for the same
  case and approval mode, and the taxonomy points to a control-plane gap.

## Risk Register

- Provider and transport failures can still appear as model-layer instability;
  classify them before changing runtime behavior.
- Model output can be invalid or over-specific; repair/retry remains bounded to
  avoid hiding bad decisions.
- Patch contracts intentionally block uncertain edits before approval; a blocked
  patch is safer than wasting approval on an untrusted diff.
- Current real repo pilot coverage is intentionally local and narrow; that is a
  scope decision, not a missing platform feature.
