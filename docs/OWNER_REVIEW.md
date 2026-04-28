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
- M10 dry run: a fresh checkout with `./.vendor` dependencies completed demo
  smoke and the full unittest suite at `95/95 OK`; no evidence triggered runtime
  hardening.
- M11 external review freeze: a clean checkout from the `m10-review-ready` tag
  completed demo smoke and `95/95 OK`; RightCode / `gpt-5.4-mini` spot check
  passed `6/6` in `auto_approve_edits`, stopped `0/6` at
  `edit_approval_required` in `stop_on_request`, and did not produce a stable
  control-plane failure. One sandbox DNS failure and one duplicate-read noise
  observation were recorded without opening runtime hardening.
- M12 external reviewer handoff: the stable reviewer entrypoint is the
  `m12-external-review-handoff` tag. The `m11-external-review-freeze` tag at
  `dc10b11` remains the runtime freeze reference point, not the handoff checkout
  target. Handoff is docs-only: reviewers should follow this pack, record
  friction, and only trigger runtime hardening when the same case and approval
  mode reproduce a taxonomy-backed control-plane failure.
- M13 reviewer feedback intake: an owner-simulated fresh checkout from
  `m12-external-review-handoff` installed `./.vendor`, completed demo smoke, and
  ran the full unittest suite at `95/95 OK`. No dependency, command-order,
  approval-semantics, baseline, or taxonomy friction triggered runtime
  hardening.

## M12 Reviewer Handoff

Use this path for external review:

1. Check out `m12-external-review-handoff`.
2. Read this Owner Review Pack before browsing implementation files.
3. Run the verification commands below.
4. Treat `stop_on_request` as an approval-gate rehearsal; `0/N` is expected when
   all cases stop at `edit_approval_required`.
5. Record friction before proposing fixes.

Friction log fields:

- Dependency setup: missing or unclear `./.vendor` installation step.
- Command order: unclear sequence between demo smoke, unittest, and real pilot.
- Approval semantics: confusion between expected approval stop and runtime
  failure.
- Baseline reading: confusion between local raw JSON, `BASELINE.md`, and
  reviewer-facing summaries.
- Failure taxonomy: unclear reason mapping or over-broad failure bucket.

Owner rule: documentation friction can only change README / Owner Review text.
Runtime hardening requires a stable same-case, same-approval-mode reproduction
with taxonomy pointing to `agent.py`, `session.py`, `context_bundle.py`, or
`eval_metrics.py`.

## M13 Feedback Intake

M13 is feedback intake, not feature work. In this local closeout, no external
human reviewer feedback was available, so the owner pass simulated the reviewer
path from the handoff tag and recorded only reproducibility evidence.

Observed result:

- Checkout target: `m12-external-review-handoff` at `bf7cf46`.
- Required local setup: `python3 -m pip install --target ./.vendor fastapi uvicorn pydantic httpx`.
- Demo command: `python3 scripts/run_demo_smoke.py` passed.
- Test command: `python3 -m unittest discover -s tests -v` passed at `95/95 OK`.
- Friction triage: no new dependency setup, command order, approval semantics,
  baseline reading, or failure taxonomy blocker was found.

Do not open runtime hardening from this M13 pass. The gate remains unchanged:
the same case and approval mode must reproduce a taxonomy-backed control-plane
failure before touching `agent.py`, `session.py`, `context_bundle.py`, or
`eval_metrics.py`.

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
