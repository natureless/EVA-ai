# UI 候选验收入口

候选 `ui-v1-20261008-g0-01`；工作树快照，Brand 1.0.0／Components 1.1.3／Token schema 1／Package format 2。

G0-01–04 已完成源码审阅与产物一致性检查。G1–G5 的 26 项仍待执行，整体发布未通过。本轮没有运行浏览器、启动服务、请求 API、读取业务数据库或实际凭据，也没有提交／清理 Git。

| 产物 | 用途 |
| --- | --- |
| [source-baseline.json](source-baseline.json) | 当前源码与关联 API 指纹、版本、历史报告差异与包内容核对 |
| [surface-inventory.md](surface-inventory.md) | 七类界面、35 个逻辑区域及其状态、操作影响和待验收范围 |
| [control-inventory.json](control-inventory.json) | 203 个源码控件归类与 8 类动态控件；不是运行时实例数 |
| [api-map.md](api-map.md) | 32 项 UI 请求的实际服务路由、身份、副作用与关联测试 |
| [findings.md](findings.md) | 6 项源码差异／待联调风险及后续任务 |
| [acceptance-index.json](acceptance-index.json) | 30 项工作单：4 passed、26 planned；依赖、验收、证据和发现关联 |
| [g0-validation.json](g0-validation.json) | 本轮指纹稳定、路由／控件／任务映射与数量核验 |
| [artifact-manifest.json](artifact-manifest.json) | 最终复核生成的文件哈希清单，固定本轮证据字节 |
| [working-tree-status.txt](working-tree-status.txt) | 相关路径修改状态，不代表已形成独立提交 |

历史完整报告为 [artifacts/ui-release/local-20261008-config-review/report.json](../../../artifacts/ui-release/local-20261008-config-review/report.json)，其 475 项 JavaScript／34 项 Python、生成资产与双阶源码包是已存在的自动检查。本轮只核对当前指纹与该报告关系及包内容，不冒充重新跑过全部测试。关联 API 字节单独采集，尚未由本轮服务测试验证。

发现优先顺序：G0-F01 输入长度契约 → G0-F05 认证／错误联调 → G0-F02 查询副作用边界 → G0-F03 语言子区域 → G0-F04 旧监控路由范围 → G0-F06 卡片键盘。F02 属于必须准确登记的既有契约，并不自动要求更改后端。

下一批：G1-01 核验原生环境；G2-01 准备隔离联调。环境缺失保持未验证，继续可执行项。真实保存／离线、前台双阶长测、原生缩放、屏幕阅读器与手机软键盘仍无本候选通过证据。

支持范围由实际结果确定：目前只有历史内置浏览器与自动证据；中文／英文在 CHAT／SET／LIST 已接入，MAP／MVSC／PROMPT 语言覆盖仍有缺口。合成预览、完整服务、真实数据库只读服务、便携包分别验收。

状态词：planned 待执行；running 进行中；passed 验收通过；failed 已执行但失败；unverified 环境缺失；deferred 有明确影响说明的 P1 延期。证据引用不升级任务状态。

候选源码改变后建立新快照／候选关联，不能覆盖既有结果来伪造同版验收。此目录的 capture_g0.py 是 G0 采集方法，本次结果已保存；生成哈希清单后拒绝重新覆盖。后续执行在任务子目录记录，维护工作单时保留本轮清单与状态变更来源。
