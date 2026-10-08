// Actual HTTP consumer and extension attachment, from one runtime observation.
(function (root) {
  function describe(data) {
    if (!data || !["legacy", "minimal"].includes(data.mode)) {
      return { text: "运行模式：未知", detail: "无法读取实际运行状态", state: "unknown" };
    }
    const mvsc = data.extensions?.mvsc;
    const extension = mvsc?.status === "attached" ? " · MVSC 扩展已装配"
      : mvsc?.status === "failed" ? " · MVSC 装配失败" : "";
    const live = data.phase === "running" && data.consumer_running === true && data.accepting_events === true;
    return {
      text: `${data.mode === "minimal" ? "Minimal" : "Legacy"} · ${live ? "运行中" : "未接收请求"}${extension}`,
      detail: `HTTP 消费者：${data.consumer_type || "未知"}。` +
        (mvsc?.status === "attached" ? "MVSC 实验循环未接入 HTTP 消费。" : ""),
      state: live ? "running" : "unavailable",
    };
  }

  async function refresh(element, fetcher = root.EvaHttp?.request || root.fetch.bind(root)) {
    let data;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4000);
      try {
        const response = await fetcher("/api/runtime", { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("runtime unavailable");
        data = await response.json();
      } finally { clearTimeout(timer); }
    } catch (_) { data = null; }
    const view = describe(data);
    element.textContent = view.text;
    element.title = view.detail;
    element.dataset.runtimeState = view.state;
  }

  root.EvaRuntimeStatus = { describe, refresh };
  if (typeof document !== "undefined") {
    const element = document.getElementById("runtimeStatus");
    if (element) {
      // A completed poll schedules the next one; slow responses cannot overlap.
      const poll = async () => { await refresh(element); setTimeout(poll, 5000); };
      poll();
    }
  }
})(globalThis);
