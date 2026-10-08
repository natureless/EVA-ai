# 当前验收状态

G0 四项和 G2-01 隔离联调准备通过；G1-01 因 Windows 工具无法可靠识别浏览器当前 URL 而未验证；另外 24 项待执行，整体验收与发布未通过。

- [当前 30 项工作单](progress.json)
- [G2-01 隔离环境与真实 API 准备证据](G2-01/README.md)
- [G1-01 原生环境阻断](G1-01/environment-check.json)
- [G0 原始证据入口](README.md)与[封存哈希](artifact-manifest.json)
- [状态变更记录](status-history.jsonl)

原始 G0 工作单及封存文件保持原字节。当前状态使用 progress.json 覆盖视图，不将历史入口里的 4 passed／26 planned 计数当作最新执行结果。其余任务没有借用这次准备检查标为通过。
