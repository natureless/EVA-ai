// EVA i18n — Chinese / English translation system
const I18N = {
  _lang: (() => { try { return localStorage.getItem("eva-lang") === "en" ? "en" : "zh"; } catch (_) { return "zh"; } })(),

  _dict: {
    "ui.geometryDetails": {zh:"查看标准几何参数",en:"View master geometry"},
    "ui.brandSections": {zh:"品牌页内导航",en:"Brand section navigation"},
    "ui.appearance": {zh:"外观",en:"Appearance"},
    "ui.delivery": {zh:"交付包",en:"Delivery"},
    "theme.heading": {zh:"统一外观",en:"Shared appearance"},
    "theme.description": {zh:"记忆星图、对话、设置与运行界面使用同一主题。",en:"Memory, chat, settings and runtime pages share one theme."},
    "theme.preference": {zh:"主题模式",en:"Theme mode"},
    "theme.system": {zh:"跟随系统",en:"System"},
    "theme.light": {zh:"浅色",en:"Light"},
    "theme.dark": {zh:"深色",en:"Dark"},
    // ── Topbar ──
    "app.subtitle":       { zh: "实时遥测 · WebSocket · REST · SSE", en: "Live Telemetry · WebSocket · REST · SSE" },
    "btn.refresh":        { zh: "刷新", en: "Refresh" },
    "btn.theme":          { zh: "切换深色/浅色模式", en: "Toggle dark/light mode" },
    "ui.language": { zh: "切换语言", en: "Switch language" },
    "ui.navMain": { zh: "主导航", en: "Main navigation" },
    "ui.chatHome": { zh: "EVA 对话", en: "EVA chat" },
    "ui.memory": { zh: "记忆图谱", en: "Memory graph" },
    "ui.models": { zh: "切换魔方模型", en: "Cube model" },
    "ui.views": { zh: "魔方视角", en: "Cube views" },
    "ui.phases": { zh: "演变阶段", en: "Form evolution" },
    "ui.conversation": { zh: "与 EVA 对话", en: "Talk to EVA" },
    "ui.transcript": { zh: "对话记录", en: "Conversation transcript" },
    "ui.responseMode": { zh: "回答模式", en: "Response mode" },
    "ui.resetView": { zh: "复位视角", en: "Reset view" },
    "ui.memoryHint": { zh: "仅将本条消息作为用户陈述存入长期记忆", en: "Save only this message as a user statement in long-term memory" },
    "ui.previewNotice": { zh: "交互预览 · 未连接模型，回复为测试文本，不保存消息。", en: "UI preview · Fixture replies. No model connection or saved messages." },
    "ui.cubeExpand": { zh: "展开魔方演示", en: "Expand cube demo" },
    "ui.cubeCollapse": { zh: "收起魔方演示", en: "Collapse cube demo" },
    "ui.skipChat": { zh: "跳到消息输入", en: "Skip to message input" },
    "ui.skipSettings": { zh: "跳到设置内容", en: "Skip to settings content" },
    "ui.settingsNav": { zh: "设置导航", en: "Settings navigation" },
    "ui.settingsCategories": { zh: "设置分类", en: "Settings categories" },
    "ui.logoPreview": { zh: "Logo 预览", en: "Logo preview" },
    "ui.logoControls": { zh: "Logo 动效操作", en: "Logo motion controls" },
    "ui.logoAlt": { zh: "EVA 魔方标识", en: "EVA modular cube logo" },
    "ui.brandAssets": { zh: "品牌资产", en: "Brand assets" },
    "ui.runtimePreview": { zh: "交互预览 · 未连接运行服务", en: "UI preview · No runtime service connected" },

    // ── Chat ──
    "chat.title":         { zh: "对话", en: "Chat" },
    "chat.hint":          { zh: "POST /api/chat", en: "POST /api/chat" },
    "chat.placeholder":   { zh: "发送消息...", en: "Send a message..." },
    "chat.sse":           { zh: "SSE 流式", en: "SSE Stream" },
    "chat.remember":      { zh: "长期记住本条消息", en: "Remember this message long-term" },
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

    // ── Navigation ──
    "nav.settings":       { zh: "设置", en: "Settings" },
    "nav.backToChat":     { zh: "返回对话", en: "Back to Chat" },

    // ── Avatar states ──
    "avatar.idle":        { zh: "EVA 已就绪", en: "EVA is here" },
    "avatar.listening":   { zh: "倾听中...", en: "Listening..." },
    "avatar.responding":  { zh: "回复中...", en: "Responding..." },

    // ── Chat page ──
    "chat.emptyState":    { zh: "开始与 EVA 对话", en: "Start a conversation with EVA" },
    "chat.typing":        { zh: "EVA 正在输入...", en: "EVA is typing..." },
    "chat.sending":       { zh: "发送中...", en: "Sending..." },
    "chat.you":           { zh: "你", en: "You" },
    "chat.eva":           { zh: "EVA", en: "EVA" },
    "chat.now":           { zh: "刚刚", en: "Just now" },
    "chat.inputHint":     { zh: "输入消息...", en: "Type a message..." },
    "chat.offline":       { zh: "SSE 关闭，将使用同步模式", en: "SSE off, using sync mode" },

    // ── Settings page ──
    "studio.package": {zh:"品牌交付包",en:"Brand delivery kit"},
    "studio.packageNote": {zh:"全部变体与视角、当前 PNG、微标及动态组件，一次取得。",en:"All variants and views, the selected PNG, micro marks and live components."},
    "config.title": {zh:"显示配置",en:"Display settings"},
    "config.export": {zh:"导出配置",en:"Export settings"},
    "config.help": {zh:"读取或粘贴 JSON，先检查差异，再应用到预览。",en:"Read or paste JSON, review the differences, then apply to the preview."},
    "config.file": {zh:"读取 JSON 文件",en:"Read JSON file"},
    "config.json": {zh:"配置 JSON",en:"Settings JSON"},
    "config.preview": {zh:"预览差异",en:"Review differences"},
    "config.undo": {zh:"撤销上一次变更",en:"Undo previous change"},
    "config.review": {zh:"待应用的变更",en:"Changes to apply"},
    "config.field": {zh:"设置",en:"Setting"},
    "config.current": {zh:"当前",en:"Current"},
    "config.incoming": {zh:"导入",en:"Imported"},
    "config.noChanges": {zh:"配置与当前一致，无需应用。",en:"These settings already match. No change is needed."},
    "config.system": {zh:"系统已启用减少动态效果，导入的个人偏好不会覆盖系统设置。",en:"System reduced motion is enabled. Imported personal preferences cannot override it."},
    "config.apply": {zh:"应用此配置",en:"Apply these settings"},
    "config.cancel": {zh:"取消审阅",en:"Cancel review"},
    "studio.packageDownload": {zh:"↓ 下载交付包 · ZIP",en:"↓ Download kit · ZIP"},
    "studio.reset": {zh:"恢复默认",en:"Reset settings"},
    "studio.front": {zh:"正面",en:"Front"},
    "studio.side": {zh:"侧面",en:"Side"},
    "studio.top": {zh:"顶部",en:"Top"},
    "studio.iso": {zh:"三视角",en:"Three-quarter"},
    "studio.oblique": {zh:"斜视图",en:"Oblique"},
    "studio.bottom": {zh:"底部",en:"Bottom"},
    "studio.continuous": {zh:"∞ 连续旋转",en:"∞ Continuous"},
    "studio.pause": {zh:"暂停",en:"Pause"},
    "studio.pngSize": {zh:"PNG 尺寸",en:"PNG size"},
    "studio.settings": { zh: "设置与品牌", en: "Settings & identity" },
    "studio.workspace": { zh: "工作空间", en: "Workspace" },
    "studio.brand": { zh: "品牌与 Logo", en: "Brand & logo" },
    "studio.runtime": { zh: "系统运行", en: "System runtime" },
    "studio.navNote": { zh: "一个标准，贯穿每一次呈现。", en: "One standard. Every expression." },
    "studio.heading": { zh: "让每一种形态，都属于 EVA。", en: "Every form. One EVA." },
    "studio.description": { zh: "预览标识、检验动效，取得适合产品使用的品牌资产。", en: "Preview the mark, inspect its motion and export product assets." },
    "studio.preview": { zh: "标识预览", en: "Logo preview" },
    "studio.engine": { zh: "真实魔方状态", en: "Live cube state" },
    "studio.cycle": { zh: "演示一轮", en: "Play a cycle" },
    "studio.ready": { zh: "标准状态", en: "Ready" },
    "studio.configure": { zh: "呈现方式", en: "Appearance" },
    "studio.configureNote": { zh: "选择场景，核心比例始终保持一致。", en: "Choose the context. Preserve the proportions." },
    "studio.order": { zh: "结构", en: "Structure" },
    "studio.two": { zh: "二阶 · 正常对话", en: "2 × 2 · Conversation" },
    "studio.three": { zh: "三阶 · 深度思考", en: "3 × 3 · Deep thinking" },
    "studio.static": { zh: "静态标识", en: "Static mark" },
    "studio.reduce": { zh: "减少动态效果", en: "Reduced motion" },
    "studio.systemMotion": { zh: "遵循系统的减少动态效果偏好。", en: "Respects the system's reduced motion preference." },
    "studio.exportNote": { zh: "导出使用已复原的标准标识。", en: "Exports use the solved master mark." },
    "studio.library": { zh: "资产库", en: "Asset library" },
    "studio.libraryNote": { zh: "按场景选择，按比例使用。", en: "Choose by context. Scale proportionally." },
    "studio.master": { zh: "L1 / 完整主标 · 160px+", en: "L1 / Master mark · 160px+" },
    "studio.product": { zh: "L2 / 单色产品标识 · 48–160px", en: "L2 / Product mark · 48–160px" },
    "studio.dark": { zh: "L2 / 深色背景标识", en: "L2 / Dark backgrounds" },
    "studio.micro": { zh: "L3 / 微型标识 · 16–48px", en: "L3 / Micro mark · 16–48px" },
    "studio.rules": { zh: "保持一致，也保留变化。", en: "Stay consistent. Allow change." },
    "studio.rulesNote": { zh: "留白至少为标识宽度的 25%，整体等比缩放。小尺寸使用简化微标。", en: "Keep 25% clear space and scale proportionally. Use the micro mark at small sizes." },
    "studio.forbidden": { zh: "禁止拉伸、独立缩放模块、修改间隙或添加霓虹与复杂纹理。", en: "Avoid stretching, resizing single modules, changing gaps or adding glow and textures." },
    "studio.journal": { zh: "查看逆序复原记录", en: "Inverse move journal" },
    "studio.journalNote": { zh: "复原严格撤销已完成动作，切换结构前先恢复标准状态。", en: "Restoration reverses committed moves before replacing the structure." },
    "settings.heading":   { zh: "系统仪表盘", en: "System Dashboard" },
    "settings.autoRefresh": { zh: "自动刷新", en: "Auto Refresh" },
    "brand.title": { zh: "品牌识别系统", en: "Brand Identity System" },
    "brand.subtitle": { zh: "统一管理 Logo 几何、变体与动效规则", en: "Manage logo geometry, variants and motion rules" },
    "brand.idle": { zh: "静态", en: "Static" },
    "brand.scramble": { zh: "打乱", en: "Scramble" },
    "brand.solve": { zh: "复原", en: "Solve" },
    "brand.reduced": { zh: "减少动态", en: "Reduced" },
    "brand.variant": { zh: "Logo 变体", en: "Logo variant" },
    "brand.primary": { zh: "Primary · 主标", en: "Primary · master" },
    "brand.monochrome": { zh: "Monochrome · 单色", en: "Monochrome" },
    "brand.dark": { zh: "Dark · 深色背景", en: "Dark · dark background" },
    "brand.light": { zh: "Light · 浅色背景", en: "Light · light background" },
    "brand.favicon": { zh: "Favicon · 微标", en: "Favicon · micro mark" },
    "brand.rules": { zh: "保持比例、间距、圆角和统一视角；只允许整体等比缩放。", en: "Keep proportion, gap, radius and camera consistent; scale the whole mark only." },
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
    this._lang = lang === "en" ? "en" : "zh";
    try { localStorage.setItem("eva-lang", this._lang); } catch (_) { /* Current-page language remains usable. */ }
    document.documentElement.lang = this._lang === "zh" ? "zh-CN" : "en";
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
    for (const attribute of ["aria-label", "alt"]) {
      document.querySelectorAll(`[data-i18n-${attribute}]`).forEach(el => {
        const key = el.getAttribute(`data-i18n-${attribute}`), text = this.t(key);
        if (text && text !== key) el.setAttribute(attribute, text);
      });
    }
    // The theme controller owns its state-dependent labels in the current language.
    globalThis.EvaTheme?.bind();
  },
};

// Init on load
document.addEventListener("DOMContentLoaded", () => {
  document.documentElement.lang = I18N.lang() === "zh" ? "zh-CN" : "en";
  I18N.applyDOM();
});
