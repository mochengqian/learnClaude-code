# Repo-Task Agent Runtime / Workbench

这个仓库当前只做一件事：用 Python 搭一个面向真实代码仓库局部任务的最小 runtime。

它不是：
- Claude Code 仿品
- 通用 AI Agent 平台
- 聊天壳
- RAG 系统
- 多智能体 team system
- 花哨前端

## 当前阶段

这一轮只做后端核心闭环：

`task input -> plan mode -> todo lifecycle -> restricted tools -> approval -> diff -> local test -> event timeline`

这一轮明确不做：

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

当前内置 3 个 case：

- `slug_join`
- `clamp_lower_bound`
- `compact_whitespace`

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
- `bad_patch`
- `directory_path`
- `edit_without_read`
- `invalid_finish`
- `invalid_model_output`
- `missing_relative_path`
- `missing_repo_file`
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

## 测试

除了原有 runtime / API 测试，这一轮新增：

- `tests/test_agent.py`：验证模型 plan 生成和 agent step 仍受审批流约束
- `tests/test_web_console.py`：验证控制台首页和静态资源可访问
- `tests/test_demo_flow.py`：验证 demo repo 的完整 bugfix 流程
- `tests/test_demo_smoke_script.py`：验证 M3 demo smoke 一条命令能跑通闭环
- `tests/test_real_repo_pilot_script.py`：验证 M5 real repo repro 入口能输出稳定摘要
