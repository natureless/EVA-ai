# EVA Documentation Index

This directory contains three kinds of documents. Their status matters:

- **Canonical** documents describe the code that runs today.
- **Experimental** documents describe optional runtimes under `packages/`, each with an explicit feature flag.
- **Research** documents preserve design theory and long-term direction; they are not implementation contracts.

When documents disagree, use this precedence order:

```text
code + tests + OpenAPI
> canonical documentation
> experimental documentation
> research documents
```

## Canonical

| Document | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Current boundaries, runtime topology, data/control flow, and known gaps |
| [Architecture and Development Retrospective — 2026-10-01](architecture-retrospective-2026-10-01.md) | 当前工作区架构、模块与交付状态、具体技术债、开发顺序及本轮隔离回归证据 |
| [Runtime Refactor v0.2](runtime-refactor-v02.md) | 统一生命周期、事件处理服务、可替换认知组件与关闭/恢复语义 |
| [Event Processing](event-processing.md) | Shared execution service, scheduling boundary, worker ownership and compatibility |
| [Request Receipts EVT-00](request-receipts-evt00.md) | 队列保护、请求期限、终态保留、停机回执与网页交付兜底 |
| [Runtime Baseline BASE-02](runtime-baseline-base02.md) | 三种配置的实际 HTTP 消费者、界面模式、固定离线数据和等待/延迟/Python 分配基线 |
| [Versioned Events EVT-01](versioned-events-evt01.md) | 共享版本化信封、旧事件适配、来源标识、入队快照与离线副本迁移 |
| [Obsidian Memory Graph OBS-01](obsidian-memory-graph.md) | 真实记忆粒子图谱、来源连线、Obsidian 笔记镜像与只读预览 |
| [Cloudflare Deployment](cloudflare-deployment.md) | Access 身份边界、命名隧道、本机部署流程及当前开通状态 |
| [World Restore RST-01](world-restore-rst01.md) | 世界图幂等合并、分页恢复、时间/权威规则与旧快照兼容 |
| [World Recovery RST-02](world-recovery-rst02.md) | 备份/冲突诊断、候选与回滚副本、有限读取、成本测量和测试隔离修复 |
| [World Provenance EVD-01](world-provenance-evd01.md) | 世界图字段来源、迁移兼容、上下文/API 标注与推断不自动升级 |
| [Development Guide](../DEVELOPMENT.md) | Local setup, quality gates, and extension workflows |
| [API Reference](api-reference.md) | Stable endpoint families and chat/WebSocket contracts |
| [Cube Logo](cube-logo.md) | 二阶／三阶合法转动、逆序复原、共享组件与视角接线 |
| [Chat Response Modes](chat-modes.md) | 按请求固定的正常／深度模式、能力元数据与公开状态边界 |
| [Brand System](brand-system.md) | 当前品牌工作区、统一 Token、同源资产、偏好、导出与使用规范 |
| [Source Review and Roadmap v3](source-review-and-roadmap-v3.md) | 来源复核、当前实现差距、已复现问题、后续优先级与验收计划；待办不代表已有能力 |
| [UI and Logo Review and Plan — 2026-10-08](ui-logo-review-plan-20261008.md) | 当前 UI／Logo 功能与验证复盘、原生与联调缺口、下一阶段 G0–G5 交付及验收计划 |
| [UI and Logo Task Breakdown — 2026-10-08](ui-logo-task-breakdown-20261008.md) | 30 项可执行任务的依赖、源码范围、交付物、验收条件、首批顺序与工时 |
| [UI Candidate G0 Evidence — 2026-10-08](../artifacts/ui-acceptance/ui-v1-20261008-g0-01/README.md) | 封存的 G0 源码基线、35 区域、32 接口与初始 30 项工作单；后续执行见当前状态入口 |
| [UI Candidate G2-01 Status — 2026-10-08](../artifacts/ui-acceptance/ui-v1-20261008-g0-01/CURRENT.md) | G2-01 批次结束时的状态：5 项通过、G1-01 未验证、24 项待执行；保留原候选记录 |
| [UI Chat Contracts and Input Fix — 2026-10-08](../artifacts/ui-acceptance/ui-v1-20261008-g2-02/README.md) | 上一候选：四组实际 API／客户端回放、长草稿发送前校验；480 JS／34 UI Python／43 隔离后端通过，保留原版证据 |
| [UI Memory and Status Recovery — 2026-10-09](../artifacts/ui-acceptance/ui-v1-20261009-g2-04/README.md) | 最新候选：记忆列表竞态与错误恢复修复；489 JS／34 UI Python、31 实际 API 检查、17 客户端回放通过，剩余系统界面与原生门槛待验 |
| [UI and Logo Work Items](ui-logo-development-plan.md) | 原 UI／Logo 工作项定义与历史执行记录；下一阶段专项顺序以 2026-10-08 复盘计划为准 |
| [UI and Logo M1 Acceptance](ui-logo-m1-acceptance.md) | 动效异常恢复的代码与实测证据，交付包校验，以及下载／离线／跨浏览器待验收范围 |
| [CHAT-01 Acceptance](ui-logo-chat-acceptance.md) | 消息复制、固定模式重发、结果未知与原请求查询的代码／实测证据及待验收范围 |
| [DS-01 Acceptance](ui-logo-ds01-acceptance.md) | 共享界面 Token、紧凑魔方、响应式与主题语言矩阵、键盘／播报实现及待验收范围 |
| [BRAND-01 Acceptance](ui-logo-brand01-acceptance.md) | 品牌／组件版本、规范化规则与源码指纹、来源一致性门槛、浏览器与 CLI 包核对 |
| [BRAND-02 Acceptance](ui-logo-brand02-acceptance.md) | 受限 JSON 配置交换、只读差异、逆序复原后应用与撤销、竞态／存储／系统偏好验证 |
| [Unified UI Theme](ui-theme-system.md) | 全界面主题来源、偏好契约、星图调色与状态保持、七类页面本地验证 |
| [UI Interaction Upgrade](ui-interaction-upgrade.md) | 对话阅读导航、设置页内定位、星图面板焦点与快捷键优化，组件 1.1.2 本地证据 |
| [OpenAI Design Adaptation](ui-openai-adaptation.md) | 官方设计原则的 EVA 适配、渐进展示、系统字体与文字放大回退，组件 1.1.3 本地证据 |
| [PERF-01 Observations](ui-logo-perf01-observations.md) | 固定设备内置浏览器中间观测、原始证据及前台／采样限制；未完成性能验收 |
| [UI and Logo Release Checks](ui-logo-release-checks.md) | 可重复本地检查、独立解码与主几何核对、版本交付包、源码指纹及原生验收待办 |
| [UI and Logo Interaction Acceptance](ui-logo-final-interactions.md) | 当前主题的模式／语言／动效组合、消息与导出失败恢复、源码关联及实测边界 |
| [Development Plan v2](development-plan-v2.md) | Earlier work-item specifications; execution order superseded by v3 |
| [Production Runbook](production-runbook.md) | Deployment, security, monitoring, backup, and recovery |
| [Architecture Boundaries](architecture-boundaries.md) | Import rules enforced by architecture tests |
| [Assistant v0.1 Integration](assistant-v01-integration.md) | Provenance, retention consent, output review and their limits |
| [Process Workers](process-workers.md) | Opt-in text-agent process pool, configuration, failure semantics and sandbox limits |

## Experimental

The stable runtime must not import `packages/*` except through `app/experimental.py`.

- [Minimal Brain v0.1](minimal-brain-v01.md) — enable with `EVA_ENABLE_MINIMAL_BRAIN=true`; concurrent state scheduling, existing processing adapter, demo and limitations
- [Business Goals GOL-01](business-goals-gol01.md) — 默认关闭的 SQLite 目标持久化、HTTP/回执接线、只读文件哈希验证与目标证据粒子图谱；支持 legacy/Minimal
- [Tool Reconciliation REC-01](tool-reconciliation-rec01.md) — 未知文件写入结果的固定描述、独立观察与图谱显式核验；保留原始收据与目标状态
- [Action Input Graph ACT-05](action-input-graph-act05.md) — 实际 Agent 行动与历史世界/记忆输入引用的有界图谱，只读追溯与部署边界
- [State/Action Transactions ACT-01](state-action-transactions-act01.md) — SQLite 状态/事件/行动意图原子提交、领取凭证与未知恢复；显式实验循环及 ACT-02 请求账本的基础
- [Durable HTTP Requests ACT-02](durable-requests-act02.md) — 实际 legacy/Minimal 请求耐久登记、事务领取、重启回执与目标关联；默认关闭，不自动重放
- [Unclaimed Request Recovery ACT-03](unclaimed-request-recovery-act03.md) — 仅恢复有未领取证据的原请求，保留原期限与目标关联、协调 Minimal 去重；默认关闭
- [Action Context Bindings ACT-04](action-context-bindings-act04.md) — 实际世界/记忆输入版本、逐项记忆引用、Agent 意图事务与只读查询；默认关闭

The following documents apply only when `EVA_ENABLE_MVSC_PIPELINE=true`.
Minimal Brain and MVSC cannot be enabled together.

- [MVSC Architecture](mvsc-architecture.md)
- [MVSC Integration Guide](mvsc-integration-guide.md)
- [MVSC Development Plan](mvsc-dev-plan.md)
- [MVSC Feasibility Analysis](mvsc-feasibility-analysis.md)
- [MVSC Panorama Analysis](eva-mvsc-panorama-analysis.md)

## Research And History

These documents explain the conceptual model behind EVA. Treat them as design
inputs, not promises that every described component is implemented.

- [EVA-VM Architecture](eva-vm-architecture.md)
- [Cognitive Dynamics v1](cognitive-dynamics-v1.md) — proposed concurrent recurrent runtime, multi-timescale semantics, repository gaps, and falsifiable acceptance experiments
- [Information Dynamics Consciousness Model](consciousness_model.md)
- [Legacy Operations Runbook](runbook.md)

## Documentation Rules

1. New runtime behavior must update the canonical architecture or API document.
2. Future work and execution priorities belong in `source-review-and-roadmap-v3.md`, not in the current-state section. The v2 plan retains earlier work-item detail.
3. Generated OpenAPI at `/openapi.json` is the endpoint schema authority.
4. Secrets, personal paths, and access tokens must never appear in documentation.
