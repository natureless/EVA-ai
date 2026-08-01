# EVA-MVSC 融合实施路线图

## 总体原则

1. **保留现有良好组件**，不改动测试已覆盖的核心逻辑
2. **增加而非替换**：现有组件增加MVSC接口，新建缺失模块
3. **契约先行**：先定义事件协议和状态模型，再改实现
4. **逐层推进**：从底层Kernel向上层认知逐层改造
5. **消融开关**：新机制从第一天就带feature flag

---

## 阶段一：契约与状态模型 (Week 1-2)

### 任务 1.1: 事件协议升级

**文件**: `packages/contracts/events.py` (新) 或扩展 `event/event_schema.py`

从当前 `Event`:
```python
class Event:
    id: str
    type: str
    source: str
    timestamp: datetime
    payload: dict
    correlation_id: str
```

升级为 `EventEnvelope`:
```python
class EventEnvelope(BaseModel):
    event_id: str
    event_type: str          # 命名空间: "perception.user_message_received"
    source: str
    timestamp: datetime
    correlation_id: str
    causation_id: str | None # 新增：因果链
    subject_id: str          # 新增：主体ID
    session_id: str | None   # 新增
    payload: dict
    confidence: float = 1.0  # 新增
    priority: float = 0.0    # 新增
    sensitivity: str = "internal"  # 新增
    schema_version: str = "1.0"
    sequence: int | None     # 新增：单调递增序号
```

**事件族命名空间**:
```
runtime.*       — 运行时事件
perception.*    — 感知事件
world.*         — 世界模型事件
body.*          — 身体/资源事件
attention.*     — 注意力事件
workspace.*     — 工作空间事件
self.*          — 自我模型事件
goal.*          — 目标事件
plan.*          — 计划事件
action.*        — 行动事件
verification.*  — 验证事件
memory.*        — 记忆事件
identity.*      — 身份事件
lifecycle.*     — 生命周期事件
security.*      — 安全事件
experiment.*    — 消融实验事件
```

**向后兼容**: 保留旧Event类，EventBus内部做转换

### 任务 1.2: 状态模型定义

**文件**: `packages/contracts/state.py` (新)

定义 `ConsciousState`:
```python
class ConsciousState(BaseModel):
    subject_id: str
    version: int           # 乐观锁版本号
    tick: int              # 认知循环计数
    runtime_mode: RuntimeMode  # 8态
    cognition_phase: CognitionPhase
    
    world: dict            # WorldModel状态
    body: BodyState        # 数字身体状态
    
    active_contents: list[ContentCandidate]
    workspace: list[BroadcastContent]
    
    goals: list[Goal]
    commitments: list[Commitment]
    current_plan: Plan | None
    
    affect: dict           # 效价状态
    confidence: dict       # 全局置信度
    uncertainty: dict      # 不确定性追踪
    
    working_memory: list[str]
    narrative_context: list[str]
    
    health: dict
    integrity_hash: str    # 状态哈希
```

### 任务 1.3: 核心协议定义

**文件**: `packages/contracts/protocols.py` (新)

```python
class CognitiveModule(Protocol):
    name: str
    async def handle(self, event: EventEnvelope, context: RuntimeContext) -> list[EventEnvelope]: ...

class StateRepository(Protocol):
    async def load(self, subject_id: str) -> ConsciousState: ...
    async def commit(self, previous_version: int, new_state: ConsciousState, events: list[EventEnvelope]) -> int: ...

class ToolAdapter(Protocol):
    tool_id: str
    async def validate(self, request: ToolRequest) -> None: ...
    async def execute(self, request: ToolRequest) -> ToolResult: ...

class Verifier(Protocol):
    async def verify(self, intent: Intent, plan: Plan, result: ToolResult) -> VerificationResult: ...
```

---

## 阶段二：Kernel 内核 (Week 3-4)

### 任务 2.1: EventStore 事件存储

**新文件**: `packages/kernel/event_store.py`

要求:
- SQLite + WAL，单调递增sequence
- append-only，不可修改已写入事件
- event_id唯一约束
- 按subject_id隔离
- correlation_id + causation_id索引
- 事务内完成写入
- 支持replay(subject_id, from_sequence)

**与现有代码关系**: 替代/包装EventBus的S5持久化部分

### 任务 2.2: StateManager 状态管理器

**新文件**: `packages/kernel/state_manager.py`

要求:
- 乐观锁版本控制
- 状态快照 + 事件重放 = 权威状态
- 完整性哈希
- 事务提交

### 任务 2.3: Kernel运行时

**新文件**: `packages/kernel/runtime.py`

扩展当前 `app/bootstrap.py` 和 `app/main.py`:

RuntimeMode扩展:
```python
class RuntimeMode(str, Enum):
    BOOTING = "booting"
    ACTIVE = "active"
    REFLECTING = "reflecting"
    CONSOLIDATING = "consolidating"
    DEGRADED = "degraded"
    SUSPENDED = "suspended"
    RECOVERING = "recovering"
    SHUTTING_DOWN = "shutting_down"
```

**与现有代码关系**: 扩展PolicyEngine的StateMachine (4态→8态)

### 任务 2.4: 心跳与自证

**新文件**: `packages/lifecycle/heartbeat.py`

要求:
- 定期心跳写入
- 挑战-响应自证(防止假活)
- 外部控制通道

---

## 阶段三：模型层 (Week 5-6)

### 任务 3.1: BodyModel 数字身体

**新文件**: `packages/models/body_model.py`

```python
class BodyState(BaseModel):
    cpu_load: float
    memory_usage: float
    disk_usage: float
    network_health: float
    token_budget: int
    cost_budget: float
    process_health: float
    error_rate: float
    latency_ms: float
    permission_scope: set[str]
    active_processes: list[str]

class ViabilityBounds(BaseModel):
    max_memory_usage: float
    max_error_rate: float
    max_cost_per_hour: float
    min_disk_free: float
    min_process_health: float
    max_consecutive_failures: int
```

### 任务 3.2: SelfModel 拆分

**改造文件**: `persona/self_model_store.py` → `packages/models/self_model.py`

从单一JSON拆为6个子模型:
- IdentityModel (版本化身份)
- DigitalBodyModel (身体状态)
- BoundaryModel (资源边界)
- AgencyModel (ActionReceipt归属)
- CapabilityModel (能力自知)
- NarrativeModel (叙事自我)

**向后兼容**: 旧self_model.json作为IdentityModel初始数据

### 任务 3.3: ValueModel 价值模型

**新文件**: `packages/models/value_model.py`

独立于ImportanceScorer的价值评估:
- 生存风险 → 注意优先级
- 目标相关性 → 行动优先级
- 不确定性 → 信息收集优先级

---

## 阶段四：认知循环升级 (Week 7-8)

### 任务 4.1: CognitionLoop Pipeline

**改造文件**: `core/cognition_loop.py`

从当前 `_process_one()` 单方法改为分阶段Pipeline:

```python
class CognitionPhase(str, Enum):
    PERCEIVE = "perceive"
    UPDATE_WORLD = "update_world"
    UPDATE_BODY = "update_body"
    GENERATE_CONTENT = "generate_content"
    SELECT_ATTENTION = "select_attention"
    BROADCAST = "broadcast"
    ATTRIBUTE_SELF = "attribute_self"
    EVALUATE = "evaluate"
    DECIDE = "decide"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    CONSOLIDATE = "consolidate"
```

### 任务 4.2: Attention 注意力系统

**新文件**: `packages/cognition/attention.py`

可解释加权函数:
```
Priority(c) = w_s*Salience + w_g*GoalRelevance + w_v*ViabilityRisk 
            + w_n*Novelty + w_u*Uncertainty + w_d*Deadline 
            + w_e*ExpectedImpact - w_i*Inhibition
```

每个候选内容保留各分量得分，确保事后可解释。

### 任务 4.3: Workspace 全局工作空间

**新文件**: `packages/cognition/workspace.py`

广播门控:
```python
broadcast = (
    content.stability >= stability_threshold
    and content.priority >= priority_threshold
    and workspace.has_capacity()
)
```

### 任务 4.4: Metacognition 元认知

**新文件**: `packages/cognition/metacognition.py`

- 置信度评估
- 不确定性追踪
- 能力自知
- 反思循环

### 任务 4.5: DecisionEngine 决策引擎

**新文件**: `packages/cognition/decision.py`

从Planner分离决策逻辑

---

## 阶段五：AgentOS 行动层 (Week 9-10)

### 任务 5.1: IntentParser 意图解析

**新文件**: `packages/agentos/intent_parser.py`

从当前Planner分离意图解析职责

### 任务 5.2: Planner DAG 计划

**改造文件**: `core/planner.py`

支持PlanStep DAG:
```python
class PlanStep(BaseModel):
    step_id: str
    dependencies: list[str]
    agent_type: str
    tool_id: str | None
    input_schema: dict
    expected_output_schema: dict
    preconditions: list[str]
    postconditions: list[str]
    retry_policy: dict
    risk_level: str
```

### 任务 5.3: Verifier 验证器

**新文件**: `packages/agentos/verifier.py`

验证行动结果:
- 事实检查
- 格式检查
- 状态一致性
- 副作用检查

### 任务 5.4: ToolAdapter 工具适配

**新文件**: `packages/tools/mcp_adapter.py`

MCP协议适配 + 现有ToolRegistry统一为ToolAdapter协议

---

## 阶段六：记忆升级 (Week 11)

### 任务 6.1: 六层记忆对齐

当前S1-S5映射到MVSC六层:

| 当前 | MVSC | 改造 |
|------|------|------|
| S1 Session | Working Memory | 保留 |
| S2 Working | Episodic Memory | 改为事件溯源模式 |
| S3 Long-term | Semantic Memory | 保留+增强 |
| - | Procedural Memory | **新建** |
| Persona | Identity Memory | 改为版本化 |
| - | Prospective Memory | **新建** |

### 任务 6.2: 记忆检索评分

**改造文件**: `memory/tiered_store.py`

从纯语义相似度改为多因素:
```
RetrievalScore = Similarity × Confidence × Authority 
               × Freshness × Permission × GoalRelevance
```

### 任务 6.3: 记忆事件溯源

MemoryRecord增加版本控制:
- memory.created
- memory.corrected (不覆盖原记录)
- memory.superseded
- memory.expired

---

## 阶段七：生命周期与驻留 (Week 12)

### 任务 7.1: LifecycleManager

**新文件**: `packages/lifecycle/state_machine.py`

8态状态机 + 模式转换规则

### 任务 7.2: 恢复增强

**改造文件**: `world/snapshot_store.py`

从快照恢复 → 快照+事件重放恢复

### 任务 7.3: 外部控制通道

**新文件**: `apps/admin/` (独立管理API)

独立于主体进程的管理通道

---

## 阶段八：MVSC消融与验证 (Week 13-14)

### 任务 8.1: 特性开关

**新文件**: `configs/experiments.yaml`

```yaml
features:
  recurrent_content: true
  global_workspace: true
  self_model: true
  value_model: true
  episodic_memory: true
  narrative_identity: true
  metacognition: true
```

### 任务 8.2: 消融实验

**新文件**: `packages/mvsc_lab/ablations/`

| 消融 | 操作 | 预期结果 |
|------|------|---------|
| 递归内容损伤 | 禁用内容迭代稳定 | 简单分类保留，持续绑定下降 |
| 广播损伤 | 禁止workspace向规划广播 | 局部处理保留，跨任务调用下降 |
| 自模型损伤 | 禁止行动归属和能力更新 | 任务执行保留，归属错误增加 |
| 价值损伤 | 统一优先级 | 风险目标注意下降 |
| 工作记忆损伤 | 极短保持窗口 | 当前反应保留，延迟任务下降 |
| 叙事损伤 | 禁止跨情景整合 | 单次任务保留，身份连续性下降 |

### 任务 8.3: 关键实验

> 在保持基础输入识别能力相近的条件下，分别关闭递归内容、全局广播和自模型，验证内容稳定、灵活访问和主体归属是否发生选择性分离。

---

## 仓库结构最终态

```text
eva-dcs/
├── apps/
│   ├── api/           # FastAPI (现有app/)
│   ├── runtime/       # 运行时主进程
│   ├── worker/        # 后台worker
│   ├── admin/         # 外部管理通道 (新)
│   └── dashboard/     # Web仪表板 (现有ui/)
│
├── packages/
│   ├── contracts/     # 事件/状态/协议定义 (新)
│   ├── kernel/        # 运行时内核 (新, 吸收部分runtime/)
│   ├── cognition/     # 认知循环+注意+工作空间+元认知 (重构core/)
│   ├── models/        # World/Self/Body/Value模型 (吸收persona/+world)
│   ├── memory/        # 六层记忆 (现有, 增强)
│   ├── agentos/       # AgentOS (吸收agent_os/+新组件)
│   ├── tools/         # 工具适配 (吸收部分core/tool_registry)
│   ├── lifecycle/     # 生命周期管理 (新, 吸收部分runtime)
│   ├── governance/    # 策略/权限/审计 (吸收部分core/policy)
│   ├── observability/ # 日志/追踪/指标 (吸收部分runtime)
│   └── mvsc_lab/      # MVSC研究层 (新)
│
├── configs/           # 配置文件
├── tests/             # 测试(按类型分层)
└── docs/              # 文档
```

---

## 优先级矩阵

| 优先级 | 任务 | 原因 |
|--------|------|------|
| P0 | 事件协议升级 | 所有模块的通信基础 |
| P0 | 状态模型定义 | 统一状态表示 |
| P0 | EventStore | 事件溯源基础 |
| P1 | BodyModel | 数字身体概念 |
| P1 | SelfModel拆分 | 6子模型 |
| P1 | Attention | 可解释注意力 |
| P1 | Workspace | 全局工作空间 |
| P2 | Metacognition | 元认知 |
| P2 | Verifier | 行动验证 |
| P2 | IntentParser | 意图解析 |
| P2 | Planner DAG | 计划DAG |
| P3 | NarrativeModel | 叙事自我 |
| P3 | Ablation Framework | 消融实验 |
| P3 | 外部控制通道 | 管理通道 |
