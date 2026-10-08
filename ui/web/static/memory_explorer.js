// EVA Memory Explorer — one owner for every browse/search result.
const MemoryExplorer = {
  _tiers: [],
  _activeTier: null,
  _initialized: false,
  _tierLoading: false,
  _overviewLoaded: false,
  _tierError: null,
  _result: null,
  _resultSerial: 0,
  _resultController: null,

  _text(zh, en) { return typeof I18N !== 'undefined' && I18N._lang === 'en' ? en : zh; },

  _worldOriginText(entry) {
    const origin = entry.field_provenance?.name || entry.provenance || {};
    const labels = {
      verified_fact: ['事实声明','Fact claim'], user_statement: ['用户陈述','User statement'],
      working_model: ['工作模型','Working model'], hypothesis: ['假设','Hypothesis'],
      assistant_inference: ['助手推断','Assistant inference'], tool_observation: ['工具观察','Tool observation'],
      simulation: ['模拟结果','Simulation'], unknown: ['未知','Unknown'],
    };
    return this._text('名称来源：','Name origin: ') + this._text(...(labels[origin.epistemic_status] || labels.unknown)) + (origin.source ? ` · ${origin.source}` : '');
  },

  init() {
    if (this._initialized) return;
    this._initialized = true;
    this._bindSearch();
    document.getElementById('memTierRetry')?.addEventListener('click', () => this._loadTiers());
    document.getElementById('memResultsRetry')?.addEventListener('click', () => {
      if (this._result) return this._runSelection(this._result.selection);
    });
    window.addEventListener('lang-changed', () => {
      this._renderLabels();
      this._renderTiers();
      this._renderTierStatus();
      this._renderResult();
    });
    this._renderLabels();
    const poll = async () => {
      await this._loadTiers();
      // Schedule after completion; a slow request never accumulates polls.
      this._pollTimer = setTimeout(poll, 30000);
    };
    poll();
  },

  _renderLabels() {
    const input = document.getElementById('memSearch');
    if (input) {
      input.placeholder = this._text('搜索所有记忆层级…','Search across all memory tiers…');
      input.setAttribute('aria-label', this._text('搜索记忆','Search memory'));
    }
    document.getElementById('memSearchBtn').textContent = this._text('搜索','Search');
    for (const id of ['memTierRetry','memResultsRetry']) {
      const button = document.getElementById(id);
      if (button) button.textContent = this._text('重试','Retry');
    }
    this._renderResult();
  },

  async _read(url, options = {}, controller = new AbortController()) {
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 8000);
    try {
      return await EvaHttp.json(url, { ...options, signal: controller.signal, cache: 'no-store' });
    } catch (error) {
      if (timedOut) { const timeout = new Error('timeout'); timeout.name = 'ReadTimeout'; throw timeout; }
      throw error;
    } finally { clearTimeout(timer); }
  },

  _failure(error) {
    if (error.name === 'LoginRequired') return this._text('登录需要更新，请重新登录。','Please sign in again.');
    if (error.name === 'ReadTimeout') return this._text('读取超时，请重试。','Read timed out. Please retry.');
    if (error.status) return this._text(`读取失败（HTTP ${error.status}），请重试。`,`Read failed (HTTP ${error.status}). Please retry.`);
    return this._text('无法读取记忆数据，请检查连接后重试。','Could not read memory data. Check the connection and retry.');
  },

  _status(id, retryId, text, error) {
    const status = document.getElementById(id), retry = document.getElementById(retryId);
    if (status) status.textContent = text;
    if (retry) retry.hidden = !error || error.name === 'LoginRequired';
  },

  async _loadTiers() {
    const el = document.getElementById('memTiers');
    if (!el || this._tierLoading) return;
    this._tierLoading = true;
    el.setAttribute('aria-busy', 'true');
    try {
      const data = await this._read('/api/memory/tiers?format=explorer');
      if (!Array.isArray(data.tiers) || data.tiers.some(t => !/^S[1-5]$/.test(t.name) || !Number.isFinite(t.entries) || t.entries < 0)) throw new Error('Invalid tiers');
      this._tiers = data.tiers;
      this._overviewLoaded = true;
      this._tierError = null;
      this._renderTiers();
    } catch (error) {
      this._tierError = error;
      if (!this._overviewLoaded) el.innerHTML = '';
    } finally {
      this._tierLoading = false;
      el.setAttribute('aria-busy', 'false');
      this._renderTierStatus();
    }
  },

  _renderTierStatus() {
    const text = this._tierError ? this._failure(this._tierError) + (this._overviewLoaded ? this._text(' 显示上次读取的数据。',' Showing the last successful data.') : '') : '';
    this._status('memTierStatus', 'memTierRetry', text, this._tierError);
  },

  _renderTiers() {
    const el = document.getElementById('memTiers');
    if (!el) return;
    if (!this._overviewLoaded) {
      if (!this._tierError) el.innerHTML = `<div class="loading">${this._text('正在读取记忆层级…','Loading memory tiers…')}</div>`;
      return;
    }
    const focusTier = el.contains(document.activeElement) ? document.activeElement.dataset.tier : null;
    el.innerHTML = this._tiers.length ? this._tiers.map(t => {
      const pct = t.max ? Math.min(100, (t.entries / t.max) * 100) : 100;
      const pctCls = pct > 90 ? 'full' : pct > 60 ? 'warn' : 'ok';
      const labels = { S1:['会话','Session'], S2:['工作','Working'], S3:['长期','Long-term'], S4:['世界','World'], S5:['事件','Events'] };
      const extra = [];
      if (t.edges !== undefined) extra.push(this._text(`${t.edges} 条关系`,`${t.edges} edges`));
      if (t.events !== undefined) extra.push(this._text(`${t.events} 条事件 · ${t.traces} 条轨迹`,`${t.events} events · ${t.traces} traces`));
      if (t.hits !== undefined) extra.push(this._text(`${t.hits} 次命中 · ${t.misses} 次未命中`,`${t.hits} hits · ${t.misses} misses`));
      if (t.expired !== undefined) extra.push(this._text(`${t.expired} 条已过期`,`${t.expired} expired`));
      if (Number.isFinite(t.avg_importance)) extra.push(this._text('平均重要度：','Average importance: ') + t.avg_importance.toFixed(2));
      return `<button type="button" data-tier="${t.name}" aria-pressed="${this._activeTier === t.name}" class="mem-tier-card ${this._activeTier === t.name ? 'active' : ''}"
        style="border-left:3px solid var(--ui-tier-${t.name.toLowerCase()});background:var(--ui-surface)" onclick="MemoryExplorer._selectTier('${t.name}')">
        <div class="tier-header"><span class="tier-badge" style="background:var(--ui-tier-${t.name.toLowerCase()});color:var(--ui-panel)">${t.name}</span><span class="tier-label">${this._text(...labels[t.name])}</span></div>
        <div class="tier-count">${t.entries.toLocaleString()}<span class="tier-max">${t.max ? ' / ' + Number(t.max).toLocaleString() : ''}</span></div>
        <div class="tier-bar"><div class="tier-fill ${pctCls}" style="width:${pct}%"></div></div>
        <div class="tier-meta">${esc(t.ttl)} · ${esc(t.storage)}${extra.length ? ' · ' + esc(extra.join(' · ')) : ''}</div>
      </button>`;
    }).join('') : `<div class="empty-state">${this._text('暂无层级数据','No tier data available')}</div>`;
    if (focusTier) [...el.querySelectorAll('[data-tier]')].find(button => button.dataset.tier === focusTier)?.focus();
  },

  _selectTier(name) {
    if (!/^S[1-5]$/.test(name)) return;
    return this._runSelection({ tier: name });
  },

  async _runSelection(selection) {
    const serial = ++this._resultSerial;
    this._resultController?.abort();
    const controller = this._resultController = new AbortController();
    this._activeTier = selection.tier || null;
    this._result = { selection, status: 'loading' };
    this._renderTiers();
    this._renderResult();
    try {
      const data = selection.tier
        ? await this._read(`/api/memory/entries/${selection.tier}?limit=50`, {}, controller)
        : await this._read('/api/memory/search', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:selection.query,tiers:['S1','S2','S3','S4'],limit:30}) }, controller);
      if (serial !== this._resultSerial) return;
      let entries;
      if (selection.tier) {
        if (!Array.isArray(data.entries)) throw new Error('Invalid entries');
        entries = data.entries;
      } else {
        if (!data.results || typeof data.results !== 'object' || Array.isArray(data.results) || Object.values(data.results).some(v => !Array.isArray(v))) throw new Error('Invalid results');
        entries = Object.entries(data.results).flatMap(([tier, rows]) => rows.map(e => ({...e,_tier:tier})));
      }
      if (!Number.isFinite(data.total) || data.total < 0) throw new Error('Invalid count');
      this._result = { selection, status:'ready', entries, total:data.total };
    } catch (error) {
      if (serial !== this._resultSerial) return;
      this._result = { selection, status:'error', error };
    } finally {
      if (serial === this._resultSerial) {
        this._resultController = null;
        this._renderResult();
      }
    }
  },

  _renderResult() {
    const title = document.getElementById('memResultsTitle'), count = document.getElementById('memResultsCount'), el = document.getElementById('memEntries');
    const result = this._result;
    if (!result) {
      title.textContent = this._text('选择一个记忆层级','Select a tier to browse');
      el.innerHTML = `<div class="empty-state">${this._text('点击上方层级卡片浏览记录','Click a tier card above to browse entries')}</div>`;
      return;
    }
    const selection = result.selection;
    title.textContent = selection.tier ? this._text(`浏览 ${selection.tier}`,`Browsing ${selection.tier}`) : this._text(`搜索：“${selection.query}”`,`Search: "${selection.query}"`);
    count.textContent = result.status === 'ready' ? this._text(`${result.total} 条${selection.tier ? '记录' : '结果'}`,`${result.total} ${selection.tier ? 'entries' : 'results'}`) : '';
    el.setAttribute('aria-busy', String(result.status === 'loading'));
    this._status('memResultsStatus','memResultsRetry',result.error ? this._failure(result.error) : '',result.error);
    if (result.status === 'loading') el.innerHTML = `<div class="loading">${this._text('正在读取…','Loading…')}</div>`;
    else if (result.status === 'error') el.innerHTML = '';
    else if (selection.tier) this._renderEntries(selection.tier, result.entries);
    else if (!result.entries.length) el.innerHTML = `<div class="empty-state">${this._text('没有找到相关记忆','No results found')}</div>`;
    else el.innerHTML = result.entries.map(e => `<div class="mem-entry search-result"><span class="tier-badge">${esc(e._tier)}</span>
      <div class="entry-content">${esc((e.content || e.name || '').substring(0,300))}</div><div class="entry-footer"><span class="entry-source">${esc(e.source || e.category || e.type || '')}</span>
      ${e._tier === 'S4' ? `<span class="entry-meta">${esc(this._worldOriginText(e))}</span>` : ''}
      ${e.importance !== undefined ? `<span class="entry-imp">${this._text('重要度：','Importance: ')}${Number(e.importance).toFixed(2)}</span>` : ''}</div></div>`).join('');
  },

  // ── Render entries ─────────────────────────────────────────
  _renderEntries(tier, entries) {
    const el = document.getElementById("memEntries");
    if (!entries || entries.length === 0) {
      el.innerHTML = `<div class="empty-state">${this._text('此层级暂无记录', 'No entries in this tier')}</div>`;
      return;
    }

    if (tier === "S4") {
      el.innerHTML = entries.map(e => `
        <div class="mem-entry s4-entry">
          <span class="entry-type">${esc(e.type)}</span>
          <span class="entry-name">${esc(e.name)}</span>
          <span class="entry-source">${esc(this._worldOriginText(e))}</span>
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

  _bindSearch() {
    const input = document.getElementById('memSearch'), button = document.getElementById('memSearchBtn');
    if (!input || !button) return;
    const search = () => {
      const query = input.value.trim();
      if (query) return this._runSelection({ query });
    };
    button.addEventListener('click', search);
    input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.isComposing) search(); });
  },
};

function esc(value) {
  return String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
}

Nav.init();
MemoryExplorer.init();
