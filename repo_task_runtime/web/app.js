const state = {
  sessionId: null,
  latestResult: null,
  latestAgent: null,
  demoDefaults: null,
};

const elements = {
  currentSessionId: document.getElementById("current-session-id"),
  statusMessage: document.getElementById("status-message"),
  snapshotPanel: document.getElementById("snapshot-panel"),
  approvalsPanel: document.getElementById("approvals-panel"),
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
  elements.snapshotPanel.textContent = JSON.stringify(
    {
      session_id: session.session_id,
      repo_path: session.repo_path,
      task_input: session.task_input,
      permission_mode: session.permission_mode,
      plan: session.plan,
      todos: session.todos,
      pending_approvals: session.pending_approvals.length,
      latest_tool_result: session.latest_tool_result,
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
    wrapper.innerHTML = `
      <strong>${approval.tool_name}</strong>
      <p>${approval.reason}</p>
      <pre>${JSON.stringify(approval.request, null, 2)}</pre>
      <div class="approval-actions">
        <button data-approval-id="${approval.approval_id}" data-approve="true">Approve Once</button>
        <button class="secondary" data-approval-id="${approval.approval_id}" data-approve="false">Reject</button>
      </div>
    `;
    elements.approvalsPanel.appendChild(wrapper);
  });
}

function renderDiff(
  session,
  result = state.latestResult || session.latest_tool_result,
  agent = state.latestAgent,
) {
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
      item.innerHTML = `
        <strong>${event.event_type}</strong>
        <p class="muted">${event.created_at}</p>
        <pre>${JSON.stringify(event.payload, null, 2)}</pre>
      `;
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
