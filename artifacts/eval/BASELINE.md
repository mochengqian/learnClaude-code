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
