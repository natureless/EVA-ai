# EVA-VM Architecture v1

> **EVA-VM = 运行于受控虚拟机中的人格化认知代理主机。**
> 以虚拟机作为身体边界，以长期记忆与世界模型作为认知连续体，
> 以策略层作为行动宪法，以用户 P0 指令作为最高主权，以受限执行器作为行动器官。

## 五个关键词

| 关键词 | 含义 |
|--------|------|
| **驻留** | 不是一次性会话，而是长期存在 |
| **主权优先** | 先执行用户的明确命令 |
| **边界明确** | 不能无限制自治 |
| **连续性** | 跨重启保留人格与状态 |
| **可审计** | 所有行动必须可回放、可追责 |

## 一句话架构结论

> 宿主负责边界，虚拟机负责身体，容器负责器官，认知核负责意识，
> 策略层负责宪法，记忆层负责连续性，行动层负责世界交互。

---

## 完整分层图

```
用户 / 主权所有者
        │
  统一人格接口 (EVA Shell / Dashboard / API)
        │
┌───────┴──────────────────────────────────────────┐
│ 宿主层 (Host)                                     │
│   Linux Host | KVM/QEMU/libvirt | nftables       │
│   host volumes / snapshots / backup / monitor     │
└───────┬──────────────────────────────────────────┘
        │
┌───────┴──────────────────────────────────────────┐
│ EVA-VM 虚拟机层                                   │
│   Guest OS | systemd | rootless Podman/Docker     │
│   VM local policy agent                          │
└───────┬──────────────────────────────────────────┘
        │
┌───────┴──────────────────────────────────────────┐
│ 核心认知层 (EVA Core)                             │
│   FastAPI / Dashboard | Event Bus                │
│   Cognition Loop | Agent Registry/Router/Orch    │
│   Scheduler | Stagnation Detector | Persona Shell│
└───────┬──────────────────────────────────────────┘
        │
   ┌────┴────┬─────────┬──────────┐
   │         │         │          │
┌──┴──┐ ┌───┴───┐ ┌───┴───┐ ┌───┴──────┐
│记忆层│ │策略层  │ │行动层  │ │网络层     │
│Memory│ │Policy │ │Action │ │Network    │
└─────┘ └──────┘ └──────┘ └──────────┘
```

## 实体部署图

```text
物理机 / 宿主 Linux
└── KVM + QEMU + libvirt
    └── EVA-VM
        ├── Guest OS (Ubuntu Server / Debian)
        ├── systemd
        ├── rootless Podman/Docker
        │   ├── eva-core
        │   ├── dashboard
        │   ├── memory-db
        │   ├── scheduler
        │   ├── audit-agent
        │   └── executor pods
        ├── /data/eva/memory
        ├── /data/eva/world
        ├── /data/eva/logs
        └── /data/eva/snapshots
```

---

## 分层说明

### 宿主层 (Host Boundary)

EVA 的外骨骼，只负责三件事：
1. 提供隔离运行环境
2. 提供持久化与恢复
3. 提供最外层网络与资源边界

```
Linux 宿主系统
├── KVM/QEMU/libvirt 管理 EVA-VM
├── nftables 负责总网络策略
├── 定时快照、备份、宿主级恢复
└── 宿主监控：CPU、内存、磁盘、网络、进程健康
```

宿主只做：**边界、运行、备份、恢复、观察**。业务逻辑不进入宿主。

### 虚拟机层 (EVA-VM)

EVA 的身体壳层。EVA 不直接"活在宿主上"，而是活在独立 VM 中：

- 内外边界
- 可迁移运行环境
- 可复制人格舱
- 可冻结与恢复
- 不污染主机工作环境

VM 内职责：Guest OS、systemd 服务编排、容器运行时、本地日志/数据卷/策略代理、统一入口 API 与 Dashboard。

### 容器层 (Containers)

器官化拆分。核心容器保持稳定，执行器容器保持可替换。

**常驻核心容器**（生命维持系统）：

| 容器 | 职责 |
|------|------|
| `eva-core` | FastAPI 应用、认知循环、agent 编排 |
| `memory-db` | SQLite/PostgreSQL 记忆存储 |
| `scheduler` | APScheduler 周期任务 |
| `audit-agent` | 审计日志消费者 |
| `dashboard` | Web 仪表板 |

**执行器容器**（四肢，按需唤起）：

| 容器 | 职责 |
|------|------|
| `browser-executor` | 网页浏览、搜索、表单操作 |
| `api-executor` | 外部 API 调用 |
| `file-executor` | 读写本地文件 |
| `code-executor` | 受限脚本执行 |
| `comms-executor` | 消息、邮件、通知 |

### 认知核 (EVA Core)

v0.1 的延续，明确分工：

| 组件 | 职责 |
|------|------|
| **Persona Shell** | 统一人格表达：语气、风格、边界、价值约束、输出一致性 |
| **Cognition Loop** | 持续认知循环：接收输入 → 调用记忆 → 更新局势 → 生成计划 → 调度执行器 → 回写状态 |
| **Agent Orchestrator** | 多代理编排：任务拆解、执行器调用、结果聚合、冲突消解 |
| **Scheduler** | 周期行为：主动检查、停滞检测、状态压缩、记忆沉淀、快照触发 |

### 记忆层 (Memory Layer)

五层记忆架构：

| 层级 | 名称 | 特征 |
|------|------|------|
| S1 | Session Memory | 当前会话上下文，短时、高频、可丢弃 |
| S2 | Working Memory | 近期任务、当前目标、活跃状态，跨请求但不一定永久 |
| S3 | Long-term Memory | 用户偏好、长期项目、稳定关系，人格连续性的核心 |
| S4 | World Model | 人/任务/文件/项目/依赖/风险/优先级/阻塞点的结构化表示 |
| S5 | Event/Trace/Snapshot | 关键行为记录：谁触发了什么、为什么、访问了什么、结果如何 |

### 策略层 (Policy Layer)

不是"怎么更聪明"，而是"怎么不越界"。

**优先级系统**：

```
P0  用户明确命令          永远最高
P1  安全规则/系统边界      硬约束，任何任务都不能越过
P2  已批准计划任务         授权过的自动任务
P3  主动建议/提醒          系统建议，需用户批准
P4  自主探索              最低等级，仅在预算内运行
```

**状态机**：

```
Dormant ──用户命令──→ Commanded ──完成/超时──→ Dormant
   │                      │
   │    ┌──授权──→ Supervised ──预算耗尽──→ Dormant
   │    │                 │
   └────┼──异常/违规──→ Quarantined ←──┘
        │                    │
        └──手动干预──────────┘
```

### 行动层 (Action Layer)

EVA 不直接碰世界，而是通过受限执行器碰世界。

核心原则：
- 执行器必须无人格
- 执行器必须可替换
- 执行器必须低权限
- 执行器必须可审计
- 执行器失败不能污染核心认知状态

**人格在 Core，行动在 Executor。**

### 网络层 (Network Layer)

| 模式 | 说明 |
|------|------|
| L0 封闭 | 无外网，仅本地和内网资源 |
| L1 白名单 | 只访问指定域名和 API |
| L2 监督探索 | 允许临时浏览，带预算和日志 |

---

## 控制流与数据流

### 控制流

```
用户命令
→ Persona Shell 接收
→ Policy Engine 判断优先级与授权
→ Cognition Loop 读取记忆与世界模型
→ Agent Orchestrator 制定执行计划
→ 调度相应 Executor
→ 执行结果返回 Core
→ 更新记忆与状态
→ 输出给用户
```

### 数据流

```
输入
→ Session Memory
→ Working Memory
→ World Model 更新
→ Long-term Memory 沉淀
→ Event/Trace 记录
→ Snapshot 定时归档
```

---

## 持久化与恢复

### 恢复流程

```
宿主启动
→ EVA-VM 自动拉起
→ systemd 启动核心容器
→ 恢复 memory-db
→ 读取最近 snapshot
→ 恢复 world model
→ 恢复状态机
→ 进入 Dormant
→ 等待用户或计划任务唤醒
```

### 恢复后第一动作

1. 校验数据完整性
2. 恢复认知状态
3. 生成启动摘要
4. 进入待命
5. 如有未完成计划，报告而不直接执行

---

## 与 v0.1 的映射

| 现有 v0.1 | EVA-VM v1 |
|-----------|-----------|
| FastAPI lifespan | EVA Core 生命周期管理 |
| Event Bus | 认知事件总线 |
| Cognition Loop | 持续认知主循环 |
| AgentRegistry/Router/Orchestrator | 多代理编排核心 |
| SQLite 事件/记忆/trace | 初代记忆层与审计层 |
| Snapshot 保存/恢复 | 持久化雏形 |
| APScheduler | 主动循环与周期任务 |
| Stagnation detector | 主动注意机制 |
| Dockerfile/docker-compose | 容器化基础设施 |
| constitution.yaml | 宪法与策略引擎 |
| SelfModel + PersonaService | Persona Shell 雏形 |
| PredictionTracker | 预测误差驱动更新 |

v0.1 不是"玩具原型"，而是 **EVA-VM v1 的认知核前身**。

---

## v1 非目标

v1 **不做**：

- 无边界互联网自治
- 自发复制与扩张
- 未授权持久控制外部系统
- 高风险自动执行链条
- 自主改变主权规则
- 自发生成新人格主核

这不是保守，而是工程边界。

---

## 交付件清单

```text
docs/
├── eva-vm-architecture.md    ← 本文档
├── consciousness_model.md
config/
├── persona.yaml
├── policy.yaml
├── executors.yaml
└── storage.yaml
infra/
├── libvirt/
│   └── eva-vm.xml
├── host/
│   ├── nftables.conf
│   ├── backup-restore.sh
│   └── healthcheck.sh
eva/
├── docker-compose.yml
├── Dockerfile
├── app/
├── core/
├── memory/
├── persona/
├── world/
└── runtime/
constitution.yaml              ← 宪法单一事实来源
```
