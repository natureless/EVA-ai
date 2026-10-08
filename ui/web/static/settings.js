// EVA Settings — dashboard polling and rendering extracted from app.js
const $ = (id) => document.getElementById(id);

// ── i18n key mapping for API-field labels ───────────────
const _LABEL_KEYS = {
  "State":              "policy.state",
  "Since (s)":          "policy.since",
  "History size":       "policy.history",
  "Active tokens":      "policy.tokens",
  "focus":              "state.focus",
  "mode":               "state.mode",
  "pending_events":     "state.pending_events",
  "pending_results":    "state.pending_results",
  "last_agent":         "state.last_agent",
  "last_loop":          "state.last_loop",
  "Score":              "health.score",
  "Overall":            "health.overall",
  "Failed":             "health.failed",
  "Agents":             "agents.list",
  "Scheduler Jobs":     "agents.jobs",
};

function _i18nLabel(key) {
  return _LABEL_KEYS[key] ? I18N.t(_LABEL_KEYS[key]) : key;
}

// ── Helpers ─────────────────────────────────────────────
function esc(v) {
  return String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
async function GET(url) {
  const r = await fetch(url);
  return r.ok ? r.json() : null;
}

function kv(target, data) {
  if (!data || Object.keys(data).length === 0) {
    target.innerHTML = `<div class="empty-state">${I18N.t("common.noData")}</div>`;
    return;
  }
  target.innerHTML = Object.entries(data).map(([k,v]) => {
    const d = typeof v === "object" ? esc(JSON.stringify(v)) : esc(v);
    return `<div class="kv-key">${esc(_i18nLabel(k))}</div><div class="kv-value">${d}</div>`;
  }).join("");
}

function list(target, items, fmt) {
  if (!items || items.length === 0) {
    target.innerHTML = `<div class="empty-state">${I18N.t("common.noData")}</div>`;
    return;
  }
  target.innerHTML = items.map(fmt).join("");
}

function progressRows(target, rows) {
  target.innerHTML = rows.map(r => `
    <div class="progress-row">
      <div class="pr-label"><span>${esc(I18N.t(r.i18nKey) || r.label)}</span><span>${esc(r.used)}/${esc(r.max)}</span></div>
      <div class="progress-bar"><div class="fill ${r.color||'accent'}" style="width:${r.pct}%"></div></div>
    </div>`).join("");
}

function compHealth(target, comps) {
  target.innerHTML = Object.entries(comps).map(([k,v]) => {
    const cls = typeof v === "boolean" ? (v ? "up" : "down") : "down";
    const statusText = v ? I18N.t("health.up") : I18N.t("health.down");
    const dotCls = v ? "ok" : "error";
    return `<div class="comp-row">
      <span><span class="dot ${dotCls}"></span>${esc(k)}</span>
      <span class="dim">${statusText}</span>
    </div>`;
  }).join("");
}

// ── Entity rendering ────────────────────────────────────
let _allEntities = [];

function renderEntities(entities) {
  const el = $("entityCount");
  if (el) el.textContent = entities.length;
  list($("worldPanel"), entities, e => `
    <div class="list-item">
      <span class="li-main"><span class="tag ${e.type||'default'}">${esc(e.type)}</span> ${esc(e.name)}</span>
      <span class="li-meta">${esc((e.updated_at||"").slice(0,16))}</span>
    </div>`);
}

// ── REST refresh ────────────────────────────────────────
async function refreshAll() {
  try {
    // ── memory tiers ──
    const tiers = await GET("/api/memory/tiers");
    if (tiers) {
      const s1=tiers.S1_session, s2=tiers.S2_working, s3=tiers.S3_long_term,
            s4=tiers.S4_world_model, s5=tiers.S5_event_trace;
      const totalMem = (s3.entries_active||0) + (s2.entries||0) + (s1.entries||0);
      const memEl = $("memSummary");
      if (memEl) memEl.textContent = totalMem + I18N.t("memory.entries");
      progressRows($("tiersPanel"), [
        { label:"S1 Session",   i18nKey:"memory.s1", used:s1.entries, max:s1.max_entries, pct:Math.round(s1.entries/s1.max_entries*100), color:"blue" },
        { label:"S2 Working",   i18nKey:"memory.s2", used:s2.entries, max:s2.max_entries, pct:Math.round(s2.entries/s2.max_entries*100), color:"accent" },
        { label:"S3 Long-term", i18nKey:"memory.s3", used:s3.entries_active, max:s3.max_entries, pct:Math.round(s3.entries_active/s3.max_entries*100), color:"green" },
        { label:"S4 Entities",  i18nKey:"memory.s4e", used:s4.entities, max:"--", pct:Math.min(s4.entities||0,100), color:"yellow" },
        { label:"S4 Edges",     i18nKey:"memory.s4r", used:s4.edges,    max:"--", pct:Math.min(s4.edges||0,100), color:"yellow" },
        { label:"S5 Events",    i18nKey:"memory.s5",  used:s5.events,   max:"--", pct:Math.min(s5.events||0,100), color:"green" },
      ]);
    }

    // ── world model ──
    const world = await GET("/api/memory/world?limit=50");
    if (world) {
      _allEntities = world.entities || [];
      renderEntities(_allEntities);
    }

    // ── policy ──
    const policy = await GET("/api/policy/state");
    const policyDot = $("policyDot");
    if (policy && policyDot) {
      const cls = policy.state === "quarantined" ? "quarantined"
        : policy.state === "supervised" ? "supervised"
        : policy.state === "dormant" ? "dormant" : "commanded";
      policyDot.className = `dot ${cls}`;
      kv($("policyPanel"), {
        "State": I18N.t("policy." + policy.state) || policy.state,
        "Since (s)": policy.state_since_seconds,
        "History size": policy.history_size,
        "Active tokens": policy.active_tokens,
      });
    }

    // ── policy timeline ──
    const policyDetail = await GET("/api/policy/state?detail=true");
    if (policyDetail && policyDetail.state_machine && policyDetail.state_machine.history) {
      const hist = policyDetail.state_machine.history.slice(-30);
      const tl = $("policyTimeline");
      if (tl) tl.innerHTML = hist.map(h => {
        const tc = h.to === "quarantined" ? "quarantined"
          : h.to === "supervised" ? "supervised"
          : h.to === "commanded" ? "commanded" : "dormant";
        return `<span class="timeline-dot ${tc}" title="${esc(h.from)}→${esc(h.to)} via ${esc(h.trigger)}"></span>`;
      }).join("");
    }

    // ── health ──
    const diag = await GET("/health/diagnostic");
    const healthScore = $("healthScore");
    if (diag && healthScore) {
      const overallLabel = I18N.t("health." + diag.overall) || diag.overall;
      healthScore.textContent = `${diag.score}% ${overallLabel}`;
      healthScore.className = `badge ${diag.overall}`;
      kv($("healthPanel"), { "Score": diag.score, "Overall": overallLabel, "Failed": diag.failed_count });
      const ready = await GET("/health/ready");
      if (ready && ready.components) {
        compHealth($("healthComponents"), ready.components);
      }
    }

    // ── system state ──
    const state = await GET("/api/state");
    if (state) {
      kv($("statePanel"), {
        "focus": esc(state.focus||"idle"),
        "mode": state.mode,
        "pending_events": state.pending_events,
        "pending_results": state.pending_results,
        "last_agent": state.last_selected_agent,
        "last_loop": state.last_loop_at,
      });
    }

    // ── audit ──
    const audit = await GET("/api/executors/audit/replay?limit=15");
    if (audit && audit.timeline) {
      list($("auditPanel"), audit.timeline.slice(0, 10), item => `
        <div class="list-item">
          <span class="li-main"><span class="tag ${item.status}">${esc(item.status)}</span> ${esc(item.executor)} / ${esc(item.action)}</span>
          <span class="li-meta">${esc(item.timestamp)} ${item.duration_ms}ms</span>
        </div>`);
    }

    // ── agents ──
    const agents = await GET("/api/agents");
    const jobs = await GET("/api/scheduler/jobs");
    if (agents) {
      kv($("agentsPanel"), {
        "Agents": agents.agents?.join(", ") || "--",
        "Scheduler Jobs": jobs?.jobs?.length || 0,
      });
    }

  } catch (e) {
    console.error("settings refresh error", e);
  }
}

// ── WebSocket events ────────────────────────────────────
const wsEvents = [];
window.addEventListener("ws-message", (evt) => {
  const data = evt.detail;
  wsEvents.unshift(data);
  if (wsEvents.length > 50) wsEvents.length = 50;
  const cnt = $("wsEventCount");
  if (cnt) cnt.textContent = wsEvents.length;

  const panel = $("wsEventPanel");
  if (panel) {
    list(panel, wsEvents.slice(0, 8), event => `
      <div class="list-item">
        <span class="li-main"><span class="tag channel">${esc(event.channel||"")}</span> ${esc(JSON.stringify(event.payload||"").slice(0, 80))}</span>
        <span class="li-meta">${new Date((event.ts||0)*1000).toLocaleTimeString()}</span>
      </div>`);
  }

  if (data.channel === "policy_state" && data.payload) {
    const p = data.payload.state_machine || data.payload;
    const policyDot = $("policyDot");
    if (p.current && policyDot) {
      const cls = p.current === "quarantined" ? "quarantined"
        : p.current === "supervised" ? "supervised"
        : p.current === "commanded" ? "commanded" : "dormant";
      policyDot.className = `dot ${cls}`;
    }
  }

  if (data.channel === "entity_created") {
    const entityCount = $("entityCount");
    if (entityCount) {
      const n = parseInt(entityCount.textContent) || 0;
      entityCount.textContent = n + 1;
      entityCount.className = "badge pulse";
      setTimeout(() => { entityCount.className = "badge"; }, 800);
    }
  }
});

// ── Language change handler ─────────────────────────────
function renderRuntimePreview() {
  document.querySelectorAll("#runtimeWorkspace .card-body").forEach(panel => {
    panel.textContent = I18N.t("ui.runtimePreview");
  });
}
window.addEventListener("lang-changed", () => {
  if (document.body.dataset.preview === "true") renderRuntimePreview();
  else refreshAll();
});

// ── Card collapse ──────────────────────────────────────
document.querySelectorAll(".card-header[data-card]").forEach(h => {
  h.addEventListener("click", (e) => {
    if (e.target.closest("button") || e.target.closest("input") || e.target.closest("label")) return;
    h.parentElement.classList.toggle("collapsed");
  });
});

// ── Entity filter ──────────────────────────────────────
const entityFilter = $("entityFilter");
if (entityFilter) {
  entityFilter.addEventListener("input", () => {
    const q = entityFilter.value.toLowerCase();
    renderEntities(q ? _allEntities.filter(e =>
      e.name.toLowerCase().includes(q) || e.type.toLowerCase().includes(q)
    ) : _allEntities);
  });
}

// ── Init ────────────────────────────────────────────────
if (document.body.dataset.preview === "true") {
  Nav._bindTheme();
  Nav._bindLang();
  renderRuntimePreview();
} else {
  Nav.init();
  refreshAll();
  setInterval(refreshAll, 8000);
}
