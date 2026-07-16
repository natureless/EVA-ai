// EVA Dashboard — reactive telemetry with WebSocket + REST
const $ = (id) => document.getElementById(id);

// ── panels ──────────────────────────────────────────────
const chatForm   = $("chatForm");
const chatInput  = $("chatInput");
const lastReply  = $("lastReply");
const replyMeta  = $("replyMeta");
const refreshBtn = $("refreshBtn");
const wsIndicator = $("wsIndicator");
const autoRefresh = $("autoRefresh");
const policyDot   = $("policyDot");
const healthScore = $("healthScore");
const entityCount = $("entityCount");
const wsEventPanel = $("wsEventPanel");

// ── helpers ─────────────────────────────────────────────
function esc(v) {
  return String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}

async function GET(url) {
  const r = await fetch(url);
  return r.ok ? r.json() : null;
}

function kv(target, data) {
  if (!data || Object.keys(data).length === 0) { target.innerHTML = '<div class="dim">--</div>'; return; }
  target.innerHTML = Object.entries(data).map(([k,v]) => {
    const d = typeof v === "object" ? esc(JSON.stringify(v)) : esc(v);
    return `<div class="kv-key">${esc(k)}</div><div class="kv-value">${d}</div>`;
  }).join("");
}

function list(target, items, fmt) {
  if (!items || items.length === 0) { target.innerHTML = '<div class="dim">No data</div>'; return; }
  target.innerHTML = items.map(fmt).join("");
}

// ── REST refresh ────────────────────────────────────────
async function refreshAll() {
  try {
    // memory tiers
    const tiers = await GET("/api/memory/tiers");
    if (tiers) {
      const s1=tiers.S1_session, s2=tiers.S2_working, s3=tiers.S3_long_term, s4=tiers.S4_world_model, s5=tiers.S5_event_trace;
      kv($("tiersPanel"), {
        "S1 Session": `${s1.entries}/${s1.max_entries}  hits=${s1.hit_count}`,
        "S2 Working": `${s2.entries}/${s2.max_entries}  ttl=${s2.ttl_hours}h`,
        "S3 Long-term": `${s3.entries_active} active / ${s3.entries_archived} archived`,
        "S4 World": `${s4.entities} entities / ${s4.edges} edges`,
        "S5 Events": `${s5.events} events / ${s5.traces} traces`,
      });
    }

    // world model entities
    const world = await GET("/api/memory/world?limit=30");
    if (world) {
      entityCount.textContent = world.entities.length;
      list($("worldPanel"), world.entities, e => `
        <div class="item">
          <span class="tag ${e.type}">${esc(e.type)}</span>
          <span class="item-title">${esc(e.name)}</span>
          <span class="dim">updated ${esc((e.updated_at||"").slice(0,16))}</span>
        </div>`);
    }

    // policy
    const policy = await GET("/api/policy/state");
    if (policy) {
      const cls = policy.state === "quarantined" ? "quarantined" : policy.state === "dormant" ? "dormant" : "commanded";
      policyDot.className = `dot ${cls}`;
      kv($("policyPanel"), {
        State: policy.state,
        "Since (s)": policy.state_since_seconds,
        "History": policy.history_size,
        "Tokens": policy.active_tokens,
      });
    }

    // health
    const diag = await GET("/health/diagnostic");
    if (diag) {
      healthScore.textContent = `${diag.score}% ${diag.overall}`;
      healthScore.className = `badge ${diag.overall}`;
      kv($("healthPanel"), { Score: diag.score, Overall: diag.overall, Failed: diag.failed_count });
    }

    // system state
    const state = await GET("/api/state");
    if (state) {
      kv($("statePanel"), {
        focus: esc(state.focus||"idle"),
        mode: state.mode,
        pending_events: state.pending_events,
        pending_results: state.pending_results,
        last_agent: state.last_selected_agent,
        last_loop: state.last_loop_at,
      });
    }

    // audit
    const audit = await GET("/api/executors/audit/replay?limit=15");
    if (audit && audit.timeline) {
      list($("auditPanel"), audit.timeline.slice(0, 10), item => `
        <div class="item">
          <span class="tag ${item.status}">${esc(item.status)}</span>
          <span class="item-title">${esc(item.executor)} / ${esc(item.action)}</span>
          <span class="dim">${esc(item.timestamp)}  ${item.duration_ms}ms</span>
        </div>`);
    }

    // agents
    const agents = await GET("/api/agents");
    const jobs = await GET("/api/scheduler/jobs");
    if (agents) {
      kv($("agentsPanel"), {
        Agents: agents.agents?.join(", ") || "--",
        "Scheduler Jobs": jobs?.jobs?.length || 0,
      });
    }

  } catch (e) {
    console.error("refresh error", e);
  }
}

// ── WebSocket ────────────────────────────────────────────
let wsReconnectTimer = null;
const wsEvents = [];

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => {
    wsIndicator.textContent = "WS";
    wsIndicator.className = "badge ok";
    autoRefresh.textContent = "Live";
    autoRefresh.className = "badge ok";
    if (wsReconnectTimer) { clearInterval(wsReconnectTimer); wsReconnectTimer = null; }
  };
  ws.onmessage = (evt) => {
    try {
      const data = JSON.parse(evt.data);
      if (data.pong) return;
      wsEvents.unshift(data);
      if (wsEvents.length > 50) wsEvents.length = 50;

      // render WS events
      list(wsEventPanel, wsEvents.slice(0, 8), event => `
        <div class="item">
          <span class="tag channel">${esc(event.channel)}</span>
          <span class="item-title">${esc(JSON.stringify(event.payload).slice(0, 80))}</span>
          <span class="dim">${new Date((event.ts||0)*1000).toLocaleTimeString()}</span>
        </div>`);

      // policy real-time update
      if (data.channel === "policy_state" && data.payload) {
        const p = data.payload.state_machine || data.payload;
        if (p.current) {
          policyDot.className = `dot ${p.current}`;
        }
      }

      // entity count bump
      if (data.channel === "entity_created") {
        const n = parseInt(entityCount.textContent) || 0;
        entityCount.textContent = n + 1;
        entityCount.className = "badge pulse";
        setTimeout(() => { entityCount.className = "badge"; }, 800);
      }
    } catch (e) {
      console.error("ws parse error", e);
    }
  };
  ws.onclose = () => {
    wsIndicator.textContent = "WS";
    wsIndicator.className = "badge disconnected";
    autoRefresh.textContent = "REST";
    autoRefresh.className = "badge";
    // reconnect in 3s
    if (!wsReconnectTimer) wsReconnectTimer = setInterval(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}
connectWS();

// ── Chat ─────────────────────────────────────────────────
chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const result = await r.json();
    lastReply.textContent = result.reply || "";
    replyMeta.textContent = `agent=${result.selected_agent} loop=${result.loop_id} ${result.duration_ms||0}ms`;
    setTimeout(refreshAll, 300);
  } catch (err) {
    lastReply.textContent = `Error: ${err.message}`;
  }
});

// ── init ─────────────────────────────────────────────────
refreshBtn.addEventListener("click", refreshAll);
refreshAll();
setInterval(refreshAll, 8000);  // REST polling every 8s (WS covers real-time)
