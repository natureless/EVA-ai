# EVA-MVSC 融合开发计划 v1.0

## 总体策略

**原则**: 渐进式对接，不推翻现有代码，每次改动后保持 747 测试通过。

**当前状态**:
- 新框架: 2,844 行 (packages/) — 契约、模型、EventStore、认知循环、消融
- 现有代码: 22,447 行 — 完全正常运行
- 适配器指南: docs/mvsc-adapter-guide.md — 8层对接方案
- 验证套件: tests/verify_mvsc_framework.py — 16项全部通过

**目标**: 将新框架的 8 层对接逐层实现，使新老代码通过适配器协同工作。

---

## 阶段一: 事件层 + 状态层对接 (当前)

**目标**: EventBus 能接收 EventEnvelope，system_state 能与 ConsciousState 互转。

### 1.1 EventBusAdapter — 事件总线适配
- 文件: `packages/kernel/event_bus_adapter.py` (新)
- 包装现有 EventBus，提供 async publish/consume 接口
- 自动完成 LegacyEvent ↔ EventEnvelope 转换
- **不改动** event/event_bus.py

### 1.2 StateBridge — 状态桥接
- 文件: `packages/kernel/state_bridge.py` (新)
- system_state dict ↔ ConsciousState 双向转换
- 启动时将现有 system_state 提升为 ConsciousState
- 每次循环后将 ConsciousState 同步回 system_state

### 1.3 集成测试
- 验证 EventBusAdapter 能正确转换事件
- 验证 StateBridge 不丢失字段

---

## 阶段二: 认知循环对接

**目标**: PipelineCognitionLoop 能使用现有 WorldModel、Memory、AgentOS。

### 2.1 AdaptedCognitionLoop
- 文件: `packages/cognition/adapted_loop.py` (新)
- 继承 PipelineCognitionLoop
- 重写 _update_world, _update_body, _execute_action, _consolidate_memory
- 接入现有 WorldModelGraph, TieredMemoryManager, AgentOS

### 2.2 Bootstrap 集成
- 在 app/bootstrap.py 中添加 feature flag
- EVA_ENABLE_MVSC_PIPELINE=true 时启用新循环

---

## 阶段三: 自我模型对接

**目标**: SelfModelStore → SelfModel 6子模型迁移。

### 3.1 SelfModelMigrator
- 文件: `packages/models/migrator.py` (新)
- 读取 data/self_model.json → SelfModel
- 保留旧文件，生成新格式

---

## 阶段四: 记忆层对接

**目标**: TieredMemoryManager 适配 MemoryProtocol。

### 4.1 TieredMemoryAdapter
- 文件: `packages/memory/memory_adapter.py` (新)
- 实现 consolidate() 和 retrieve()

---

## 阶段五: 执行器对接

**目标**: 现有 Executor 适配 ToolAdapterProtocol。

---

## 阶段六: 消融实验集成

**目标**: 将 feature_flags 连接到现有系统。

---

## 阶段七: 端到端验证

**目标**: 完整运行链路测试。
