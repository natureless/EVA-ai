# EVA → EVA-MVSC (DCS) 融合对齐分析

## 一、当前代码与MVSC框架的逐层映射

### L0: EVA-VM 数字驻留环境

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| 持续进程 | ✅ FastAPI lifespan + daemon线程 | 需增强进程管理 |
| 心跳 | ⚠️ HealthService + /health/live | 缺少自证心跳(挑战-响应) |
| 状态快照 | ✅ SnapshotStore.save_latest() | 缺少事务静默点概念 |
| 事件日志 | ✅ EventBus + S5持久化 | 需改为append-only不可覆盖 |
| 恢复 | ✅ snapshot恢复 + S1/S2恢复 | 需增加事件重放机制 |
| 外部控制 | ❌ 无独立管理通道 | **缺失** |
| 权限隔离 | ⚠️ 执行器有路径白名单 | 需更系统化 |
| 故障注入 | ❌ 无 | **缺失** |
| BodyState | ⚠️ 无正式数字身体模型 | **需新建** |
| ViabilityBounds | ⚠️ constitution有部分边界 | 需独立为BodyModel |

### L1: EVA Kernel 持续运行内核

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| 时间管理 | ✅ CognitionLoop tick | 需独立Clock组件 |
| 事件排序 | ✅ EventBus Queue (FIFO) | 需sequence编号 |
| 状态事务 | ⚠️ 部分通过RLock | 需乐观锁+版本号 |
| 模块注册 | ✅ AgentRegistry + ToolRegistry | 需泛化为ModuleRegistry |
| 任务调度 | ✅ RuntimeScheduler | 可 |
| 身份加载 | ✅ SelfModelStore + ProfileStore | 需版本化 |
| 权限检查 | ✅ PolicyEngine | 需增强 |
| 运行模式切换 | ⚠️ PolicyEngine StateMachine (4态) | 需扩展到8态 |
| 故障隔离 | ⚠️ Quarantine机制 | 需更精细 |
| 快照与恢复 | ✅ 有 | 需事件重放 |
| 生命周期管理 | ⚠️ bootstrap/shutdown | 需正式LifecycleManager |

### L2: 感知、事件和工具接口

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| EventBus | ✅ 线程安全队列 | 需升级为EventEnvelope |
| 感知标准化 | ⚠️ 事件类型字符串 | 需事件族命名空间 |
| MCP适配器 | ❌ 无 | **缺失** |
| 连接器 | ✅ GitHub connector | 可扩展 |

### L3: 世界模型、自我模型与生成模型

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| WorldModel | ✅ WorldModelGraph (实体+边) | 可，需增强 |
| SelfModel | ⚠️ SelfModelStore (静态JSON) | 需拆6子模型 |
| BodyModel | ❌ 无 | **需新建** |
| OtherAgentModel | ❌ 无 | 远期 |
| IdentityModel | ⚠️ 隐含在Persona | 需显式化+版本化 |
| BoundaryModel | ⚠️ 隐含在constitution | 需独立模型 |
| AgencyModel | ❌ 无ActionReceipt | **需新建** |
| CapabilityModel | ⚠️ 无 | **需新建** |
| NarrativeModel | ❌ 无 | **需新建** |

### L4: 内容形成、注意、价值和竞争

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| 候选内容生成 | ⚠️ Planner.plan() | 需独立ContentEngine |
| 注意力竞争 | ❌ 无可解释加权函数 | **需新建** |
| 递归稳定 | ❌ 无 | **需新建** |
| 价值调制 | ⚠️ ImportanceScorer | 需独立ValueModel |

### L5: 全局工作空间与跨模块广播

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| Workspace | ❌ 无 | **需新建** |
| 广播门控 | ⚠️ WS广播(无门控) | 需加条件 |
| WorkingMemory | ✅ S1 SessionMemory | 可 |

### L6: 元认知、叙事与身份连续性

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| 置信度 | ⚠️ 仅在AgentResult.meta | 需系统化 |
| 反思 | ❌ 无 | **需新建** |
| 叙事身份 | ❌ 无 | **需新建** |

### L7: 代理操作系统与行动层

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| IntentParser | ❌ Planner混合职责 | 需独立 |
| Planner | ✅ Planner(轻量) | 需DAG支持 |
| Router | ✅ AgentRouter | 可 |
| Executor | ✅ 5类执行器 | 需ToolAdapter协议 |
| Verifier | ❌ 无 | **需新建** |
| SandboxManager | ⚠️ 部分(code executor) | 需独立 |

### L8: 治理、审计与研究评估

| MVSC要求 | 当前EVA状态 | 差距 |
|----------|------------|------|
| Policy | ✅ PolicyEngine | 需增强 |
| Audit | ✅ ExecutorAuditLog + Trace | 需更全面 |
| Metrics | ✅ /api/metrics | 需MVSC维度 |
| Ablation | ❌ 无 | **需新建** |

---

## 二、关键架构差异总结

### 2.1 当前项目优点(保留)
- EventBus + CognitionLoop 核心运转良好
- 五层记忆架构(S1-S5)是坚实的记忆基础
- PolicyEngine状态机+优先级+令牌是好的安全基础
- WorldModelGraph的实体-边图结构正确
- Tool calling循环+9内置工具实用
- Constitution加载+合并机制好

### 2.2 核心缺失(需新建)
1. **BodyModel** — 数字身体状态、可生存域
2. **Attention** — 可解释的注意力竞争机制
3. **Workspace** — 全局工作空间+广播门控
4. **Metacognition** — 置信度、反思、不确定性
5. **Verifier** — 行动结果验证
6. **IntentParser** — 独立意图解析
7. **NarrativeModel** — 叙事自我
8. **AgencyModel** — ActionReceipt归属
9. **CapabilityModel** — 能力自知
10. **Ablation Framework** — 机制开关+消融测试

### 2.3 需重构升级
1. Event → EventEnvelope (增加causation_id, subject_id, sequence等)
2. SelfModel (单JSON → 6子模型)
3. Planner (混合 → 独立IntentParser + DAG Planner)
4. CognitionLoop (单方法 → 分阶段Pipeline)
5. State管理 (dict → ConsciousState)
6. 记忆检索 (纯语义 → 多因素RetrievalScore)
7. MemoryRecord (增加版本、来源、有效期等)

---

## 三、融合策略：渐进式演进，不推翻重来

**原则**: 当前代码是v0.1/v0.2的工程成果，MVSC是理论框架。融合方式应该是：
1. 保留现有良好组件
2. 在现有组件上增加MVSC要求的接口和字段
3. 新建缺失模块
4. 逐步重构责任不清晰的组件

**不做的**:
- 不推翻重写
- 不一次性全部改造
- 不丢失现有测试覆盖
