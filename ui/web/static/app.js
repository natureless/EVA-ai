const statePanel = document.getElementById("statePanel");
const memoryPanel = document.getElementById("memoryPanel");
const tracePanel = document.getElementById("tracePanel");
const eventPanel = document.getElementById("eventPanel");
const agentsPanel = document.getElementById("agentsPanel");
const healthPanel = document.getElementById("healthPanel");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const chatStatus = document.getElementById("chatStatus");
const lastReply = document.getElementById("lastReply");
const refreshBtn = document.getElementById("refreshBtn");

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return await res.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderKV(target, data) {
  const rows = Object.entries(data).map(([key, value]) => {
    const display = typeof value === "object"
      ? escapeHtml(JSON.stringify(value, null, 2))
      : escapeHtml(value);
    return `
      <div class="kv-key">${escapeHtml(key)}</div>
      <div class="kv-value">${display}</div>
    `;
  });
  target.innerHTML = rows.join("");
}

function renderList(target, items, formatter) {
  if (!items || items.length === 0) {
    target.innerHTML = `<div class="item"><div class="item-body">No data yet.</div></div>`;
    return;
  }
  target.innerHTML = items.map(formatter).join("");
}

function renderMemoryItem(item) {
  return `
    <div class="item">
      <div class="item-title">${escapeHtml(item.summary)}</div>
      <div class="item-meta">
        ${escapeHtml(item.timestamp)} - ${escapeHtml(item.event_type)} - importance=${escapeHtml(item.importance)}
      </div>
      <div class="item-body">${escapeHtml(JSON.stringify(item.payload, null, 2))}</div>
    </div>
  `;
}

function renderTraceItem(item) {
  return `
    <div class="item">
      <div class="item-title">${escapeHtml(item.loop_id)}</div>
      <div class="item-meta">
        ${escapeHtml(item.timestamp)} - event=${escapeHtml(item.event_type)} - agent=${escapeHtml(item.agent)} - ${escapeHtml(item.duration_ms)}ms
      </div>
      <div class="item-body">decision=${escapeHtml(item.decision)}\nsummary=${escapeHtml(item.result_summary)}</div>
    </div>
  `;
}

function renderEventItem(item) {
  return `
    <div class="item">
      <div class="item-title">${escapeHtml(item.type)}</div>
      <div class="item-meta">
        ${escapeHtml(item.timestamp)} - source=${escapeHtml(item.source)} - status=${escapeHtml(item.status)}
      </div>
      <div class="item-body">${escapeHtml(JSON.stringify(item.payload, null, 2))}</div>
    </div>
  `;
}

async function refreshAll() {
  try {
    const [state, memory, trace, agents, events, live, ready, jobs] = await Promise.all([
      fetchJSON("/api/state"),
      fetchJSON("/api/memory/recent?limit=10"),
      fetchJSON("/api/debug/trace?limit=10"),
      fetchJSON("/api/agents"),
      fetchJSON("/api/events/recent?limit=10"),
      fetchJSON("/health/live"),
      fetchJSON("/health/ready"),
      fetchJSON("/api/scheduler/jobs"),
    ]);

    renderKV(statePanel, {
      focus: state.focus,
      mode: state.mode,
      pending_events: state.pending_events,
      pending_results: state.pending_results,
      scheduler_running: state.scheduler_running,
      last_selected_agent: state.last_selected_agent,
      last_loop_id: state.last_loop_id,
      last_loop_at: state.last_loop_at,
      last_snapshot_at: state.last_snapshot_at,
      last_proactive_reason: state.last_proactive_reason,
    });

    lastReply.textContent = state.last_reply || "";

    renderList(memoryPanel, memory.items, renderMemoryItem);
    renderList(tracePanel, trace.items, renderTraceItem);
    renderList(eventPanel, events.items, renderEventItem);

    agentsPanel.textContent = JSON.stringify(agents, null, 2);
    healthPanel.textContent = JSON.stringify({ live, ready, scheduler_jobs: jobs.jobs }, null, 2);

    chatStatus.textContent = "System refreshed.";
    chatStatus.className = "hint ok";
  } catch (err) {
    chatStatus.textContent = `Refresh failed: ${err.message}`;
    chatStatus.className = "hint error";
  }
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;

  try {
    chatStatus.textContent = "Message processing...";
    chatStatus.className = "hint";

    const result = await fetchJSON("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text }),
    });

    chatInput.value = "";

    if (result.completed) {
      lastReply.textContent = result.reply || "";
      chatStatus.textContent = `Completed · agent=${result.selected_agent} · loop=${result.loop_id}`;
      chatStatus.className = "hint ok";
    } else {
      chatStatus.textContent = "Queued but timed out waiting for result.";
      chatStatus.className = "hint";
    }

    setTimeout(refreshAll, 150);
    setTimeout(refreshAll, 800);
  } catch (err) {
    chatStatus.textContent = `Send failed: ${err.message}`;
    chatStatus.className = "hint error";
  }
});

refreshBtn.addEventListener("click", refreshAll);

refreshAll();
setInterval(refreshAll, 5000);
