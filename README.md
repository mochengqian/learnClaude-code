# Repo-Task Agent Runtime / Workbench

Reviewer quick path: [Owner Review Pack](docs/OWNER_REVIEW.md).

这个仓库当前只做一件事：用 Python 搭一个面向真实代码仓库局部任务的最小 runtime。

它不是：
- Claude Code 仿品
- 通用 AI Agent 平台
- 聊天壳
- RAG 系统
- 多智能体 team system
- 花哨前端

## 当前交付状态

当前项目已进入 delivery / review-ready 状态，交付范围仍然只围绕后端核心闭环：

`task input -> plan mode -> todo lifecycle -> restricted tools -> approval -> diff -> local test -> event timeline`

当前交付边界明确不做：

- 子代理 / worktree 隔离
- MCP / plugin / skill 系统
- 长期记忆
- 自动恢复 / 回放存储

## 为什么这样切

从 `Claude Code` 参考源码里，这一轮只迁移四个高价值机制，不迁移产品壳：

1. `plan mode` 本质上是权限模式，不是“写一份 plan 文档”这么简单。
2. `todo lifecycle` 是 runtime 状态，不是随手输出的文本列表。
3. `approval` 应该是规则驱动，而不是前端弹窗驱动。
4. `diff` 和 `event timeline` 要成为一等输出，方便后续接 Web 控制台。

## 目录结构

```text
repo_task_runtime/
  __init__.py
  approval.py
  agent.py
  context_bundle.py
  diffing.py
  demo_repo.py
  eval_cases.py
  eval_metrics.py
  eval_runner.py
  eval_pack.py
  eval_types.py
  git_repo.py
  model_client.py
  models.py
  session.py
  workbench.py
  web/
    app.js
    index.html
    styles.css
examples/
  demo_repo_template/
  eval_repo_templates/
scripts/
  run_eval.py
  setup_demo_repo.py
tests/
  test_agent.py
  test_context_bundle.py
  test_runtime.py
  test_api.py
  test_demo_flow.py
  test_eval_pack.py
  test_web_console.py
```

## 核心接口

```python
from pathlib import Path

from repo_task_runtime import (
    FilePatchRequest,
    TaskWorkbench,
    TestCommandRequest,
    TodoItem,
    TodoStatus,
)

workbench = TaskWorkbench()
session = workbench.create_session(Path("/path/to/repo"))

session.begin_task("Fix a failing test in the parser module")
session.update_plan(
    "1. Inspect the parser.\n"
    "2. Make the smallest safe code change.\n"
    "3. Run local tests.\n"
)
session.approve_plan()

session.replace_todos(
    [
        TodoItem(content="Inspect parser failure", status=TodoStatus.IN_PROGRESS),
        TodoItem(content="Patch parser edge case", status=TodoStatus.PENDING),
        TodoItem(content="Run local tests", status=TodoStatus.PENDING),
    ]
)

pending = session.request_tool(
    FilePatchRequest(
        relative_path="parser.py",
        expected_old_snippet="return token.value\n",
        new_snippet="return token.value.strip()\n",
    )
)
session.resolve_approval(pending.approval_id, approve=True)

test_result = session.request_tool(
    TestCommandRequest(command=("python3", "-m", "unittest", "discover", "-s", "tests"))
)

snapshot = session.snapshot()
```

## 状态边界

### `TaskSession`

负责单次 repo task 的全部运行时状态：

- 任务输入
- 当前权限模式
- 当前 plan
- 当前 todo 列表
- 待审批操作
- 最新 diff
- 最新 tool result
- 事件时间线

### `ApprovalPolicy`

当前是一个保守的本地策略层：

- `plan` 模式下拒绝所有变更型工具
- 读文件直接放行
- 写文件默认要求审批
- 常见危险命令直接拒绝
- 测试命令走独立 allowlist

### `ToolRequest`

首版只保留 5 类请求：

- `FileReadRequest`
- `FilePatchRequest`
- `WriteFileRequest`
- `ShellCommandRequest`
- `TestCommandRequest`

## 测试方案

当前测试覆盖：

- `plan mode` 对变更工具的阻断
- `todo lifecycle` 状态机约束
- `file_patch` / 写文件审批流
- 本地测试命令执行
- diff 与 timeline 更新

运行方式：

```bash
python3 -m unittest discover -s tests -v
```

## 当前风险

1. 会话状态目前是内存态，服务重启后不会恢复。
2. Shell 审批策略是保守 allowlist，真实项目里还需要更细粒度的路径和前缀规则。
3. `latest_diff` 对 shell 侧产生的新文件主要依赖 Git 视图，后续需要补更完整的工作区快照机制。

## 回退方案

如果这条线做重了，最先删减的不是状态机，而是外围层：

1. 不做 Web 控制台，先只保留 Python service。
2. 不做自动 plan 生成，先由人工或假模型写 plan。
3. 不做通用 shell，先只保留文件读写和测试工具。

## `file_patch` 原语

这一轮新增了一个最小 `file_patch`：

- 只支持单文件字符串替换
- 需要 `relative_path`
- 需要 `expected_old_snippet`
- 需要 `new_snippet`
- 可选 `replace_all`

约束故意保持保守：

- 默认要求 `expected_old_snippet` 只匹配一次
- 如果匹配不到，会直接失败
- 如果匹配到多处且 `replace_all=false`，会直接失败
- 仍然走 approval、diff、timeline

这一步的目标不是做通用 patch 引擎，而是先把“局部编辑原语”补齐，替换掉 demo 里原先的整文件覆盖。

下一步最合理的扩展不是“加功能”，而是继续围绕这套 runtime 做一个很薄的可演示壳。

## API 层

这一轮新增了一个很薄的 FastAPI adapter：

- [repo_task_runtime/api.py](/Users/luan/claude-code-main/learnClaude-code/repo_task_runtime/api.py:1)

它只做 HTTP 映射，不承载业务状态机。核心状态仍在 `TaskSession`。

当前路由：

- `GET /`
- `GET /healthz`
- `POST /demo/setup`
- `POST /sessions`
- `POST /sessions/{session_id}/task`
- `POST /sessions/{session_id}/plan`
- `POST /sessions/{session_id}/plan/approve`
- `POST /sessions/{session_id}/agent/plan`
- `POST /sessions/{session_id}/agent/step`
- `POST /sessions/{session_id}/agent/loop`
- `PUT /sessions/{session_id}/todos`
- `POST /sessions/{session_id}/tools`
- `POST /sessions/{session_id}/approvals/{approval_id}/resolve`
- `GET /sessions/{session_id}`

如果本地已安装依赖，可直接启动：

```bash
uvicorn repo_task_runtime.api:app --reload
```

如果我把依赖安装在仓库根目录的 `./.vendor`，`api.py` 会自动把它加入 `sys.path`，避免污染全局环境。

建议的本地依赖：

```bash
python3 -m pip install --target ./.vendor fastapi uvicorn pydantic httpx
```

## Eval Pack

这一轮新增了一个固定内置的 `eval pack`，目标不是做 benchmark 平台，而是给 runtime 提供一组稳定、可重复、可量化的回归任务。

当前内置 6 个 case：

- `slug_join`
- `clamp_lower_bound`
- `compact_whitespace`
- `implementation_only_change`
- `failing_test_points_to_source`
- `multi_file_context_single_edit`

每个 case 都会：

1. 从模板生成一个临时 Git repo
2. 创建一个新的 `TaskSession`
3. 让 agent 起 plan
4. 跑有限步数的 repo-task loop
5. 最后强制再跑一次本地测试做验证闭环

核心实现：

- [repo_task_runtime/eval_pack.py](/Users/luan/claude-code-main/learnClaude-code/repo_task_runtime/eval_pack.py:1)
- [scripts/run_eval.py](/Users/luan/claude-code-main/learnClaude-code/scripts/run_eval.py:1)

先列出内置 case：

```bash
python3 scripts/run_eval.py --list-cases
```

使用当前模型配置跑完整 eval pack：

```bash
export REPO_TASK_MODEL_BASE_URL="https://right.codes/codex/v1"
export REPO_TASK_MODEL_API_KEY="..."
export REPO_TASK_MODEL_NAME="gpt-5.4-mini"
python3 scripts/run_eval.py --approval-mode auto_approve_edits
```

如果你想观察 agent 在真实 approval 边界上的停机原因，而不是自动放行编辑：

```bash
python3 scripts/run_eval.py --approval-mode stop_on_request
```

也可以只跑某个 case：

```bash
python3 scripts/run_eval.py --case slug_join --case clamp_lower_bound
```

如果你要把结果沉淀成后续可对比的本地 artifact：

```bash
python3 scripts/run_eval.py \
  --approval-mode auto_approve_edits \
  --output-json artifacts/eval/rightcode-gpt-5.4-mini-auto_approve_edits.json
```

`run_eval.py` 会自动创建父目录，适合后续把不同模型、不同 approval 模式的基线结果都落成一组稳定 JSON。

但从仓库治理角度，`artifacts/eval/*.json` 只作为本地运行产物保留，不纳入 git。
每次 checkpoint 只更新一份可提交的基线摘要：

- [artifacts/eval/BASELINE.md](/Users/luan/claude-code-main/learnClaude-code/artifacts/eval/BASELINE.md:1)

这份摘要只记录稳定字段，例如：

- checkpoint / commit / model
- 每种 approval mode 的通过率
- 平均步数
- 平均重复 `read_file`
- 同文件复读 case 数
- 聚合后的 `failure_reason_counts`
- 每个 case 的稳定结论

当前输出除了通过率和失败原因，还开始补上 `context bundle` 的效果代理指标：

- 通过 / 失败数量
- 平均步数
- 平均 `read_file` 次数
- 平均重复 `read_file` 次数
- 有多少 case 还在复读同一文件
- 每个 case 的 `stop_reason`
- 聚合后的 `failure_reason_counts`

每个 case 现在也会额外记录：

- `read_file_calls`
- `duplicate_read_file_calls`
- `same_file_reread_detected`
- `same_file_reread_paths`

失败原因当前会归并成最小集合：

- `approval_required`
- `edit_approval_required`
- `shell_approval_required`
- `test_approval_required`
- `bad_patch_snippet`
- `directory_path`
- `edit_without_read`
- `failed_test_context_missing`
- `invalid_finish`
- `invalid_model_output`
- `missing_relative_path`
- `missing_repo_file`
- `model_provider_response_invalid`
- `model_transport_failed`
- `no_op_patch`
- `off_target_edit`
- `plan_invalid_output`
- `readme_reread`
- `same_file_reread`
- `shell_tool_misuse`
- `tool_failed`
- `tool_blocked`
- `max_steps_reached`
- `verification_failed`
- `runner_failed`

## Context Bundle

这一轮把 agent 每步拿到的 prompt 上下文，从“临时拼一个 snapshot”收敛成了一个最小 `context bundle`：

- [repo_task_runtime/context_bundle.py](/Users/luan/claude-code-main/learnClaude-code/repo_task_runtime/context_bundle.py:1)

边界仍然很克制，只包含：

- 当前 task input / plan / todos / approvals
- `latest_tool_result`
- `latest_diff`
- `recent_timeline`
- `recent_file_contexts`
- `recent_test_failures`

`recent_file_contexts` 有两个关键约束：

- 最近读过的文件会进入 bundle
- 最近写过或 patch 过的文件，也会刷新进 bundle，避免模型继续拿旧内容做判断

`recent_test_failures` 也保持最小语义：

- 测试失败时保留最近失败摘要
- 测试成功后清空旧失败，避免把过期报错继续塞给模型

agent 接入点仍然只有一处：

- [repo_task_runtime/agent.py](/Users/luan/claude-code-main/learnClaude-code/repo_task_runtime/agent.py:68)

验证这条线的测试：

- [tests/test_context_bundle.py](/Users/luan/claude-code-main/learnClaude-code/tests/test_context_bundle.py:1)

## 模型接入

这一轮只接一个最小的 `OpenAI-compatible` 模型入口：

- [repo_task_runtime/model_client.py](/Users/luan/claude-code-main/learnClaude-code/repo_task_runtime/model_client.py:1)
- [repo_task_runtime/agent.py](/Users/luan/claude-code-main/learnClaude-code/repo_task_runtime/agent.py:1)

边界仍然严格：

- 只支持单模型
- 只支持同步请求
- 只做 `plan` 生成和“下一步 tool 决策”
- 不做多模型路由
- 不做自动 fallback
- 不读取 `cc switch` sqlite 作为产品依赖

默认环境变量：

```bash
export REPO_TASK_MODEL_BASE_URL="https://right.codes/codex/v1"
export REPO_TASK_MODEL_API_KEY="..."
export REPO_TASK_MODEL_NAME="gpt-5.4-mini"
```

如果你已经设置了 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL`，也可以直接复用。

模型侧现在还带一个很小的 todo 自动推进规则：

- `agent/step` 或 `agent/loop` 的单步结果如果是 `executed`，当前 `in_progress` 会变成 `completed`，下一个 `pending` 会变成 `in_progress`
- 如果结果是 `finish`，只会完成当前 `in_progress`
- 如果结果是 `approval_required / denied / failed / rejected`，todo 不会自动推进

## Web 控制台

这一轮新增了一个纯静态控制台，由 FastAPI 直接提供：

- `GET /` 返回控制台页面
- `/assets/*` 提供样式和浏览器脚本

控制台严格只保留这几块：

- session snapshot
- approval queue
- diff / latest tool result
- event timeline

为了能真的操作 runtime，我加了几个很薄的控制表单：

- create session
- draft plan with model
- save / approve plan
- replace todos
- run agent step
- run agent loop
- request tool

没有聊天区，没有前端构建链。

## Demo Repo

仓库内提供了一个可复制的 demo repo 模板：

- [examples/demo_repo_template/README.md](/Users/luan/claude-code-main/learnClaude-code/examples/demo_repo_template/README.md:1)

它故意带着一个很小但真实的 bug：

- `demo_app/string_tools.py` 里 `slugify_title()` 用了下划线连接词片段
- 测试期望值要求使用连字符

你可以用下面的方式创建一个真正可运行、带 Git 初始提交的 demo repo：

```bash
python3 scripts/setup_demo_repo.py
```

或者直接调用 API：

```bash
curl -X POST http://127.0.0.1:8000/demo/setup
```

## 端到端演示路径

建议的演示路径已经固定：

1. 点击或调用 `demo/setup` 创建 demo repo。
2. 创建 session，任务输入使用 demo 返回的 task brief。
3. 用 `agent/plan` 或控制台里的 `Draft Plan With Model` 生成 plan 和初始 todos。
4. approve plan。
5. 用 `agent/step` 先读文件或先跑测试。
6. 也可以改用 `agent/loop`，让 agent 在限步内连续推进。
7. `agent/loop` 一旦遇到 `approval_required / denied / failed / finish / max_steps` 就会停下。
8. 如果 agent 走到 `file_patch` 或 `write_file`，它仍然会进入 approval queue。
9. approve once 后，再跑下一步 agent step、agent loop 或手工触发 `run_test`。
10. 确认测试通过。
11. 在控制台查看 latest diff、latest tool result 和 event timeline。

## M2 Readiness Note

M2 的收口目标是证明“真实 repo 局部任务闭环可演示、可解释、可回归”，不是继续长成平台。

当前可演示路径：

- demo repo setup -> session/task input -> plan draft/approve -> todo lifecycle -> restricted tools
- read/test -> edit approval -> approve -> diff -> successful local test -> event timeline
- Web 控制台只消费现有 session snapshot 和 timeline 字段，不承载新的 runtime 逻辑

已完成能力：

- plan mode、todo 状态、approval kind、diff/test evidence、timeline summary 都已经在薄控制台可见
- agent loop 只做单 agent 限步推进，并带最小 todo sync、output repair/retry、path/read/edit/finish guardrails
- eval 治理保持克制：`artifacts/eval/*.json` 是本地产物，只提交 `artifacts/eval/BASELINE.md` 摘要

明确不做：

- 不做持久化 / 数据库
- 不做 worktree / 子代理
- 不做 MCP / plugin / 记忆系统
- 不新增工具类型
- 不把 shell 扩成通用命令平台
- 不做复杂前端或产品化面板

M2 closeout smoke 命令：

```bash
node --check repo_task_runtime/web/app.js
python3 -m unittest tests.test_demo_flow -v
python3 -m unittest tests.test_api -v
python3 -m unittest tests.test_agent -v
python3 -m unittest tests.test_web_console -v
python3 -m unittest discover -s tests -v
```

## M3 Real Repo Robustness

M3 的目标不是新增产品功能，而是把真实 repo 局部任务的稳定性继续压实。当前只接受控制面和验证面的增强。

M3.0 baseline refresh 使用 RightCode / `gpt-5.4-mini` 在当前远端锚点重新跑两档 approval mode；raw JSON 继续只保留在本地 `artifacts/eval/*.json`，提交态只更新 [artifacts/eval/BASELINE.md](/Users/luan/claude-code-main/learnClaude-code/artifacts/eval/BASELINE.md:1)。

M3.1 demo smoke 提供一条本地命令，证明 demo repo、API session flow、agent plan/loop、approval、diff、successful test 和 timeline 能端到端跑通：

```bash
python3 scripts/run_demo_smoke.py
```

M3.2 patch-contract hardening 把 no-op `file_patch` 前移到 agent 输出 contract 校验，并给一次受限 repair，避免没有 diff 的编辑请求进入 approval/execute 后才失败。

M3.3 eval case 小扩容不是功能开发，只新增 3 个更贴近真实 repo 局部任务的内置 eval case：

- `implementation_only_change`：要求只改实现，不改测试。
- `failing_test_points_to_source`：先读失败测试，再读 source，最终只改 source。
- `multi_file_context_single_edit`：允许读 2 个文件，但只编辑 1 个目标文件。

M3.4 plan output hardening 只补 plan 阶段控制面韧性：

- `draft_plan` 对非法 JSON / 非法 todo 输出给一次受限 repair。
- timeline 显式记录 `agent_plan_output_invalid`、`agent_plan_output_retry_requested`、`agent_plan_output_repaired`。
- eval taxonomy 把 plan 阶段失败拆成 `plan_invalid_output`，不再混进通用 `invalid_model_output`。
- RightCode / `gpt-5.4-mini` 扩展基线恢复到 `auto_approve_edits = 6/6`，`avg_duplicate_reads = 0.0`。

M3 closeout 口径：

- 当前远端 checkpoint：`3e138e7`。
- 当前实现锚点：`fd93ea9`。
- 可演示闭环：demo repo -> session/task input -> plan draft/approve -> limited agent loop -> approval -> diff -> successful local test -> timeline。
- 基线治理：raw JSON 继续只落本地 `artifacts/eval/*.json`，提交态只更新 [artifacts/eval/BASELINE.md](/Users/luan/claude-code-main/learnClaude-code/artifacts/eval/BASELINE.md:1)。

M3 closeout 验证命令：

```bash
python3 scripts/run_demo_smoke.py
python3 -m unittest discover -s tests -v
```

M3 继续明确不做：

- 不做持久化 / 数据库
- 不做 worktree / 子代理
- 不做 MCP / plugin / 记忆系统
- 不新增工具类型
- 不把 shell 扩成通用命令平台
- 不做更复杂前端或产品化面板

## M4 Real Repo Pilot

M4 的目标不是继续加功能，而是把当前 runtime 放到真实 repo 局部任务里验证。先设计 2 到 3 个小任务 dry run，记录失败 taxonomy 和 timeline 证据，再决定是否需要补控制面 hardening。

建议 pilot 任务粒度：

- 单文件实现修复：读 1 到 2 个文件，编辑 1 个 source 文件，必须跑本地测试。
- 失败测试定位：先读失败测试，再读目标 source，最终只改 source。
- 小范围多文件上下文：允许读 2 到 3 个相关文件，但只编辑 1 个明确目标文件。

M4 进入条件：

- M3 closeout 文档和演示口径已提交。
- `python3 scripts/run_demo_smoke.py` 通过。
- `python3 -m unittest discover -s tests -v` 通过。
- `artifacts/eval/BASELINE.md` 已记录 M3.4 基线摘要。

M4 明确不做：

- 不新增工具类型。
- 不做目录浏览 / 通用检索 / RAG。
- 不做持久化 / 数据库 / worktree。
- 不做多 agent / 子代理 / MCP / plugin / 记忆系统。
- 不为了 pilot 分数去绕过 approval、diff、test、timeline 这条核心闭环。

M4 provider-stability closeout：

- 当前远端 checkpoint：`effc35b`。
- 当前实现锚点：`fa64829`。
- RightCode / `gpt-5.4-mini` 基线保持 `auto_approve_edits = 6/6`、`avg_duplicate_reads = 0.0`。
- `stop_on_request` 继续 `0/6 finished`，但 `6/6` 都干净停在 `edit_approval_required`，没有回退成粗粒度 approval failure。
- provider/transport hardening 没有把失败重新放大到控制面：checkpoint smoke 没出现 `model_transport_failed` 或 `model_provider_response_invalid` 回退。

M4 closeout 验证命令：

```bash
python3 scripts/run_demo_smoke.py
python3 -m unittest discover -s tests -v
```

M4 继续明确不做：

- 不新增工具类型。
- 不做目录浏览 / 通用检索 / RAG。
- 不做持久化 / 数据库 / worktree。
- 不做多 agent / 子代理 / MCP / plugin / 记忆系统。
- 不做更复杂前端或产品化面板。
- 不把 eval pack 长成 benchmark 平台。

## M5 Real Repo Repro Pack

M5 的目标不是新增 runtime 能力，而是把已经验证过的真实 repo pilot 收成一条可复现、本地可讲述的 smoke 入口。底层逻辑是用当前仓库的临时副本承载真实 repo 局部任务，而不是回退到 demo 模板或继续平台化。

本地复现命令：

```bash
python3 scripts/run_real_repo_pilot.py
```

M5.0 当前内置的 real repo pilot case：

- `readme_provider_checkpoint_refresh`：README-only 的 checkpoint 刷新任务，验证文档编辑 + full test 闭环。
- `provider_content_comment_single_file`：单文件 source 注释任务，只改 `repo_task_runtime/model_client.py`，验证 source-only edit 不漂移。
- `failing_test_points_to_source_real`：先看失败测试，再修 `repo_task_runtime/eval_metrics.py`，验证真实 repo 的 test-to-source 修复路径。

M5.0 固定输出摘要：

- 通过率
- 平均步数
- 平均 duplicate reads
- failure taxonomy

M5.1 Patch Contract Hardening closeout：

- 当前远端锚点：`8c94c0a`。
- 控制面实现锚点：`f4e9be2`。
- 真实模型：RightCode / `gpt-5.4-mini`。
- `auto_approve_edits`：`3/3`，`bad_patch_snippet: {}`，平均 duplicate reads `0.0`。
- `stop_on_request`：`0/3`，均停在 `edit_approval_required`，平均 duplicate reads `0.0`。
- 结论：M5.1 只把 ambiguous `expected_old_snippet` 的 patch contract repair 收紧到控制面，未新增工具、目录浏览、RAG、UI 面板或平台能力。

M5.2 Real Repo Observe closeout：

- 当前远端锚点：`2e37b3d`。
- 真实模型：RightCode / `gpt-5.4-mini`。
- Observe 轮次：连续两轮 `auto_approve_edits` + 连续两轮 `stop_on_request`。
- `auto_approve_edits`：两轮均 `3/3`，平均步数 `4.67`，平均 read_file `1.33`，平均 duplicate reads `0.0`，failure taxonomy `{}`，`bad_patch_snippet: {}`。
- `stop_on_request`：两轮均 `0/3`，全部稳定停在 `edit_approval_required`，平均步数 `2.67`，平均 read_file `1.33`，平均 duplicate reads `0.0`。
- 结论：M5.2 没有复现 provider/transport 抖动、same-file reread、bad_patch_snippet 或 approval taxonomy 退化，因此不新增 runtime hardening；继续把改动边界收在证据链和演示口径。

M5 继续明确不做：

- 不做 benchmark 平台或统计面板。
- 不新增工具类型。
- 不做目录浏览 / 通用检索 / RAG。
- 不做持久化 / 数据库 / worktree。
- 不做多 agent / 子代理 / MCP / plugin / 记忆系统。

## M6 Real Repo Pilot Expansion Design

M6 的目标不是把 case 数量做大，而是把真实 repo pilot 的准入标准做硬。底层逻辑是用少量高质量局部任务持续暴露控制面缺口，仍然服务 `read -> patch -> test -> timeline` 这条核心闭环。

M6.0 pilot case 准入标准：

- 必须是真实 repo 局部任务，不使用 demo 模板伪造复杂度。
- 必须有明确目标文件和预期编辑边界，避免把问题退化成目录浏览或检索。
- 必须经过 `read_file -> file_patch/write_file -> run_test -> timeline`，不能绕过 diff、approval 或测试闭环。
- 必须能暴露一个可解释的控制面风险，例如 patch contract、completion contract、approval path、read focus 或 failure taxonomy。
- 必须能用固定摘要复盘：通过率、平均步数、duplicate reads、failure taxonomy。

M6.0 暂定 case 选择方向：

- 实现修复型：目标 source 文件明确，测试已有或可直接运行，验证 source-only edit 不漂移。
- 失败测试定位型：先读失败测试，再读 source，验证 test-to-source 的上下文链路。
- 小范围上下文型：允许读 2 到 3 个相关文件，但只允许编辑 1 个目标文件，继续压 off-target patch。

M6.1 Real Repo Pilot Expansion closeout：

- 当前远端锚点：`c5d13c5`。
- 当前内置真实 repo pilot case：`6` 个。
- 扩展 case 覆盖：completion contract 的 source-only edit、multi-file context single edit、approval path test-first。
- 真实模型：RightCode / `gpt-5.4-mini`。
- `auto_approve_edits`：`6/6`，平均步数 `4.67`，平均 read_file `1.5`，平均 duplicate reads `0.0`，failure taxonomy `{}`。
- `stop_on_request`：`0/6`，全部预期停在 `edit_approval_required`，用于验证 approval 停机路径而不是追求通过率。
- 一次性 duplicate read 噪音：`approval_path_test_first` 在 focused rerun 中已回到 `0.0`，不构成稳定退化信号。
- 结论：M6.1 扩容没有触发新的稳定控制面失败，因此不进入 M6.2 hardening；继续保持不新增工具、不做目录浏览、不扩产品面。

M6 明确不做：

- 不新增目录浏览、搜索工具、通用检索或 RAG。
- 不接 MCP / plugin / memory。
- 不做多 agent、子代理编排或 worktree 管理。
- 不做复杂 UI、统计面板或 benchmark 平台。
- 不为了 case 数量牺牲任务颗粒度和可解释性。

## M7.0 Demo/Interview Delivery Pack Closeout

M7 的目标不是新增 runtime 能力，而是把当前 repo-task agent 的价值压成一条可讲、可跑、可解释的演示路径。底层逻辑是用现有 demo smoke 和 real repo pilot 证明核心闭环，而不是把项目扩成通用 agent 平台。

M7 进入锚点：

- 当前远端 M7 rehearsal notes：`3839011`。
- 当前真实 repo pilot：`6` 个 case。
- 当前真实模型基线：RightCode / `gpt-5.4-mini` 下 `auto_approve_edits = 6/6`。
- 当前 stop_on_request 口径：预期停在 `edit_approval_required`，用于展示 approval gate，不按失败处理。

M7 唯一推荐演示路径：

1. 输入一个真实 repo 局部任务，任务必须有明确目标文件和测试命令。
2. 进入 plan mode，生成 plan 和 todo。
3. approve plan，锁定任务边界。
4. agent 使用 restricted tools 获取上下文，优先 `read_file` / `run_test`，不把 shell 扩成通用命令平台。
5. agent 提交 `file_patch` 或 `write_file` 时进入 approval gate。
6. approve edit 后生成 diff，并绑定当前 repo state。
7. 运行本地测试，`finish` 必须绑定最近一次成功测试。
8. 打开 event timeline，解释 plan/todo、tool、approval、diff、test 和 stop reason。
9. 用 eval summary 复盘通过率、平均步数、duplicate reads 和 failure taxonomy。

M7 本地演示命令：

```bash
python3 scripts/run_demo_smoke.py
python3 scripts/run_real_repo_pilot.py
python3 scripts/run_real_repo_pilot.py --approval-mode stop_on_request
```

M7 验收标准：

- `python3 scripts/run_demo_smoke.py` 必须通过，证明 demo repo 的 task input -> plan/todo -> restricted tool -> approval -> diff/test -> timeline 闭环可跑。
- `python3 -m unittest discover -s tests -v` 必须通过，证明 delivery pack 没有破坏 runtime、API、eval pack、web console 和 real repo pilot 入口。
- README 必须能解释当前 demo 路径、成功指标、approval 停机口径和失败复盘口径，不依赖临场口头补洞。
- 本轮不刷新 RightCode 基线；除非后续要更新真实模型数据，否则继续沿用 M6.1 `6/6` 摘要和本地忽略的 raw JSON 策略。

M7 演示时长口径：

- 30 秒：说明项目不是聊天壳、RAG 或通用 agent 平台，而是面向真实 repo 局部任务的受控 runtime。
- 3 分钟：跑 `run_demo_smoke.py`，沿着 plan/todo、tool、approval、diff/test、timeline 解释核心闭环。
- 8 分钟：再讲 real repo pilot 的 `6` 个 case、RightCode `6/6`、duplicate reads `0.0`、failure taxonomy 和为什么不扩工具面。

M7 失败时如何解释：

- `edit_approval_required`：在 `stop_on_request` 下是预期停机，用来展示 edit approval gate，不按 runtime 失败处理。
- `bad_patch_snippet` / `bad_patch_target`：说明 patch contract 拦住了不可信 diff，应该先看 repair context；只有稳定复现才进入 evidence-based hardening。
- `plan_invalid_output` / `invalid_model_output`：说明模型输出层有可分类失败，不应包装成通用 runner failed；先看 taxonomy，再决定是否需要最小 retry。
- `model_request_failed`：优先归类为 provider/transport 稳定性问题，不把它误判成 agent loop 或工具面缺陷。
- duplicate read 噪音：只在复跑仍稳定出现时处理；单次噪音不作为新增 runtime 功能的理由。

M7.1 rehearsal 小结：

- `python3 scripts/run_demo_smoke.py` 已通过，表现为 `approval_required -> finished`、`approval_kind=edit`、`latest_successful_test=true`。
- RightCode / `gpt-5.4-mini` 全量 real repo pilot 彩排曾出现一次 `5/6`，唯一失败是 `readme_provider_checkpoint_refresh` 的 `invalid_model_output`，`avg_duplicate_reads=0.0`。
- `stop_on_request` 全量彩排符合 approval gate 口径，`5` 个 case 停在 `edit_approval_required`，同一个 README checkpoint case 曾出现一次 `bad_patch_snippet`，`avg_duplicate_reads=0.0`。
- 对 `readme_provider_checkpoint_refresh` focused rerun 后，`auto_approve_edits` 回到 `1/1 PASS`，`stop_on_request` 回到预期 `edit_approval_required`。
- Owner 结论：没有稳定复现的 runtime 缺口，不进入 `agent.py` / `session.py` / `context_bundle.py` / `eval_metrics.py` hardening；避免为单次模型噪音过拟合。

M7.2 面试彩排口径：

- 开场 30 秒：这是 Repo-Task Agent Runtime，不是聊天壳、RAG、MCP 平台或多 agent team；价值在受控地跑真实 repo 局部任务。
- 演示 3 分钟：先跑 `python3 scripts/run_demo_smoke.py`，按 task input、plan/todo、restricted tool、approval、diff/test、timeline 顺序讲闭环。
- 深挖 8 分钟：补充 real repo pilot 的 `6` 个 case、RightCode 基线、duplicate reads `0.0`、approval stop reason 和 failure taxonomy。
- 被问到 `stop_on_request` 的 `0/6` 时，明确说明这是 approval gate 演示模式，不是追求通过率；预期停机原因是 `edit_approval_required`。
- 被问到 rehearsal 中的 README case 波动时，说明 focused rerun 已恢复，当前判断为非稳定模型/patch 噪音；项目只对可复现失败做 evidence-based hardening。
- 被问到为什么不加搜索、目录浏览、RAG、memory、多 agent 时，回答是 M7 目标是证明最小闭环和控制面边界，不把演示包扩成通用平台。

M7 面试讲述抓手：

- 架构边界：Python control plane 管 agent loop、session、approval、diff、test、timeline；Web 控制台只做薄展示。
- 核心取舍：只做单 agent、局部 repo task、受限工具、可解释 timeline，不做聊天壳或通用平台。
- 稳定性证据：M6.1 real repo pilot `6/6`，duplicate reads 已压到 `0.0`，approval stop reason 已结构化。
- 可回归资产：demo smoke 验闭环，real repo pilot 验真实 repo 局部任务，`BASELINE.md` 只提交摘要不提交 raw JSON。

M7 明确不做：

- 不新增工具类型。
- 不做目录浏览、搜索工具、通用检索或 RAG。
- 不接 MCP / plugin / memory。
- 不做多 agent、子代理编排或 worktree 管理。
- 不做复杂 UI、统计面板或 benchmark 平台。
- 不把演示包包装成产品化交付平台。

## M8 Release/Review Readiness Closeout

M8 的目标不是新增 agent 能力，而是确认项目从“本机能跑”提升到“fresh checkout 可复现、owner review 可解释、风险边界清楚”。这一阶段继续冻结 runtime，只有稳定、可复现、能指向控制面的失败才允许进入 evidence-based hardening。

M8.0 clean clone / fresh run 结论：

- fresh checkout 在没有 API 依赖时，`python3 -m unittest discover -s tests -v` 可以通过，但 API / web / demo smoke 相关测试会按预期 skip，`python3 scripts/run_demo_smoke.py` 会因为缺少 `fastapi` 停止。
- 安装 API 依赖到 `./.vendor` 后，fresh checkout 的 `python3 scripts/run_demo_smoke.py` 通过，表现为 `approval_required -> finished`、`approval_kind=edit`、`latest_successful_test=true`。
- 同一个 fresh checkout 在补齐 `./.vendor` 后，`python3 -m unittest discover -s tests -v` 跑到 `95/95 OK`，不再依赖当前工作树的隐式环境。

fresh checkout 演示前置条件：

```bash
python3 -m pip install --target ./.vendor fastapi uvicorn pydantic httpx
python3 scripts/run_demo_smoke.py
python3 -m unittest discover -s tests -v
```

M8.1 owner review sweep 结论：

- README 的 eval pack case 数已经同步为 `6`，避免和 `repo_task_runtime/eval_cases.py` 漂移。
- README 的 failure taxonomy 已同步为当前细粒度分类，避免把 approval、provider、read-focus、patch-contract 失败重新混成粗粒度失败。
- `artifacts/eval/BASELINE.md` 继续只保留真实模型摘要，不提交 raw JSON，不把 M8 文档收口伪装成新的模型基线。

M8.2 minimal risk register：

- provider / transport 波动：`model_transport_failed` 和 `model_provider_response_invalid` 是 provider 层信号，不能直接归咎于 agent loop 或工具设计。
- 模型输出不稳定：`plan_invalid_output`、`invalid_model_output`、`bad_patch_snippet` 只在稳定复现时触发控制面 hardening；单次波动先复跑和归档。
- `stop_on_request` 语义：`0/N` 是 approval gate 演示模式下的预期结果，关键看是否稳定停在 `edit_approval_required`，不是追求通过率。
- real repo pilot 覆盖边界：当前只覆盖小范围真实 repo 局部任务，不声明支持目录浏览、通用检索、RAG、多 agent 或 worktree 隔离。

M8.3 hardening gate：

- 本轮 clean clone / rehearsal 没有暴露稳定 runtime 控制面失败，因此不进入 `agent.py` / `session.py` / `context_bundle.py` / `eval_metrics.py` hardening。
- 后续只有当同一失败在相同 case、相同 approval mode 下可复现，并且 taxonomy 能指向控制面缺口，才允许打开最小修复。

## M11 External Review Rehearsal Freeze

M11 的目标是证明外部 reviewer 可以从稳定 tag 复现、理解和质询项目，而不是继续开发功能。runtime 在这一阶段保持冻结；只有稳定、可复现、同 case / 同 approval mode、且能指向控制面的失败才允许进入 hardening。

M11.0 tag-based clean run：

- 从 `m10-review-ready` tag 做 clean checkout，锚点为 `9d73c43`。
- 按 Owner Review Pack 安装 API 依赖到 `./.vendor` 后，`python3 scripts/run_demo_smoke.py` 通过。
- 同一个 clean checkout 跑 `python3 -m unittest discover -s tests -v`，结果为 `95/95 OK`。

M11.1 interview rehearsal：

- 30 秒口径：这是 Repo-Task Agent Runtime / Workbench，不是聊天壳、RAG、MCP 平台或多 agent team；价值在受控地执行真实 repo 局部任务。
- 3 分钟口径：跑 `python3 scripts/run_demo_smoke.py`，按 task input、plan/todo、restricted tool、approval、diff/test、timeline 顺序解释闭环。
- 8 分钟口径：补充 real repo pilot 的 `6` 个 case、RightCode spot check、approval gate、duplicate-read 指标和 failure taxonomy；明确 pilot 是证据包，不是 benchmark 平台。

M11.2 RightCode spot check：

- 模型：RightCode / `gpt-5.4-mini`。
- `auto_approve_edits`：`6/6`，平均步数 `5.17`，平均 read_file `1.67`，平均 duplicate reads `0.17`，failure taxonomy `{}`。
- `stop_on_request`：`0/6`，全部预期停在 `edit_approval_required`，平均 duplicate reads `0.0`，failure taxonomy `{"edit_approval_required": 6}`。
- `failing_test_points_to_source_real` focused rerun 曾在沙箱内出现一次 DNS 型 `Model request failed: [Errno 8] nodename nor servname provided, or not known`，沙箱外同 case 复跑恢复为 `1/1 PASS`。
- 同一个 focused rerun 仍有 duplicate-read 噪音，但没有产生 patch/test/timeline 失败；M11 将它记录为观察信号，不作为立刻修改 runtime 的理由。

M11.3 evidence gate：

- 没有发现稳定可复现的控制面失败。
- 不进入 `agent.py` / `session.py` / `context_bundle.py` / `eval_metrics.py` hardening。
- 不新增目录浏览、RAG、MCP、memory、多 agent、worktree、复杂 UI、新工具类型或 benchmark 平台。

## M12 External Reviewer Handoff

M12 的目标不是继续开发，而是把 `m12-external-review-handoff` 作为唯一 handoff 入口交给外部 reviewer 复现和质询。`m11-external-review-freeze` 保留为 runtime freeze 参考点，不再作为 reviewer 的实际 checkout 入口。Reviewer 应从 README 顶部的 Owner Review Pack 开始，按里面的命令验证本地闭环；本阶段不新增工具、不重跑模型基线、不修改 runtime。

M12.0 reviewer handoff：

- 固定 handoff 入口：`m12-external-review-handoff` tag。
- runtime freeze 参考点：`m11-external-review-freeze` tag，提交 `dc10b11`。
- 固定阅读路径：README 顶部 Owner Review Pack -> 项目目标 -> 验证命令 -> risk register。
- 固定演示路径：`python3 scripts/run_demo_smoke.py` 证明 task input、plan/todo、approval、diff/test、timeline 的闭环。

M12.1 friction log：

- 只记录 reviewer 卡点，不直接修 runtime。
- 记录字段：依赖安装、命令顺序、`stop_on_request` 语义、baseline 解读、failure taxonomy 解读。
- 初始 owner pass 没发现新的文档阻塞点；真实 reviewer 反馈进入下一轮 docs-only fix。

M12.2 docs-only fix：

- 如果问题是入口不清、命令顺序不清、approval 语义误解或 baseline 口径误解，只改 README / `docs/OWNER_REVIEW.md`。
- 不把解释问题误判成 agent loop、tooling 或 model runtime 缺口。

M12.x evidence-based hardening gate：

- 只有同一 case、同一 approval mode、稳定复现、taxonomy 指向控制面时，才允许打开 `agent.py` / `session.py` / `context_bundle.py` / `eval_metrics.py`。
- 单次 provider/transport 抖动、sandbox DNS、duplicate-read 噪音或模型 patch 过拟合，只归档，不触发 runtime 修改。

## M13 Reviewer Feedback Intake

M13 的目标是验证 reviewer handoff 是否真的可复现，并把反馈入口收窄到 friction log，而不是继续开发 runtime。本轮没有实际外部 reviewer 新反馈，因此采用 owner-simulated dry run：从 `m12-external-review-handoff` 做 fresh checkout，严格按 Owner Review Pack 的本地命令验证。

M13.0 external reviewer dry run：

- checkout 入口：`m12-external-review-handoff`，提交 `bf7cf46`。
- 按文档安装 API 依赖到 `./.vendor` 后，`python3 scripts/run_demo_smoke.py` 通过。
- 同一个 fresh checkout 跑 `python3 -m unittest discover -s tests -v`，结果为 `95/95 OK`。
- 本轮未重跑 RightCode；真实模型基线仍以 `artifacts/eval/BASELINE.md` 和前序 checkpoint 摘要为准。

M13.1 friction log triage：

- dependency setup：`./.vendor` 安装步骤可执行，没有新增阻塞。
- command order：Owner Review Pack 的 install -> demo smoke -> unittest 顺序可执行。
- approval semantics：本地 demo smoke 正常表现为先 `approval_required`，审批后 `finished`。
- baseline reading：未发现 raw JSON、`BASELINE.md`、reviewer-facing summary 之间的新口径冲突。
- failure taxonomy：本轮没有产生新的 failure bucket 或泛化失败。

M13.2 docs-only fix / evidence gate：

- 只补充本节和 Owner Review Pack 的 M13 证据，不修改 runtime。
- 没有同一 case、同一 approval mode、稳定复现且 taxonomy 指向控制面的失败，因此不进入 `agent.py` / `session.py` / `context_bundle.py` / `eval_metrics.py` hardening。
- 继续禁止目录浏览、RAG、MCP、memory、多 agent、worktree、复杂 UI、新工具类型和 benchmark 平台。

## M14 Real External Feedback Intake

M14 的目标是把反馈入口从 owner-simulated dry run 推进到真实外部 reviewer。仓库侧只交付 intake 合约：reviewer 仍从 `m12-external-review-handoff` checkout，按 README 和 Owner Review Pack 验证；反馈只记录 friction；没有稳定控制面失败，不打开 runtime。

M14.0 real reviewer run：

- 执行人必须是真实外部 reviewer；owner 本地复跑不能替代 M14 外部反馈。
- 固定 checkout 入口仍是 `m12-external-review-handoff`，避免 reviewer 跟随后续 docs-only commit 漂移。
- 固定阅读路径仍是 README 顶部 Owner Review Pack -> 验证命令 -> risk register。
- 固定本地验证命令是 `python3 scripts/run_demo_smoke.py` 和 `python3 -m unittest discover -s tests -v`。
- RightCode / `gpt-5.4-mini` 真实模型复跑是可选 spot check，只有明确授权时才执行。

M14.1 friction log：

- dependency setup：`./.vendor` 安装步骤是否缺失、失败或顺序不清。
- command order：demo smoke、unittest、real repo pilot 的执行顺序是否误导。
- `stop_on_request` semantics：是否把预期的 `edit_approval_required` 停机误读成失败。
- baseline reading：是否混淆 raw JSON、本地 ignored artifact、`BASELINE.md` 摘要和 reviewer-facing 口径。
- taxonomy reading：是否看不懂 failure bucket，或把 provider/transport 抖动误判成 runtime 缺口。
- 当前状态：暂无真实外部 reviewer 反馈；不能把 M13 的 owner-simulated dry run 写成 M14 pass。

M14.2 docs-only fix：

- 如果 friction 是入口、命令、approval 语义、baseline 或 taxonomy 解释不清，只改 README / `docs/OWNER_REVIEW.md`。
- 不把解释问题升级成 agent loop、tooling、model provider 或 runtime hardening。

M14.x evidence-based hardening gate：

- 只有同一 case、同一 approval mode、稳定复现、taxonomy 指向控制面，才允许打开 `agent.py` / `session.py` / `context_bundle.py` / `eval_metrics.py`。
- 继续禁止目录浏览、RAG、MCP、memory、多 agent、worktree、复杂 UI、新工具类型和 benchmark 平台。

## 测试

除了原有 runtime / API 测试，这一轮新增：

- `tests/test_agent.py`：验证模型 plan 生成和 agent step 仍受审批流约束
- `tests/test_web_console.py`：验证控制台首页和静态资源可访问
- `tests/test_demo_flow.py`：验证 demo repo 的完整 bugfix 流程
- `tests/test_demo_smoke_script.py`：验证 M3 demo smoke 一条命令能跑通闭环
- `tests/test_real_repo_pilot_script.py`：验证 M5 real repo repro 入口能输出稳定摘要
