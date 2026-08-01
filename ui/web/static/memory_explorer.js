// EVA Memory Explorer — browse and search the five-tier memory system
const MemoryExplorer = {
  _tiers: [],
  _activeTier: null,

  init() {
    this._bindSearch();
    this._loadTiers();
    // Auto-refresh every 30s
    setInterval(() => this._loadTiers(), 30000);
  },

  // ── Load tier overview ────────────────────────────────────
  async _loadTiers() {
    const el = document.getElementById("memTiers");
    if (!el) return;
    try {
      const r = await fetch("/api/memory/tiers?format=explorer");
      const data = await r.json();
      this._tiers = data.tiers || [];
      this._renderTiers();
    } catch (e) {
      el.innerHTML = `<div class="empty-state">Failed to load: ${e.message}</div>`;
    }
  },

  _renderTiers() {
    const el = document.getElementById("memTiers");
    const colors = {
      S1: { bg: "#eff6ff", border: "#60a5fa", label: "Session" },
      S2: { bg: "#ecfdf5", border: "#34d399", label: "Working" },
      S3: { bg: "#fffbeb", border: "#fbbf24", label: "Long-term" },
      S4: { bg: "#fef2f2", border: "#f87171", label: "World" },
      S5: { bg: "#f5f3ff", border: "#a78bfa", label: "Events" },
    };

    el.innerHTML = this._tiers.map(t => {
      const c = colors[t.name] || colors.S1;
      const pct = t.max ? Math.min(100, (t.entries / t.max) * 100) : 100;
      const pctCls = pct > 90 ? "full" : pct > 60 ? "warn" : "ok";
      const extra = [];
      if (t.edges !== undefined) extra.push(`${t.edges} edges`);
      if (t.events !== undefined) extra.push(`${t.events} events, ${t.traces} traces`);
      if (t.hits !== undefined) extra.push(`${t.hits} hits, ${t.misses} misses`);
      if (t.expired !== undefined) extra.push(`${t.expired} expired`);
      if (t.avg_importance !== undefined) extra.push(`avg imp: ${t.avg_importance.toFixed(2)}`);

      return `<div class="mem-tier-card ${this._activeTier === t.name ? 'active' : ''}"
                   style="border-left: 3px solid ${c.border}; background: ${c.bg}"
                   onclick="MemoryExplorer._selectTier('${t.name}')">
        <div class="tier-header">
          <span class="tier-badge" style="background:${c.border};color:#fff">${t.name}</span>
          <span class="tier-label">${t.label}</span>
        </div>
        <div class="tier-count">${t.entries.toLocaleString()}<span class="tier-max">${t.max ? ' / ' + t.max.toLocaleString() : ''}</span></div>
        <div class="tier-bar"><div class="tier-fill ${pctCls}" style="width:${pct}%"></div></div>
        <div class="tier-meta">${t.ttl} · ${t.storage}${extra.length ? ' · ' + extra.join(' · ') : ''}</div>
      </div>`;
    }).join("");
  },

  // ── Select a tier to browse ────────────────────────────────
  async _selectTier(name) {
    this._activeTier = name;
    this._renderTiers();
    document.getElementById("memResultsTitle").textContent = `Browsing ${name}`;
    document.getElementById("memResultsCount").textContent = "";
    document.getElementById("memEntries").innerHTML = '<div class="loading">Loading…</div>';

    try {
      const r = await fetch(`/api/memory/entries/${name}?limit=50`);
      const data = await r.json();
      document.getElementById("memResultsCount").textContent = `${data.total || data.entries.length} entries`;
      this._renderEntries(name, data.entries);
    } catch (e) {
      document.getElementById("memEntries").innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
    }
  },

  // ── Render entries ─────────────────────────────────────────
  _renderEntries(tier, entries) {
    const el = document.getElementById("memEntries");
    if (!entries || entries.length === 0) {
      el.innerHTML = '<div class="empty-state">No entries in this tier</div>';
      return;
    }

    if (tier === "S4") {
      el.innerHTML = entries.map(e => `
        <div class="mem-entry s4-entry">
          <span class="entry-type">${esc(e.type)}</span>
          <span class="entry-name">${esc(e.name)}</span>
          <span class="entry-meta">${esc(e.updated_at || "")}</span>
        </div>`).join("");
    } else if (tier === "S5") {
      el.innerHTML = entries.map(e => `
        <div class="mem-entry s5-entry">
          <span class="entry-type">${esc(e.type)}</span>
          <span class="entry-source">${esc(e.source)}</span>
          <span class="entry-status ${e.status}">${esc(e.status)}</span>
          <span class="entry-meta">${esc(e.timestamp || "")}</span>
        </div>`).join("");
    } else {
      el.innerHTML = entries.map(e => `
        <div class="mem-entry">
          <div class="entry-content">${esc((e.content || e.summary || "").substring(0, 300))}</div>
          <div class="entry-footer">
            <span class="entry-source">${esc(e.source || e.category || "")}</span>
            ${e.importance !== undefined ? `<span class="entry-imp">imp: ${Number(e.importance).toFixed(2)}</span>` : ""}
            ${e.priority !== undefined ? `<span class="entry-prio">pri: ${e.priority}</span>` : ""}
            <span class="entry-meta">${esc(e.created_at || e.ts || "")}</span>
          </div>
        </div>`).join("");
    }
  },

  // ── Search ─────────────────────────────────────────────────
  _bindSearch() {
    const input = document.getElementById("memSearch");
    const btn = document.getElementById("memSearchBtn");
    if (!input || !btn) return;

    const doSearch = async () => {
      const query = input.value.trim();
      if (!query) return;

      document.getElementById("memResultsTitle").textContent = `Search: "${query}"`;
      document.getElementById("memResultsCount").textContent = "";
      document.getElementById("memEntries").innerHTML = '<div class="loading">Searching…</div>';

      try {
        const r = await fetch("/api/memory/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query, tiers: ["S1", "S2", "S3", "S4"], limit: 30 }),
        });
        const data = await r.json();
        document.getElementById("memResultsCount").textContent = `${data.total} results`;

        // Flatten results from all tiers
        const all = [];
        for (const [tier, entries] of Object.entries(data.results || {})) {
          for (const e of entries) {
            all.push({ ...e, _tier: tier });
          }
        }
        if (all.length === 0) {
          document.getElementById("memEntries").innerHTML = '<div class="empty-state">No results found</div>';
          return;
        }

        document.getElementById("memEntries").innerHTML = all.map(e => `
          <div class="mem-entry search-result">
            <span class="tier-badge" style="font-size:10px;padding:1px 6px">${e._tier}</span>
            <div class="entry-content">${esc((e.content || e.name || "").substring(0, 300))}</div>
            <div class="entry-footer">
              <span class="entry-source">${esc(e.source || e.category || e.type || "")}</span>
              ${e.importance !== undefined ? `<span class="entry-imp">imp: ${Number(e.importance).toFixed(2)}</span>` : ""}
            </div>
          </div>`).join("");
      } catch (e) {
        document.getElementById("memEntries").innerHTML = `<div class="empty-state">Search error: ${e.message}</div>`;
      }
    };

    btn.addEventListener("click", doSearch);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") doSearch();
    });
  },
};

function esc(v) {
  return String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

// Init
Nav.init();
MemoryExplorer.init();
