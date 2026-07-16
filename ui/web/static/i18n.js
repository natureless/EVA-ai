// EVA i18n — Chinese / English translation system
const I18N = {
  _lang: localStorage.getItem("eva-lang") || "zh",

  _dict: {
    // ── Topbar ──
    "app.subtitle":       { zh: "实时遥测 · WebSocket · REST · SSE", en: "Live Telemetry · WebSocket · REST · SSE" },
    "btn.refresh":        { zh: "刷新", en: "Refresh" },
    "btn.theme":          { zh: "切换深色/浅色模式", en: "Toggle dark/light mode" },

    // ── Chat ──
    "chat.title":         { zh: "对话", en: "Chat" },
    "chat.hint":          { zh: "POST /api/chat", en: "POST /api/chat" },
    "chat.placeholder":   { zh: "发送消息...", en: "Send a message..." },
    "chat.sse":           { zh: "SSE 流式", en: "SSE Stream" },
    "chat.send":          { zh: "发送", en: "Send" },
    "chat.reply":         { zh: "回复", en: "Reply" },
    "chat.streaming":     { zh: "接收中...", en: "streaming..." },
    "chat.streamed":      { zh: "流式完成", en: "streamed in" },
    "chat.error":         { zh: "错误", en: "error" },

    // ── Policy ──
    "policy.title":       { zh: "策略", en: "Policy" },
    "policy.state":       { zh: "状态", en: "State" },
    "policy.since":       { zh: "持续 (秒)", en: "Since (s)" },
    "policy.history":     { zh: "历史记录", en: "History size" },
    "policy.tokens":      { zh: "活跃令牌", en: "Active tokens" },

    // ── Memory ──
    "memory.title":       { zh: "记忆层级", en: "Memory Tiers" },
    "memory.entries":     { zh: "条记录", en: " entries"},
    "memory.s1":          { zh: "S1 会话", en: "S1 Session" },
    "memory.s2":          { zh: "S2 工作", en: "S2 Working" },
    "memory.s3":          { zh: "S3 长期", en: "S3 Long-term" },
    "memory.s4e":         { zh: "S4 实体", en: "S4 Entities" },
    "memory.s4r":         { zh: "S4 关系", en: "S4 Edges" },
    "memory.s5":          { zh: "S5 事件", en: "S5 Events" },

    // ── World Model ──
    "world.title":        { zh: "世界模型", en: "World Model" },
    "world.filter":       { zh: "筛选实体...", en: "Filter entities..." },

    // ── Audit ──
    "audit.title":        { zh: "审计时间线", en: "Audit Timeline" },

    // ── System State ──
    "state.title":        { zh: "系统状态", en: "System State" },
    "state.focus":        { zh: "焦点", en: "focus" },
    "state.mode":         { zh: "模式", en: "mode" },
    "state.pending_events": { zh: "待处理事件", en: "pending_events" },
    "state.pending_results":{ zh: "待处理结果", en: "pending_results" },
    "state.last_agent":   { zh: "最近智能体", en: "last_agent" },
    "state.last_loop":    { zh: "最近循环", en: "last_loop" },

    // ── Health ──
    "health.title":       { zh: "健康", en: "Health" },
    "health.score":       { zh: "评分", en: "Score" },
    "health.overall":     { zh: "总体", en: "Overall" },
    "health.failed":      { zh: "故障", en: "Failed" },
    "health.up":          { zh: "正常", en: "up" },
    "health.down":        { zh: "异常", en: "down" },

    // ── WS Events ──
    "ws.title":           { zh: "WS 事件", en: "WS Events" },

    // ── Agents ──
    "agents.title":       { zh: "智能体 & 调度器", en: "Agents & Scheduler" },
    "agents.list":        { zh: "智能体", en: "Agents" },
    "agents.jobs":        { zh: "调度任务", en: "Scheduler Jobs" },

    // ── Common ──
    "common.noData":      { zh: "暂无数据", en: "No data" },
    "common.loading":     { zh: "加载中...", en: "Loading..." },

    // ── Health status labels ──
    "health.healthy":     { zh: "健康", en: "healthy" },
    "health.degraded":    { zh: "降级", en: "degraded" },
    "health.critical":    { zh: "严重", en: "critical" },

    // ── WS indicator ──
    "ws.live":            { zh: "实时", en: "Live" },
    "ws.rest":            { zh: "REST", en: "REST" },

    // ── Policy states ──
    "policy.dormant":     { zh: "休眠", en: "dormant" },
    "policy.commanded":   { zh: "指令", en: "commanded" },
    "policy.supervised":  { zh: "监督", en: "supervised" },
    "policy.quarantined": { zh: "隔离", en: "quarantined" },
  },

  // ── Public API ──
  t(key, vars) {
    let s = (this._dict[key] && this._dict[key][this._lang]) || key;
    if (vars) {
      Object.entries(vars).forEach(([k, v]) => { s = s.replace("{" + k + "}", v); });
    }
    return s;
  },

  lang() {
    return this._lang;
  },

  setLang(lang) {
    this._lang = lang;
    localStorage.setItem("eva-lang", lang);
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  },

  toggle() {
    this.setLang(this._lang === "zh" ? "en" : "zh");
  },

  // Apply all data-i18n attributes in the DOM
  applyDOM() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.getAttribute("data-i18n");
      const text = this.t(key);
      if (text && text !== key) el.textContent = text;
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
      const key = el.getAttribute("data-i18n-placeholder");
      const text = this.t(key);
      if (text && text !== key) el.placeholder = text;
    });
    document.querySelectorAll("[data-i18n-title]").forEach(el => {
      const key = el.getAttribute("data-i18n-title");
      const text = this.t(key);
      if (text && text !== key) el.title = text;
    });
  },
};

// Init on load
document.addEventListener("DOMContentLoaded", () => {
  document.documentElement.lang = I18N.lang() === "zh" ? "zh-CN" : "en";
  I18N.applyDOM();
});
