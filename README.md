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
  diffing.py
  demo_repo.py
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
scripts/
  setup_demo_repo.py
tests/
  test_agent.py
  test_runtime.py
  test_api.py
  test_demo_flow.py
  test_web_console.py
```

## 核心接口

```python
from pathlib import Path

from repo_task_runtime import (
    TaskWorkbench,
    TestCommandRequest,
    TodoItem,
    TodoStatus,
    WriteFileRequest,
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
    WriteFileRequest(relative_path="notes.txt", content="temporary note\n")
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

首版只保留 4 类请求：

- `FileReadRequest`
- `WriteFileRequest`
- `ShellCommandRequest`
- `TestCommandRequest`

## 测试方案

当前测试覆盖：

- `plan mode` 对变更工具的阻断
- `todo lifecycle` 状态机约束
- 写文件审批流
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
8. 如果 agent 走到 `write_file`，它仍然会进入 approval queue。
9. approve once 后，再跑下一步 agent step、agent loop 或手工触发 `run_test`。
10. 确认测试通过。
11. 在控制台查看 latest diff、latest tool result 和 event timeline。

## 测试

除了原有 runtime / API 测试，这一轮新增：

- `tests/test_agent.py`：验证模型 plan 生成和 agent step 仍受审批流约束
- `tests/test_web_console.py`：验证控制台首页和静态资源可访问
- `tests/test_demo_flow.py`：验证 demo repo 的完整 bugfix 流程
