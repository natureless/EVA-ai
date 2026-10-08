# G1-01 原生环境核验

状态：未验证。证据见 [environment-check.json](environment-check.json)。浏览器连接清单请求在 300 秒后超时；Windows computer-use 能枚举 Chrome／Edge 窗口，但读取所选 Chrome 页面时工具无法可靠识别当前 URL，策略检查中止本轮 Windows 操作。此后未再调用 Windows 输入，未尝试绕过检查。

已使用 [computer-use SKILL.md](C:/Users/origi/.codex/plugins/cache/openai-bundled/computer-use/26.930.61225/skills/computer-use/SKILL.md)及其[运行指引](C:/Users/origi/.codex/plugins/cache/openai-bundled/computer-use/26.930.61225/docs/guidance.md)。指引明确要求：

> If Computer Use reports that the turn ended or that the user stopped Computer Use, stop issuing app input.

这限制的是 Windows 界面操作；独立的本地 API 准备继续执行。工具中止不是页面功能失败，也不能由可枚举窗口推断浏览器已连通。浏览器版本、目标 URL、缩放、减少动态效果、页面操作、原生下载／离线与前台性能均没有通过记录。后续只能在工具能够完成 URL 策略校验的环境继续原生验收。
