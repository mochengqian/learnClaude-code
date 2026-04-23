const state = {
  sessionId: null,
  latestResult: null,
  latestAgent: null,
  demoDefaults: null,
};

const elements = {
  currentSessionId: document.getElementById("current-session-id"),
  statusMessage: document.getElementById("status-message"),
  snapshotSummary: document.getElementById("snapshot-summary"),
  snapshotPanel: document.getElementById("snapshot-panel"),
  approvalsPanel: document.getElementById("approvals-panel"),
  latestResultSummary: document.getElementById("latest-result-summary"),
  diffPanel: document.getElementById("diff-panel"),
  timelinePanel: document.getElementById("timeline-panel"),
  repoPathInput: document.getElementById("repo-path-input"),
  taskInputTextarea: document.getElementById("task-input-textarea"),
  planTextarea: document.getElementById("plan-textarea"),
  todosTextarea: document.getElementById("todos-textarea"),
  toolTypeSelect: document.getElementById("tool-type-select"),
  toolRelativePathInput: document.getElementById("tool-relative-path-input"),
  toolContentTextarea: document.getElementById("tool-content-textarea"),
  toolExpectedOldSnippetTextarea: document.getElementById("tool-expected-old-snippet-textarea"),
  toolNewSnippetTextarea: document.getElementById("tool-new-snippet-textarea"),
  toolReplaceAllCheckbox: document.getElementById("tool-replace-all-checkbox"),
  toolCommandInput: document.getElementById("tool-command-input"),
  agentLoopMaxStepsInput: document.getElementById("agent-loop-max-steps-input"),
};

function clearNode(node) {
  node.innerHTML = "";
}

function appendMutedMessage(node, message) {
  const text = document.createElement("p");
  text.className = "muted";
  text.textContent = message;
  node.appendChild(text);
}

function createMetaPill(label, value, className = "") {
  const pill = document.createElement("span");
  pill.className = className ? `meta-pill ${className}` : "meta-pill";
  const strong = document.createElement("strong");
  strong.textContent = `${label}:`;
  pill.appendChild(strong);
  pill.append(document.createTextNode(` ${value || "none"}`));
  return pill;
}

function createApprovalKindBadge(kind) {
  const badge = document.createElement("span");
  badge.className = `kind-badge kind-${kind || "none"}`;
  badge.textContent = `approval:${(kind || "none").toUpperCase()}`;
  return badge;
}

function createSummaryRow(...nodes) {
  const row = document.createElement("div");
  row.className = "meta-row";
  nodes.filter(Boolean).forEach((node) => row.appendChild(node));
  return row;
}

function createStateBadge(label, value, tone = "none") {
  const badge = document.createElement("span");
  badge.className = `state-badge state-${tone}`;
  const strong = document.createElement("strong");
  strong.textContent = `${label}:`;
  badge.appendChild(strong);
  badge.append(document.createTextNode(` ${value}`));
  return badge;
}

function approvalKindFromPayload(payload) {
  if (!payload) {
    return null;
  }
  return payload.approval_kind || (payload.approval && payload.approval.approval_kind) || null;
}

function summarizeLatestSuccessfulTest(session) {
  if (!session || !session.latest_successful_test) {
    return {
      badge: createStateBadge("test", "missing", "none"),
      command: null,
    };
  }

  const command = Array.isArray(session.latest_successful_test.command)
    ? session.latest_successful_test.command.join(" ")
    : String(session.latest_successful_test.command || "");
  return {
    badge: createStateBadge("test", "current pass", "good"),
    command: createMetaPill("test cmd", command || "none"),
  };
}

function summarizeLatestDiff(session) {
  const diff = session && session.latest_diff ? session.latest_diff : "";
  if (!diff) {
    return createStateBadge("diff", "clean", "none");
  }
  return createStateBadge("diff", `${diff.length} chars`, "dirty");
}

function buildTodoSummary(session) {
  const counts = {
    in_progress: 0,
    pending: 0,
    completed: 0,
  };
  const todos = Array.isArray(session && session.todos) ? session.todos : [];

  todos.forEach((todo) => {
    const status = String(todo.status || "pending").toLowerCase();
    if (Object.prototype.hasOwnProperty.call(counts, status)) {
      counts[status] += 1;
    }
  });

  return {
    counts,
    activeTodo: todos.find((todo) => String(todo.status || "").toLowerCase() === "in_progress") || null,
    nextPendingTodo: todos.find((todo) => String(todo.status || "").toLowerCase() === "pending") || null,
  };
}

function summarizeTodoLabel(todo) {
  if (!todo) {
    return "none";
  }
  return String(todo.content || todo.active_form || "none");
}

function summarizePlanMode(session) {
  const isPlanMode = session && session.permission_mode === "plan";
  return createStateBadge("plan mode", isPlanMode ? "active" : "exited", isPlanMode ? "dirty" : "good");
}

function appendPlanTodoSummary(node, session) {
  const summary = buildTodoSummary(session);
  node.appendChild(
    createSummaryRow(
      summarizePlanMode(session),
      createStateBadge("in_progress", String(summary.counts.in_progress), summary.counts.in_progress ? "dirty" : "none"),
      createStateBadge("pending", String(summary.counts.pending), summary.counts.pending ? "none" : "good"),
      createStateBadge("completed", String(summary.counts.completed), summary.counts.completed ? "good" : "none"),
    ),
  );
  node.appendChild(
    createSummaryRow(
      createMetaPill("active todo", summarizeTodoLabel(summary.activeTodo), "todo-pill"),
      createMetaPill("next pending", summarizeTodoLabel(summary.nextPendingTodo), "todo-pill"),
    ),
  );
  return summary;
}

function appendRepoStateSummary(node, session) {
  const testSummary = summarizeLatestSuccessfulTest(session);
  node.appendChild(
    createSummaryRow(
      testSummary.badge,
      summarizeLatestDiff(session),
    ),
  );
  if (testSummary.command) {
    node.appendChild(createSummaryRow(testSummary.command));
  }
}

function renderResultSummary(node, result) {
  clearNode(node);
  if (!result) {
    appendMutedMessage(node, "No tool result yet.");
    return;
  }

  node.appendChild(
    createSummaryRow(
      createMetaPill("tool", result.tool_name || "unknown"),
      createMetaPill("status", result.status || "unknown"),
      createApprovalKindBadge(result.approval_kind),
    ),
  );
}

function setStatus(message, isError = false) {
  elements.statusMessage.textContent = message;
  elements.statusMessage.classList.toggle("warning", isError);
}

function requireSession() {
  if (!state.sessionId) {
    throw new Error("Create a session first.");
  }
}

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail = typeof payload === "string" ? payload : payload.detail || JSON.stringify(payload);
    throw new Error(detail);
  }
  return payload;
}

function renderSnapshot(session) {
  elements.currentSessionId.textContent = session.session_id;
  const latestApprovalKind = session.latest_tool_result
    ? session.latest_tool_result.approval_kind
    : session.pending_approvals[0]
      ? session.pending_approvals[0].approval_kind
      : null;
  renderResultSummary(elements.snapshotSummary, {
    tool_name: session.latest_tool_result ? session.latest_tool_result.tool_name : "none",
    status: session.latest_tool_result ? session.latest_tool_result.status : "idle",
    approval_kind: latestApprovalKind,
  });
  elements.snapshotSummary.appendChild(
    createSummaryRow(createMetaPill("pending approvals", String(session.pending_approvals.length))),
  );
  const todoSummary = appendPlanTodoSummary(elements.snapshotSummary, session);
  appendRepoStateSummary(elements.snapshotSummary, session);
  elements.snapshotPanel.textContent = JSON.stringify(
    {
      session_id: session.session_id,
      repo_path: session.repo_path,
      task_input: session.task_input,
      permission_mode: session.permission_mode,
      plan: session.plan,
      todos: session.todos,
      plan_mode_exited: session.permission_mode !== "plan",
      todo_counts: todoSummary.counts,
      active_todo: summarizeTodoLabel(todoSummary.activeTodo),
      next_pending_todo: summarizeTodoLabel(todoSummary.nextPendingTodo),
      pending_approvals: session.pending_approvals.length,
      latest_tool_result: session.latest_tool_result,
      latest_successful_test: session.latest_successful_test,
      latest_diff_chars: session.latest_diff ? session.latest_diff.length : 0,
    },
    null,
    2,
  );
}

function renderApprovals(session) {
  const approvals = session.pending_approvals || [];
  if (approvals.length === 0) {
    elements.approvalsPanel.innerHTML = '<p class="muted">No pending approvals.</p>';
    return;
  }

  elements.approvalsPanel.innerHTML = "";
  approvals.forEach((approval) => {
    const wrapper = document.createElement("div");
    wrapper.className = "approval-card";
    const head = document.createElement("div");
    head.className = "card-head";
    const title = document.createElement("strong");
    title.textContent = approval.tool_name;
    head.appendChild(title);
    head.appendChild(createApprovalKindBadge(approval.approval_kind));

    const reason = document.createElement("p");
    reason.textContent = approval.reason;

    const requestPreview = document.createElement("pre");
    requestPreview.textContent = JSON.stringify(approval.request, null, 2);

    const actions = document.createElement("div");
    actions.className = "approval-actions";

    const approveButton = document.createElement("button");
    approveButton.dataset.approvalId = approval.approval_id;
    approveButton.dataset.approve = "true";
    approveButton.textContent = "Approve Once";

    const rejectButton = document.createElement("button");
    rejectButton.className = "secondary";
    rejectButton.dataset.approvalId = approval.approval_id;
    rejectButton.dataset.approve = "false";
    rejectButton.textContent = "Reject";

    actions.appendChild(approveButton);
    actions.appendChild(rejectButton);

    wrapper.appendChild(head);
    wrapper.appendChild(reason);
    wrapper.appendChild(requestPreview);
    wrapper.appendChild(actions);
    elements.approvalsPanel.appendChild(wrapper);
  });
}

function renderDiff(
  session,
  result = state.latestResult || session.latest_tool_result,
  agent = state.latestAgent,
) {
  renderResultSummary(elements.latestResultSummary, result);
  appendRepoStateSummary(elements.latestResultSummary, session);
  const resultSummary = result ? JSON.stringify(result, null, 2) : "No tool result yet.";
  const diff = session.latest_diff || "No diff yet.";
  const agentSummary = agent ? `${JSON.stringify(agent, null, 2)}\n\n--- tool result ---\n\n` : "";
  elements.diffPanel.textContent = `${agentSummary}${resultSummary}\n\n--- latest diff ---\n\n${diff}`;
}

function renderTimeline(session) {
  const timeline = session.timeline || [];
  if (timeline.length === 0) {
    elements.timelinePanel.innerHTML = '<p class="muted">No events yet.</p>';
    return;
  }

  elements.timelinePanel.innerHTML = "";
  timeline
    .slice()
    .reverse()
    .forEach((event) => {
      const item = document.createElement("div");
      item.className = "timeline-item";
      const head = document.createElement("div");
      head.className = "card-head";

      const title = document.createElement("strong");
      title.textContent = event.event_type;
      head.appendChild(title);

      head.appendChild(createApprovalKindBadge(approvalKindFromPayload(event.payload)));

      const createdAt = document.createElement("p");
      createdAt.className = "muted";
      createdAt.textContent = event.created_at;

      const payloadPreview = document.createElement("pre");
      payloadPreview.textContent = JSON.stringify(event.payload, null, 2);

      item.appendChild(head);
      item.appendChild(createdAt);
      item.appendChild(payloadPreview);
      elements.timelinePanel.appendChild(item);
    });
}

function hydrateFromSession(session) {
  state.sessionId = session.session_id;
  state.latestResult = session.latest_tool_result || state.latestResult;
  renderSnapshot(session);
  renderApprovals(session);
  renderDiff(session);
  renderTimeline(session);
}

function parseTodos(rawText) {
  return rawText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const pieces = line.split("|").map((piece) => piece.trim());
      return {
        status: pieces[0] || "pending",
        content: pieces[1] || "",
        active_form: pieces[2] || pieces[1] || "",
      };
    });
}

function fillDemoDefaults(payload) {
  state.demoDefaults = payload;
  elements.repoPathInput.value = payload.repo_path;
  elements.taskInputTextarea.value = payload.task_input;
  elements.planTextarea.value = [
    "1. Read the failing module and test.",
    "2. Apply the smallest safe fix.",
    "3. Run the local unittest suite.",
  ].join("\n");
  elements.todosTextarea.value = [
    "in_progress | Read string_tools.py and tests | Read string_tools.py and tests",
    "pending | Fix the slug join character | Fix the slug join character",
    "pending | Run the unittest suite | Run the unittest suite",
  ].join("\n");
  elements.toolCommandInput.value = payload.test_command;
  elements.toolRelativePathInput.value = "demo_app/string_tools.py";
  setStatus(`Demo repo ready at ${payload.repo_path}`);
}

document.getElementById("demo-setup-button").addEventListener("click", async () => {
  try {
    const payload = await apiFetch("/demo/setup", { method: "POST" });
    fillDemoDefaults(payload.demo);
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("refresh-session-button").addEventListener("click", async () => {
  try {
    requireSession();
    const payload = await apiFetch(`/sessions/${state.sessionId}`);
    hydrateFromSession(payload.session);
    setStatus("Session refreshed.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("session-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = await apiFetch("/sessions", {
      method: "POST",
      body: JSON.stringify({
        repo_path: elements.repoPathInput.value,
        task_input: elements.taskInputTextarea.value,
      }),
    });
    state.latestResult = null;
    hydrateFromSession(payload.session);
    setStatus("Session created.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("plan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    requireSession();
    const payload = await apiFetch(`/sessions/${state.sessionId}/plan`, {
      method: "POST",
      body: JSON.stringify({ plan_markdown: elements.planTextarea.value }),
    });
    hydrateFromSession(payload.session);
    setStatus("Plan saved.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("agent-plan-button").addEventListener("click", async () => {
  try {
    requireSession();
    const payload = await apiFetch(`/sessions/${state.sessionId}/agent/plan`, {
      method: "POST",
    });
    state.latestAgent = payload.agent;
    hydrateFromSession(payload.session);
    setStatus(`Agent drafted plan with ${payload.agent.model}.`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("approve-plan-button").addEventListener("click", async () => {
  try {
    requireSession();
    const payload = await apiFetch(`/sessions/${state.sessionId}/plan/approve`, {
      method: "POST",
    });
    hydrateFromSession(payload.session);
    setStatus("Plan approved. Session exited plan mode.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("todos-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    requireSession();
    const todos = parseTodos(elements.todosTextarea.value);
    const payload = await apiFetch(`/sessions/${state.sessionId}/todos`, {
      method: "PUT",
      body: JSON.stringify({ todos }),
    });
    hydrateFromSession(payload.session);
    setStatus("Todos replaced.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("tool-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    requireSession();
    const payload = await apiFetch(`/sessions/${state.sessionId}/tools`, {
      method: "POST",
      body: JSON.stringify({
        tool_type: elements.toolTypeSelect.value,
        relative_path: elements.toolRelativePathInput.value || null,
        expected_old_snippet: elements.toolExpectedOldSnippetTextarea.value || null,
        new_snippet: elements.toolNewSnippetTextarea.value || null,
        replace_all: elements.toolReplaceAllCheckbox.checked,
        content: elements.toolContentTextarea.value || null,
        command: elements.toolCommandInput.value || "",
      }),
    });
    state.latestAgent = null;
    state.latestResult = payload.result;
    hydrateFromSession(payload.session);

    if (
      payload.result.status === "executed" &&
      payload.result.tool_name === "read_file" &&
      payload.result.data &&
      payload.result.data.content
    ) {
      elements.toolContentTextarea.value = payload.result.data.content;
    }

    setStatus(`Tool request completed: ${payload.result.status}`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("agent-step-button").addEventListener("click", async () => {
  try {
    requireSession();
    const payload = await apiFetch(`/sessions/${state.sessionId}/agent/step`, {
      method: "POST",
    });
    state.latestAgent = payload.agent;
    state.latestResult = payload.agent.tool_result;
    hydrateFromSession(payload.session);
    const action = payload.agent.decision ? payload.agent.decision.action : "unknown";
    setStatus(`Agent step completed: ${action}`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("agent-loop-button").addEventListener("click", async () => {
  try {
    requireSession();
    const payload = await apiFetch(`/sessions/${state.sessionId}/agent/loop`, {
      method: "POST",
      body: JSON.stringify({
        max_steps: Number(elements.agentLoopMaxStepsInput.value || 3),
      }),
    });
    state.latestAgent = payload.agent;
    const steps = payload.agent.steps || [];
    state.latestResult = steps.length > 0 ? steps[steps.length - 1].tool_result : null;
    hydrateFromSession(payload.session);
    setStatus(
      `Agent loop stopped: ${payload.agent.stop_reason} after ${payload.agent.steps_completed} step(s).`,
    );
  } catch (error) {
    setStatus(error.message, true);
  }
});

elements.approvalsPanel.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-approval-id]");
  if (!button) {
    return;
  }
  try {
    requireSession();
    const payload = await apiFetch(
      `/sessions/${state.sessionId}/approvals/${button.dataset.approvalId}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({ approve: button.dataset.approve === "true" }),
      },
    );
    state.latestAgent = null;
    state.latestResult = payload.result;
    hydrateFromSession(payload.session);
    setStatus(`Approval resolved: ${payload.result.status}`);
  } catch (error) {
    setStatus(error.message, true);
  }
});
