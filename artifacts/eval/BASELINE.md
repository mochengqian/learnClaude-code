# Eval Baseline Summary

This file is the only eval artifact that should be committed.

Rules:
- Keep raw eval runs local in `artifacts/eval/*.json`.
- Update this file at each checkpoint instead of committing raw JSON.
- Record only stable fields that are useful for regression tracking and review.

## Summary Format

Each checkpoint update should use this structure:

```markdown
## <checkpoint-name> (<YYYY-MM-DD>)

- Commit: `<git-sha>`
- Model: `<provider> / <model>`
- Eval command source: `artifacts/eval/<local-json-name>.json`
- Notes: `<one-line interpretation>`

### auto_approve_edits

- Passed: `<passed>/<total>`
- Average steps: `<value>`
- Average duplicate reads: `<value>`
- Cases with same-file rereads: `<value>`
- Failure reasons: `<json-like summary>`

Case outcomes:
- `<case_id>`: `PASS/FAIL`, stop=`<stop_reason>`, failure=`<failure_reason or ->`

### stop_on_request

- Passed: `<passed>/<total>`
- Average steps: `<value>`
- Average duplicate reads: `<value>`
- Cases with same-file rereads: `<value>`
- Failure reasons: `<json-like summary>`

Case outcomes:
- `<case_id>`: `PASS/FAIL`, stop=`<stop_reason>`, failure=`<failure_reason or ->`
```

## M1 Checkpoint (2026-04-22)

- Commit: `68a030e`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-stop_on_request.json`
- Notes: `M1 runtime loop and control-plane hardening are in place; the remaining auto-approve gap is a missing_repo_file path selection failure.`

### auto_approve_edits

- Passed: `2/3`
- Average steps: `5.0`
- Average duplicate reads: `0.33`
- Cases with same-file rereads: `1`
- Failure reasons: `{"missing_repo_file": 1}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `FAIL`, stop=`runner_failed`, failure=`missing_repo_file`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `4.33`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"approval_required": 3}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`approval_required`

## Transport Retry Checkpoint (2026-04-22)

- Commit: `8188058`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-stop_on_request.json`
- Notes: `Bounded transport retry/classify landed in the checkpoint. auto_approve_edits recovered to 3/3, and stop_on_request still halts exactly at approval_required as intended.`

### auto_approve_edits

- Passed: `3/3`
- Average steps: `6.0`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `4.33`
- Average duplicate reads: `0.33`
- Cases with same-file rereads: `1`
- Failure reasons: `{"approval_required": 3}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`approval_required`

## Read Focus Hardening Checkpoint (2026-04-22)

- Commit: `bf43680`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-stop_on_request.json`
- Notes: `Read-focus hardening removed duplicate rereads in both approval modes. The remaining failures moved to non-reread issues: one bad patch path choice under auto_approve_edits and one edit_without_read miss under stop_on_request.`

### auto_approve_edits

- Passed: `2/3`
- Average steps: `6.0`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"tool_failed": 1}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `FAIL`, stop=`tool_failed`, failure=`tool_failed`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `2.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"approval_required": 2, "edit_without_read": 1}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`runner_failed`, failure=`edit_without_read`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`approval_required`

## Edit Target Binding Checkpoint (2026-04-23)

- Commit: `16f2611`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-stop_on_request.json`
- Notes: `Edit-target binding hardening restored auto_approve_edits to 3/3 while keeping duplicate rereads at 0.0. Failure taxonomy now splits off_target_edit and bad_patch_target instead of collapsing these regressions into tool_failed.`

### auto_approve_edits

- Passed: `3/3`
- Average steps: `5.33`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `2.33`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"approval_required": 3}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`approval_required`

## Approval Path Hardening Checkpoint (2026-04-23)

- Commit: `d292542`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-stop_on_request.json`
- Notes: `Approval-path hardening kept auto_approve_edits at 3/3 and removed low-value shell approval noise from stop_on_request. Approval failures now land as edit_approval_required instead of a single generic approval_required bucket.`

### auto_approve_edits

- Passed: `3/3`
- Average steps: `5.33`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `3.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"edit_approval_required": 3}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`

## Approval Timeline Explainability Checkpoint (2026-04-23)

- Commit: `d2cd709`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-stop_on_request.json`
- Notes: `Approval kind is now structured directly into ApprovalRequest, ToolExecutionResult, and approval timeline events. Runtime explainability improved without regressing auto_approve_edits or duplicate-read behavior.`

### auto_approve_edits

- Passed: `3/3`
- Average steps: `5.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `4.0`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"edit_approval_required": 3}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`

## M3 Start Baseline Refresh (2026-04-23)

- Commit: `72dc057`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-start-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-start-stop_on_request.json`
- Notes: `M3 starts with a live regression signal: read-focus remains stable at 0.0 duplicate reads, while auto_approve_edits regressed on edit-target and output-contract failures that should drive the next control-plane hardening slice.`

### auto_approve_edits

- Passed: `1/3`
- Average steps: `2.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"off_target_edit": 1, "invalid_model_output": 1}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`tool_failed`, failure=`off_target_edit`
- `clamp_lower_bound`: `FAIL`, stop=`runner_failed`, failure=`invalid_model_output`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `3.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"edit_approval_required": 3}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`

## M3 Patch Contract Hardening Checkpoint (2026-04-23)

- Commit: `76ee48c`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-patch-contract-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-patch-contract-stop_on_request.json`
- Notes: `No-op file_patch is now blocked at the agent output contract and gets one bounded repair. auto_approve_edits recovered to 3/3 while duplicate reads stayed at 0.0.`

### auto_approve_edits

- Passed: `3/3`
- Average steps: `5.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/3`
- Average steps: `3.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"edit_approval_required": 3}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`

## M3 Eval Expansion Checkpoint (2026-04-23)

- Commit: `7390d8d`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-eval-expansion-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-eval-expansion-stop_on_request.json`
- Notes: `The eval pack now covers 6 repo-local cases. Read-focus stayed stable with 0.0 duplicate reads. The only auto_approve_edits miss was a plan-stage invalid JSON on multi_file_context_single_edit, so the next control-plane gap is plan output repair rather than product expansion.`

### auto_approve_edits

- Passed: `5/6`
- Average steps: `4.67`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"invalid_model_output": 1}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`
- `implementation_only_change`: `PASS`, stop=`finished`, failure=`-`
- `failing_test_points_to_source`: `PASS`, stop=`finished`, failure=`-`
- `multi_file_context_single_edit`: `FAIL`, stop=`runner_failed`, failure=`invalid_model_output`

### stop_on_request

- Passed: `0/6`
- Average steps: `3.33`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"edit_approval_required": 6}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `implementation_only_change`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `failing_test_points_to_source`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `multi_file_context_single_edit`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`

## M3 Plan Output Hardening Checkpoint (2026-04-23)

- Commit: `fd93ea9`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-plan-output-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-m3-plan-output-stop_on_request.json`
- Notes: `Plan-stage invalid JSON/todo output now gets one bounded repair with timeline events and plan_invalid_output taxonomy. auto_approve_edits recovered to 6/6 while duplicate reads stayed at 0.0.`

### auto_approve_edits

- Passed: `6/6`
- Average steps: `5.17`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`
- `implementation_only_change`: `PASS`, stop=`finished`, failure=`-`
- `failing_test_points_to_source`: `PASS`, stop=`finished`, failure=`-`
- `multi_file_context_single_edit`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/6`
- Average steps: `3.0`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"edit_approval_required": 6}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `implementation_only_change`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `failing_test_points_to_source`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `multi_file_context_single_edit`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`

## M4 Provider Stability Checkpoint (2026-04-23)

- Commit: `fa64829`
- Model: `RightCode / gpt-5.4-mini`
- Eval command source:
  - `artifacts/eval/rightcode-gpt-5.4-mini-m4-provider-stability-auto_approve_edits.json`
  - `artifacts/eval/rightcode-gpt-5.4-mini-m4-provider-stability-stop_on_request.json`
- Notes: `Provider/transport hardening stayed invisible to the control plane under real-model validation. auto_approve_edits remained 6/6, and stop_on_request halted cleanly at edit approval with duplicate reads still pinned at 0.0.`

### auto_approve_edits

- Passed: `6/6`
- Average steps: `5.5`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{}`

Case outcomes:
- `slug_join`: `PASS`, stop=`finished`, failure=`-`
- `clamp_lower_bound`: `PASS`, stop=`finished`, failure=`-`
- `compact_whitespace`: `PASS`, stop=`finished`, failure=`-`
- `implementation_only_change`: `PASS`, stop=`finished`, failure=`-`
- `failing_test_points_to_source`: `PASS`, stop=`finished`, failure=`-`
- `multi_file_context_single_edit`: `PASS`, stop=`finished`, failure=`-`

### stop_on_request

- Passed: `0/6`
- Average steps: `3.17`
- Average duplicate reads: `0.0`
- Cases with same-file rereads: `0`
- Failure reasons: `{"edit_approval_required": 6}`

Case outcomes:
- `slug_join`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `clamp_lower_bound`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `compact_whitespace`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `implementation_only_change`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `failing_test_points_to_source`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
- `multi_file_context_single_edit`: `FAIL`, stop=`approval_required`, failure=`edit_approval_required`
