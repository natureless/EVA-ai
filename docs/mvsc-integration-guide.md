# EVA-MVSC 适配器层 — 新旧组件对接指南

本文档精确描述新MVSC组件如何与现有EVA代码对接。

## 一、事件层对接

### 现有 → 新

```
现有: Event (event/event_schema.py)
  id, type, source, timestamp, payload, correlation_id, status

新: EventEnvelope (packages/contracts/events.py)
  event_id, event_type, source, timestamp, correlation_id,
  causation_id, subject_id, session_id, sequence,
  payload, confidence, priority, sensitivity, schema_version
```

**转换函数**: `from_legacy_event(event, subject_id)` → `EventEnvelope`

**对接点**: EventBus.publish() 入口处。现有代码继续 emit Legacy Event，
在进入MVSC认知循环前转换。

**事件类型映射**:
| 旧类型 | 新类型 |
|--------|--------|
| user_message | perception.user_message_received |
| maintenance | lifecycle.maintenance_started |
| reminder_trigger | perception.system_event |
| system_tick | perception.scheduler_tick |

### EventBus 对接

```
现有 EventBus (event/event_bus.py):
  - 线程安全 queue.Queue
  - publish(LegacyEvent) → bool
  - consume(timeout) → LegacyEvent | None

新 EventBusProtocol (packages/contracts/protocols.py):
  - async publish(EventEnvelope) → bool
  - async consume(timeout) → EventEnvelope | None
  - subscribe(event_type, handler)
```

**对接方案**: 在 EventBus 外层包一个适配器:
```python
class EventBusAdapter:
    def __init__(self, legacy_bus: EventBus):
        self._bus = legacy_bus
    
    async def publish(self, event: EventEnvelope) -> bool:
        # EventEnvelope → LegacyEvent (如果需要向下兼容)
        # 或直接写入 EventStore
        return True
    
    async def consume(self, timeout: float = 0.5) -> EventEnvelope | None:
        legacy = self._bus.consume(timeout=timeout)
        if legacy is None:
            return None
        return from_legacy_event(legacy)
```

---

## 二、状态层对接

### 现有 → 新

```
现有: system_state (dict, app/bootstrap.py)
  focus, mode, active_tasks, pending_events, last_reply, ...

新: ConsciousState (packages/contracts/state.py)
  subject_id, version, tick, runtime_mode, cognition_phase,
  world, body, self_model, active_contents, workspace,
  goals, commitments, current_plan, affect, confidence,
  uncertainty, working_memory, narrative_context,
  relations, health, integrity_hash, feature_flags
```

**对接方案**: system_state dict 作为 ConsciousState 的向后兼容视图:
```python
def system_state_to_conscious_state(ss: dict) -> ConsciousState:
    return ConsciousState(
        world={
            "focus": ss.get("focus", "idle"),
            "mode": ss.get("mode", "active"),
            "active_tasks": ss.get("active_tasks", []),
        },
        health={
            "ready": ss.get("ready", False),
            "db_ready": ss.get("db_ready", False),
            "loop_ready": ss.get("loop_ready", False),
        },
    )

def conscious_state_to_system_state(cs: ConsciousState) -> dict:
    return {
        "focus": cs.world.get("focus", "idle"),
        "mode": cs.world.get("mode", "active"),
        "active_tasks": cs.world.get("active_tasks", []),
        "pending_events": cs.health.get("pending_events", 0),
        "last_reply": cs.world.get("last_reply", ""),
        "stability_score": cs.self_model.get("stability_metrics", {}).get("stability_score", 1.0),
        "mean_prediction_error": cs.self_model.get("stability_metrics", {}).get("mean_prediction_error", 0.0),
    }
```

---

## 三、认知循环对接

### 现有 → 新

```
现有: CognitionLoop (core/cognition_loop.py)
  - _run_forever() 单线程循环
  - _process_one() 单方法处理全部逻辑
  - 直接操作 system_state dict

新: PipelineCognitionLoop (packages/cognition/loop.py)
  - run_once(event) 13阶段pipeline
  - 阶段通过可重写方法实现
  - 通过 ConsciousState 管理状态
```

**对接方案**: 通过子类化实现适配:

```python
class AdaptedCognitionLoop(PipelineCognitionLoop):
    """将 PipelineCognitionLoop 适配到现有 EVA 后端。"""
    
    def __init__(self, container: AppContainer, **kwargs):
        super().__init__(**kwargs)
        self._container = container
    
    async def _update_world(self, state, event):
        wm = self._container.world_model
        if event.event_type == EventFamily.PERCEPTION.USER_MESSAGE:
            text = event.payload.get("text", "")
            wm.apply_user_message(text)
            return {"focus": wm.focus, "mode": wm.mode}
        return {}
    
    async def _update_body(self, state, event):
        return {
            "error_rate": self._container.loop._consecutive_errors / 100.0,
            "consecutive_failures": self._container.loop._consecutive_errors,
        }
    
    async def _execute_action(self, state, decision, plan):
        # 使用现有 AgentOS 执行
        ...
    
    async def _consolidate_memory(self, state, event, broadcast, evaluation, action_events):
        # 使用现有 TieredMemoryManager
        if self._container.tiered_memory:
            self._container.tiered_memory.ingest(...)
        return []
```

**Feature Flag 切换**:
```python
# 在 bootstrap_system() 中:
if settings.enable_mvsc_pipeline:
    loop = AdaptedCognitionLoop(container=container, ...)
else:
    loop = CognitionLoop(...)  # 现有实现
```

---

## 四、自我模型对接

### 现有 → 新

```
现有: SelfModelStore (persona/self_model_store.py)
  - 单JSON文件 (data/self_model.json)
  - identity, capabilities, constraints, state_history, perturbations, prediction_errors, stability_metrics

新: SelfModel (packages/models/self_model.py)
  - 6子模型: Identity, DigitalBody, Boundary, Agency, Capability, Narrative
  - 每个子模型独立版本化
```

**对接方案**:
```python
def migrate_self_model(legacy_path: Path) -> SelfModel:
    """将旧 self_model.json 迁移为新的 SelfModel 6子模型。"""
    legacy = SelfModelStore(legacy_path).load_or_init()
    
    sm = SelfModel.from_legacy(legacy)
    
    # 迁移 capabilities 列表 → CapabilityModel
    for cap_name in legacy.get("capabilities", []):
        sm.capability.register_capability(cap_name, cap_name, f"Legacy capability: {cap_name}")
    
    # 迁移 constraints → BoundaryModel
    for constraint in legacy.get("constraints", []):
        sm.boundary.permission_scope.add(constraint)
    
    # 迁移 stability_metrics
    sm.stability_metrics = legacy.get("stability_metrics", {})
    
    return sm
```

---

## 五、记忆层对接

### 现有 → 新

```
现有: TieredMemoryManager (memory/tiered_store.py)
  - S1 Session, S2 Working, S3 Long-term, S4 World, S5 Event/Trace
  - ingest(), recall(), hybrid_search()

新: MemoryProtocol (packages/contracts/protocols.py)
  - consolidate(state, event, broadcast, evaluation, action_events) → events
  - retrieve(query, context) → results
```

**对接方案**: TieredMemoryManager 已经包含所需的 ingest/recall 功能，
只需在外层实现 MemoryProtocol:

```python
class TieredMemoryAdapter:
    """将 TieredMemoryManager 适配为 MemoryProtocol。"""
    
    def __init__(self, tiered_memory: TieredMemoryManager):
        self._tm = tiered_memory
    
    async def consolidate(self, state, source_event, broadcast, evaluation, action_events):
        events = []
        for bc in broadcast:
            self._tm.ingest(
                bc.content.summary,
                importance=bc.content.priority,
                source=bc.content.source_module,
                category=bc.content.content_type,
            )
            events.append(EventEnvelope(
                event_type=EventFamily.MEMORY.EPISODE_COMMITTED,
                source="memory",
                causation_id=source_event.event_id,
                payload={"content_id": bc.content.content_id},
            ))
        return events
    
    async def retrieve(self, query, context):
        return self._tm.recall(query, tiers=[1, 2, 3])
```

---

## 六、PolicyEngine → RuntimeMode 对接

```
现有 PolicyEngine 4态:
  Dormant → Commanded → Supervised → Quarantined

新 RuntimeMode 8态:
  BOOTING → ACTIVE → REFLECTING → CONSOLIDATING
  → DEGRADED → SUSPENDED → RECOVERING → SHUTTING_DOWN
```

**映射**:
| 旧态 | 新态 |
|------|------|
| Dormant | ACTIVE (空闲) |
| Commanded | ACTIVE (处理命令中) |
| Supervised | ACTIVE (受监督) |
| Quarantined | DEGRADED |

**新态触发**:
| 触发条件 | 新态 |
|----------|------|
| 系统启动 | BOOTING |
| 每100 tick无广播 | REFLECTING |
| 记忆维护周期 | CONSOLIDATING |
| 连续错误超阈值 | DEGRADED |
| 用户暂停 | SUSPENDED |
| 从快照恢复 | RECOVERING |
| 系统关闭 | SHUTTING_DOWN |

---

## 七、执行器对接

```
现有 Executors (core/executor.py):
  FileExecutor, CodeExecutor, BrowserExecutor, APIExecutor, CommsExecutor
  - execute(action, params, token_id) → dict

新 ToolAdapterProtocol (packages/contracts/protocols.py):
  - validate(ToolRequest) → None
  - execute(ToolRequest) → ToolResult
```

**对接方案**:
```python
class ExecutorToolAdapter:
    """将现有 Executor 适配为 ToolAdapterProtocol。"""
    
    tool_id: str
    
    def __init__(self, tool_id: str, executor):
        self.tool_id = tool_id
        self._executor = executor
    
    async def validate(self, request: ToolRequest) -> None:
        # 现有 executor 内部已有 token 验证和边界检查
        pass
    
    async def execute(self, request: ToolRequest) -> ToolResult:
        result = self._executor.execute(
            action=request.parameters.get("action", ""),
            params=request.parameters.get("params", {}),
            token_id=request.token_id,
        )
        return ToolResult(
            ok=result.get("ok", False),
            data=result,
            error=result.get("error"),
            duration_ms=result.get("duration_ms", 0),
        )
```

---

## 八、对接优先级

| 优先级 | 对接项 | 理由 |
|--------|--------|------|
| P0 | EventBusAdapter | 事件是通信基础 |
| P0 | system_state ↔ ConsciousState | 状态是数据基础 |
| P1 | AdaptedCognitionLoop | 新pipeline需要接入现有后端 |
| P1 | migrate_self_model | 自我模型升级 |
| P2 | TieredMemoryAdapter | 记忆协议对接 |
| P2 | PolicyEngine → RuntimeMode | 状态机升级 |
| P3 | ExecutorToolAdapter | 执行器协议对接 |
